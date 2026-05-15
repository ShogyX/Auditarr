"""Stage 5 (audit follow-up) — Global path mappings + suggestions.

Pins:
  - ``GET /api/v1/system/path-mappings/global`` lists rows ordered
    by priority asc / created_at asc.
  - ``POST`` creates a row (admin-only).
  - ``PATCH`` updates a row.
  - ``DELETE`` removes a row.
  - ``GET /api/v1/system/path-suggestions`` returns the union of
    library roots, integration mapping paths, and global mapping
    paths.
  - The ``remap_path_chain`` helper applies per-integration mappings
    first, then global ones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.integrations.path_mapping import (
    PathMapping,
    remap_path_chain,
)
from app.main import create_app
from app.models.integration import Integration
from app.models.library import Library
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "global_pmaps.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
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
        get_settings.cache_clear()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = r.json()
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


# ── remap_path_chain (unit-style on the helper) ──────────────────
def test_chain_applies_integration_then_global() -> None:
    ig = [PathMapping(src_prefix="/data/movies", dst_prefix="/scratch/movies")]
    gl = [PathMapping(src_prefix="/scratch", dst_prefix="/mnt/storage")]
    out = remap_path_chain("/data/movies/Dune.mkv", ig, gl)
    assert out == "/mnt/storage/movies/Dune.mkv"


def test_chain_with_empty_lists_returns_path_unchanged() -> None:
    assert remap_path_chain("/data/x.mkv", [], []) == "/data/x.mkv"


def test_chain_only_global_when_integration_does_not_match() -> None:
    """Per-integration mapping doesn't match → global still applies."""
    ig = [PathMapping(src_prefix="/elsewhere", dst_prefix="/nope")]
    gl = [PathMapping(src_prefix="/data", dst_prefix="/mnt/storage")]
    out = remap_path_chain("/data/x.mkv", ig, gl)
    assert out == "/mnt/storage/x.mkv"


# ── Global mapping CRUD ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_global_mapping_returns_201(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/data/media", "to_path": "/mnt/storage"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["from_path"] == "/data/media"
    assert body["to_path"] == "/mnt/storage"
    assert body["enabled"] is True
    assert body["priority"] == 0
    assert "id" in body


@pytest.mark.asyncio
async def test_list_global_mappings_ordered_by_priority(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    # Insert in non-priority order; the list endpoint must reorder
    # so the resolver applies low-priority first.
    await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/a", "to_path": "/A", "priority": 10},
    )
    await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/b", "to_path": "/B", "priority": 0},
    )
    await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/c", "to_path": "/C", "priority": 5},
    )
    r = await client.get(
        "/api/v1/system/path-mappings/global", headers=headers
    )
    assert r.status_code == 200
    rows = r.json()
    assert [row["priority"] for row in rows] == [0, 5, 10]


@pytest.mark.asyncio
async def test_patch_global_mapping_updates_fields(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/a", "to_path": "/A"},
    )
    mid = create.json()["id"]
    patch = await client.patch(
        f"/api/v1/system/path-mappings/global/{mid}",
        headers=headers,
        json={"enabled": False, "priority": 50},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["enabled"] is False
    assert body["priority"] == 50
    # Original from/to preserved (we only patched two fields).
    assert body["from_path"] == "/a"
    assert body["to_path"] == "/A"


@pytest.mark.asyncio
async def test_delete_global_mapping(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/a", "to_path": "/A"},
    )
    mid = create.json()["id"]
    d = await client.delete(
        f"/api/v1/system/path-mappings/global/{mid}", headers=headers
    )
    assert d.status_code == 204, d.text
    listing = await client.get(
        "/api/v1/system/path-mappings/global", headers=headers
    )
    assert listing.json() == []


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    r = await client.delete(
        "/api/v1/system/path-mappings/global/does-not-exist",
        headers=headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_non_admin(client: AsyncClient) -> None:
    # Register a non-admin user and try to POST.
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/a", "to_path": "/A"},
    )
    # AdminUser dep raises 403.
    assert r.status_code in (401, 403)


# ── path-suggestions ─────────────────────────────────────────────
async def _seed_for_suggestions() -> None:
    async with get_database().session() as sess:
        sess.add_all(
            [
                Library(
                    id="lib-1",
                    name="Movies",
                    root_path="/mnt/storage/Movies",
                    kind="movies",
                    enabled=True,
                ),
                Library(
                    id="lib-2",
                    name="TV",
                    root_path="/mnt/storage/TV",
                    kind="tv",
                    enabled=True,
                ),
                Integration(
                    id="ig-plex",
                    name="Plex",
                    kind="plex",
                    enabled=True,
                    config={
                        "path_mappings": [
                            {"from": "/data/movies",
                             "to": "/mnt/storage/Movies"},
                            {"from": "/data/tv",
                             "to": "/mnt/storage/TV"},
                        ]
                    },
                ),
            ]
        )
        await sess.commit()


@pytest.mark.asyncio
async def test_path_suggestions_unions_known_roots(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await _seed_for_suggestions()

    # Add one global mapping too.
    await client.post(
        "/api/v1/system/path-mappings/global",
        headers=headers,
        json={"from_path": "/old/path", "to_path": "/new/path"},
    )

    r = await client.get(
        "/api/v1/system/path-suggestions", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Library roots present + sorted.
    assert body["library_roots"] == [
        "/mnt/storage/Movies",
        "/mnt/storage/TV",
    ]
    # Integration mappings exposed verbatim (no parser; raw paths so
    # operators can see malformed entries).
    pairs = {(p["from"], p["to"]) for p in body["integration_paths"]}
    assert ("/data/movies", "/mnt/storage/Movies") in pairs
    assert ("/data/tv", "/mnt/storage/TV") in pairs
    # Global paths: union of from and to, deduped + sorted.
    assert body["global_paths"] == ["/new/path", "/old/path"]


@pytest.mark.asyncio
async def test_path_suggestions_empty_install(
    client: AsyncClient,
) -> None:
    """Fresh install: no libraries, no integrations, no mappings.
    The endpoint must return the well-known shape with empty arrays
    (NOT 404, NOT 500)."""
    headers = await _admin_headers(client)
    r = await client.get(
        "/api/v1/system/path-suggestions", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "library_roots": [],
        "integration_paths": [],
        "global_paths": [],
    }
