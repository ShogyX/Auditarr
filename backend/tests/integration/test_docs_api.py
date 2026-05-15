"""Documentation API integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_docs(root: Path) -> None:
    _write(
        root / "overview.md",
        """---
title: Overview
category: guide
tags: [intro]
help_context: [dashboard.overview]
---

# Overview

Auditarr audits media libraries. Codec checks, subtitle detection.
""",
    )
    _write(
        root / "rules" / "conditions.md",
        """---
id: rules/conditions
title: Conditions
category: rules
tags: [rules]
help_context: [rules.conditions]
---

# Conditions

Define which files match. Use the `eq`, `gt`, `in` operators.
""",
    )


@pytest_asyncio.fixture
async def docs_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    monkeypatch.setenv("AUDITARR_DOCS_DIR", str(docs_root))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
    )

    from app.core.settings import get_settings
    from app.documentation import (
        get_documentation_service,
        reset_documentation_service,
    )

    get_settings.cache_clear()
    reset_documentation_service()
    # Ensure the service is initialized before the app starts; the lifespan
    # runs ``load`` again, but this guarantees the index is populated even
    # without entering lifespan (httpx ASGITransport doesn't by default).
    get_documentation_service().load()

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    reset_documentation_service()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_pages(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs")
    assert response.status_code == 200
    body = response.json()
    ids = sorted(p["id"] for p in body)
    assert ids == ["overview", "rules/conditions"]


@pytest.mark.asyncio
async def test_filter_by_category(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs?category=rules")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == ["rules/conditions"]


@pytest.mark.asyncio
async def test_get_page(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/rules/conditions")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Conditions"
    assert "<h1>Conditions</h1>" in body["body_html"]
    assert body["help_contexts"] == ["rules.conditions"]


@pytest.mark.asyncio
async def test_get_page_404(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_search(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/search?q=codec")
    assert response.status_code == 200
    body = response.json()
    assert body
    assert body[0]["page_id"] == "overview"
    assert "score" in body[0]
    assert "excerpt" in body[0]


@pytest.mark.asyncio
async def test_search_empty_query(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/search?q=")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_help_context_lookup(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/help/rules.conditions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "rules/conditions"


@pytest.mark.asyncio
async def test_help_context_unknown_returns_empty(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/help/nope.unknown")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_categories(docs_client: AsyncClient) -> None:
    response = await docs_client.get("/api/v1/docs/categories")
    assert response.status_code == 200
    body = response.json()
    assert "guide" in body
    assert "rules" in body
