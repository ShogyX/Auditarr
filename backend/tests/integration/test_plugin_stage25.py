"""Stage 25 — Plugin reload endpoint + enriched list response.

The loader's behavior is exercised in ``test_plugin_loader_stage25.py``.
This file pins the API contract:

  - ``GET /api/v1/plugins`` returns the enriched dict shape
    (``description``, ``author``, ``status``, ``last_error``,
    ``has_settings``).
  - ``POST /api/v1/plugins/{id}/reload`` is admin-only, returns the
    new summary on success, 404s on unknown plugin.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.registry import get_registry
from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.plugins.loader import get_plugin_loader
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


def _write_plugin(
    root: Path,
    plugin_id: str,
    *,
    description: str = "",
    author: str = "",
    body: str | None = None,
) -> Path:
    pdir = root / plugin_id
    pdir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.2.0",
        "type": "generic",
        "description": description,
        "author": author,
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": False,
        "permissions": [],
        "capabilities": [],
        "requires": [],
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest))
    (pdir / "backend.py").write_text(
        body
        or (
            "from app.plugins import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    pass\n"
            "def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        )
    )
    return pdir


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "plugins.db"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_PLUGIN_DIR", str(plugin_dir))

    get_settings.cache_clear()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()
    registry = get_registry()
    registry.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Pre-seed plugins on disk before app creation; the loader reads
    # plugin_dir at discover_and_load time.
    _write_plugin(plugin_dir, "alpha", description="Alpha plugin", author="ACME")

    # Reset the loader so it picks up the fresh plugin_dir.
    loader = get_plugin_loader()
    loader._plugins.clear()  # noqa: SLF001
    loader._failed_loads.clear()  # noqa: SLF001
    # The loader needs to read the new settings — it was instantiated
    # with the old ones at module load. Replace its settings ref.
    loader._settings = get_settings()  # noqa: SLF001
    await loader.discover_and_load()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            c._plugin_dir = plugin_dir  # type: ignore[attr-defined]
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        registry.clear()
        loader._plugins.clear()  # noqa: SLF001
        loader._failed_loads.clear()  # noqa: SLF001
        get_settings.cache_clear()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "a@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = response.json()
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _non_admin_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "u@example.com",
            "username": "user1",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user1", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


# ── Enriched list response ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_plugins_includes_enriched_fields(client: AsyncClient) -> None:
    """Auth required, then the response carries the Stage 25 fields."""
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/plugins", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(p["id"] == "alpha" for p in body)
    alpha = next(p for p in body if p["id"] == "alpha")
    for key in ("description", "author", "status", "last_error", "has_settings"):
        assert key in alpha, f"missing {key} in enriched summary"
    assert alpha["description"] == "Alpha plugin"
    assert alpha["author"] == "ACME"
    assert alpha["status"] == "loaded"


# ── Reload endpoint ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_returns_summary_for_loaded_plugin(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/plugins/alpha/reload", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "alpha"
    assert body["status"] == "loaded"
    assert body["last_error"] is None


@pytest.mark.asyncio
async def test_reload_unknown_plugin_404(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/plugins/does-not-exist/reload", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reload_admin_only(client: AsyncClient) -> None:
    user_headers = await _non_admin_headers(client)
    response = await client.post(
        "/api/v1/plugins/alpha/reload", headers=user_headers
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reload_picks_up_source_changes(client: AsyncClient) -> None:
    """End-to-end: rewrite the backend.py and confirm the reload
    endpoint surfaces a changed plugin without a host restart."""
    headers = await _admin_headers(client)
    plugin_dir: Path = client._plugin_dir  # type: ignore[attr-defined]

    # Rewrite to deliberately fail on import.
    (plugin_dir / "alpha" / "backend.py").write_text("raise RuntimeError('break')\n")

    response = await client.post(
        "/api/v1/plugins/alpha/reload", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed_to_load"
    assert "break" in body["last_error"]
