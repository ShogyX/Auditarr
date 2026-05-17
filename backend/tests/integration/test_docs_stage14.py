"""Stage 14 (plan §638) — the new AI-authoring page is indexed
and findable via the docs search endpoint.

Pins that:
  * The page parses cleanly (frontmatter valid).
  * It surfaces under the rules category.
  * Searching for "AI" / "JSON" / "import" returns it.
  * The upgrade-to-v1.7 doc also parses + indexes.
  * The Plex-compatibility doc parses + indexes.

Like ``test_docs_files_overview_stage04.py``, this test points
the documentation service at the **real** repo ``docs/``
directory so a broken frontmatter on the actual files fails the
test rather than silently shipping.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


REPO_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


@pytest_asyncio.fixture
async def docs_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    assert REPO_DOCS_DIR.is_dir(), (
        f"expected real docs dir at {REPO_DOCS_DIR}; layout drift?"
    )
    monkeypatch.setenv("AUDITARR_DOCS_DIR", str(REPO_DOCS_DIR))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'stage14_docs.db'}",
    )

    from app.core.settings import get_settings
    from app.documentation import (
        get_documentation_service,
        reset_documentation_service,
    )

    get_settings.cache_clear()
    reset_documentation_service()
    get_documentation_service().load()

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c

    reset_documentation_service()
    get_settings.cache_clear()


# ── Test 1 — AI-authoring page is listed under the rules category


@pytest.mark.asyncio
async def test_ai_authoring_page_indexed_under_rules(
    docs_client: AsyncClient,
) -> None:
    resp = await docs_client.get("/api/v1/docs?category=rules")
    assert resp.status_code == 200, resp.text
    pages = resp.json()
    ids = [p["id"] for p in pages]
    assert "rules/ai-authoring" in ids, (
        f"expected rules/ai-authoring to appear; got {ids}"
    )


# ── Test 2 — search returns the AI-authoring page on relevant queries


@pytest.mark.asyncio
async def test_ai_authoring_findable_via_search(
    docs_client: AsyncClient,
) -> None:
    # Search for a term unique to the AI-authoring page.
    resp = await docs_client.get(
        "/api/v1/docs/search", params={"q": "AI assistant", "limit": 20}
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()
    ids = [h["page_id"] for h in hits]
    assert "rules/ai-authoring" in ids, (
        f"expected rules/ai-authoring in search results for "
        f"'AI assistant'; got {ids}"
    )

    # Mass-import is a distinctive term too.
    resp = await docs_client.get(
        "/api/v1/docs/search", params={"q": "mass-importing", "limit": 20}
    )
    assert resp.status_code == 200
    hits2 = resp.json()
    assert any(h["page_id"] == "rules/ai-authoring" for h in hits2)


# ── Test 3 — the upgrade-to-v1.7 doc parses + indexes


@pytest.mark.asyncio
async def test_upgrade_doc_indexed_under_getting_started(
    docs_client: AsyncClient,
) -> None:
    resp = await docs_client.get(
        "/api/v1/docs?category=getting-started"
    )
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert "getting-started/upgrade-to-v1.7" in ids, (
        f"expected upgrade doc to appear; got {ids}"
    )


@pytest.mark.asyncio
async def test_upgrade_doc_findable_via_search(
    docs_client: AsyncClient,
) -> None:
    resp = await docs_client.get(
        "/api/v1/docs/search", params={"q": "quarantine removal", "limit": 20}
    )
    assert resp.status_code == 200
    hits = resp.json()
    ids = [h["page_id"] for h in hits]
    assert "getting-started/upgrade-to-v1.7" in ids, (
        f"upgrade doc not findable via search; got {ids}"
    )


# ── Test 4 — the Plex-compatibility doc parses + indexes


@pytest.mark.asyncio
async def test_plex_compatibility_doc_indexed_under_rules(
    docs_client: AsyncClient,
) -> None:
    resp = await docs_client.get("/api/v1/docs?category=rules")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert "rules/plex-compatibility" in ids, (
        f"expected plex-compatibility doc to appear; got {ids}"
    )


@pytest.mark.asyncio
async def test_plex_compatibility_doc_findable_via_search(
    docs_client: AsyncClient,
) -> None:
    resp = await docs_client.get(
        "/api/v1/docs/search",
        params={"q": "direct-play compatibility", "limit": 20},
    )
    assert resp.status_code == 200
    hits = resp.json()
    ids = [h["page_id"] for h in hits]
    assert "rules/plex-compatibility" in ids, (
        f"plex-compatibility doc not found via search; got {ids}"
    )


# ── Test 5 — file/overview page still works (no Stage 14 regression)


@pytest.mark.asyncio
async def test_files_overview_still_indexed_after_stage14(
    docs_client: AsyncClient,
) -> None:
    """Stage 14 edited docs/files/overview.md (dropped the
    quarantine-view dropdown mention). Make sure the page
    still parses and surfaces under its help context."""
    resp = await docs_client.get("/api/v1/docs/help/files.overview")
    assert resp.status_code == 200
    pages = resp.json()
    assert any("Files page" in p["title"] for p in pages), (
        f"files.overview help context broken after Stage 14 edit; "
        f"got titles={[p['title'] for p in pages]}"
    )
