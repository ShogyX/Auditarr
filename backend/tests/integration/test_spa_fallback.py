"""SPA fallback tests.

Verifies that hard-loads of client-side routes (`/login`, `/files`, …) return
the built ``index.html`` and that real assets are served, while API and
websocket paths fall through to FastAPI's normal routing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def spa_client(tmp_path: Path, monkeypatch) -> AsyncIterator[AsyncClient]:
    """Build an app pointed at a fake SPA dist."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Auditarr</title></head>"
        "<body><div id='root'></div></body></html>"
    )
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "assets" / "app.js").write_text("/* fake bundle */")

    monkeypatch.setenv("AUDITARR_FRONTEND_DIST", str(dist))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
    )

    # Reset cached settings so the new env is picked up.
    from app.core.settings import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_root_returns_spa_index(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert "<title>Auditarr</title>" in response.text


@pytest.mark.asyncio
async def test_client_route_falls_back_to_index(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/login", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert "<title>Auditarr</title>" in response.text


@pytest.mark.asyncio
async def test_real_top_level_file_is_served(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"


@pytest.mark.asyncio
async def test_assets_path_is_served(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/assets/app.js")
    assert response.status_code == 200
    assert "fake bundle" in response.text


@pytest.mark.asyncio
async def test_api_routes_not_intercepted(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/api/v1/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_unknown_api_returns_404_not_index(spa_client: AsyncClient) -> None:
    # Even with browser headers, /api/* must never be SPA-fallbacked.
    response = await spa_client.get(
        "/api/v1/does-not-exist", headers={"accept": "text/html"}
    )
    assert response.status_code == 404
    assert "<title>" not in response.text


@pytest.mark.asyncio
async def test_non_html_request_does_not_get_index(spa_client: AsyncClient) -> None:
    """A JSON or wildcard request that 404s shouldn't be turned into HTML."""
    response = await spa_client.get(
        "/this-does-not-exist", headers={"accept": "application/json"}
    )
    assert response.status_code == 404
    assert "<title>" not in response.text
