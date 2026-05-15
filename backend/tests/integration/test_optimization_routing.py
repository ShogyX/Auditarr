"""Stage 7 (audit follow-up) — optimization_integration_id routing column.

Pins the round-trip behaviour of the new column added in
``0018_profile_integration_routing``:

  - POST /optimization/profiles with no ``optimization_integration_id``
    persists NULL (pre-Stage-7 contract preserved).
  - POST with a value persists and returns it.
  - PATCH can set the field on an existing profile.
  - PATCH can clear the field by sending null.

The worker doesn't dispatch differently at Stage 7 (the routing
wiring lands with the first integration that supports it). These
tests pin the storage shape so that wiring has a stable column to
read from when the time comes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "opt_routing.db"
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


@pytest.mark.asyncio
async def test_create_profile_without_routing_persists_null(
    client: AsyncClient,
) -> None:
    """Pre-Stage-7 callers don't send the field; the response includes
    it as NULL."""
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Default routing profile",
            "settings": {"video": {"codec": "libx265"}},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["optimization_integration_id"] is None


@pytest.mark.asyncio
async def test_create_profile_with_routing_persists_value(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Tdarr-routed profile",
            "settings": {"video": {"codec": "libx265"}},
            "optimization_integration_id": "ig-tdarr-1",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["optimization_integration_id"] == "ig-tdarr-1"


@pytest.mark.asyncio
async def test_patch_can_set_then_clear_routing(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    created = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Patchable profile",
            "settings": {"video": {"codec": "libx265"}},
        },
    )
    pid = created.json()["id"]

    # Set the routing.
    set_r = await client.patch(
        f"/api/v1/optimization/profiles/{pid}",
        headers=headers,
        json={"optimization_integration_id": "ig-tdarr-1"},
    )
    assert set_r.status_code == 200, set_r.text
    assert set_r.json()["optimization_integration_id"] == "ig-tdarr-1"

    # Clear by sending null.
    clear_r = await client.patch(
        f"/api/v1/optimization/profiles/{pid}",
        headers=headers,
        json={"optimization_integration_id": None},
    )
    assert clear_r.status_code == 200, clear_r.text
    assert clear_r.json()["optimization_integration_id"] is None


@pytest.mark.asyncio
async def test_list_includes_routing_column(client: AsyncClient) -> None:
    """The list endpoint must include the new field on every row so
    the frontend can show the picker pre-populated."""
    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Listed profile",
            "settings": {"video": {"codec": "libx264"}},
            "optimization_integration_id": "ig-2",
        },
    )
    listing = await client.get(
        "/api/v1/optimization/profiles", headers=headers
    )
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["optimization_integration_id"] == "ig-2"
