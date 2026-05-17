"""Stage 04 — files.overview help context resolves to a real doc page.

Plan §270: "extend test_docs_api.py to assert
``/api/v1/docs/help/files.overview`` returns ≥1 page."

Rather than seeding a fake docs tree like ``test_docs_api.py``,
this test points the documentation service at the **real** repo
``docs/`` directory and asserts that the Stage 04 file
``docs/files/overview.md`` is indexed under the
``files.overview`` help context. That makes the test a true
integration check: a broken frontmatter on the real file would
fail this test, not silently land.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# The test points AUDITARR_DOCS_DIR at the repo's actual docs/
# directory. ``__file__`` resolves to
# ``backend/tests/integration/test_docs_files_overview_stage04.py``
# so the docs root is three parents up + "docs".
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
        f"sqlite+aiosqlite:///{tmp_path / 'stage04_docs.db'}",
    )

    from app.core.settings import get_settings
    from app.documentation import (
        get_documentation_service,
        reset_documentation_service,
    )

    get_settings.cache_clear()
    reset_documentation_service()
    # Initialize the index before the app starts — mirrors the
    # pattern used by ``test_docs_api.py``'s fixture.
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


@pytest.mark.asyncio
async def test_files_overview_help_context_resolves(
    docs_client: AsyncClient,
) -> None:
    """The Stage 04 Files-page help key lands somewhere."""
    resp = await docs_client.get("/api/v1/docs/help/files.overview")
    assert resp.status_code == 200, resp.text
    pages = resp.json()
    assert isinstance(pages, list)
    assert len(pages) >= 1, (
        "expected at least one page under help_context 'files.overview'"
    )
    # And the one we added carries that title.
    titles = [p["title"] for p in pages]
    assert any("Files page" in t for t in titles), (
        f"expected a 'Files page' doc; got titles={titles}"
    )


@pytest.mark.asyncio
async def test_files_overview_page_is_listed(
    docs_client: AsyncClient,
) -> None:
    """The page also surfaces under the regular listing — it
    isn't a help-context-only ghost."""
    resp = await docs_client.get("/api/v1/docs?category=files")
    assert resp.status_code == 200, resp.text
    pages = resp.json()
    ids = [p["id"] for p in pages]
    assert "files/overview" in ids, (
        f"expected files/overview in category=files listing; got {ids}"
    )
