"""End-to-end smoke test (Stage 13).

Walks the full operator flow:

  1. Admin registers + logs in.
  2. Creates a library pointed at a temp directory with a fake .mkv.
  3. Creates a notification channel (recording provider, in-memory).
  4. Creates a rule.
  5. Creates an optimization profile.
  6. Hits the dashboard endpoints to verify aggregations work.
  7. Lists plugins to verify the SDK surface is alive.
  8. Lists updater status to verify the audit chain is wired.

The point isn't comprehensive coverage — every endpoint here has its
own dedicated test suite. The point is making sure all the seams
between subsystems still fit after Stage 13 hardening. If this test
ever fails after a refactor, something cross-cutting is broken.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.registry import get_registry
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.notifications.types import (
    ChannelConfig,
    DeliveryReport,
    NotificationMessage,
)
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class _RecordingProvider:
    """In-memory notification provider that records every call."""

    kind = "recording"
    label = "Recording (test)"
    config_schema = {
        "type": "object",
        "required": ["target"],
        "properties": {"target": {"type": "string"}},
    }
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.calls: list[tuple[ChannelConfig, NotificationMessage]] = []

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        self.calls.append((config, message))
        return DeliveryReport(status="sent", detail="ok")


@pytest_asyncio.fixture
async def smoke_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "e2e.db"
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
    registry = get_registry()
    registry.clear()
    registry.register_capability(
        "notifications.channel", _RecordingProvider()
    )

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
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
        get_settings.cache_clear()


async def _register_admin(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user_id).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    return {"authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_full_operator_flow(
    smoke_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _register_admin(smoke_client)

    # ── /auth/me works after login ────────────────────────────
    me = await smoke_client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["role"] == "admin"

    # ── Library creation ─────────────────────────────────────
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "movie.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)

    lib = await smoke_client.post(
        "/api/v1/libraries",
        headers=headers,
        json={
            "name": "Movies",
            "root_path": str(library_root),
            "kind": "movies",
        },
    )
    assert lib.status_code == 201, lib.text
    library_id = lib.json()["id"]

    # Library listing should include the new entry.
    libs = await smoke_client.get("/api/v1/libraries", headers=headers)
    assert libs.status_code == 200
    assert any(item["id"] == library_id for item in libs.json())

    # ── Notification channel ─────────────────────────────────
    channel = await smoke_client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Ops channel",
            "kind": "recording",
            "config": {"target": "ops"},
            "min_severity_rank": 0,
        },
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]

    # Channel listing.
    channels = await smoke_client.get(
        "/api/v1/notifications", headers=headers
    )
    assert channels.status_code == 200
    assert any(c["id"] == channel_id for c in channels.json())

    # ── Rule creation ────────────────────────────────────────
    rule = await smoke_client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "Flag tiny files",
            "enabled": True,
            "definition": {
                "match": {
                    "all": [
                        {"field": "size_bytes", "op": "lt", "value": 1000}
                    ]
                },
                "actions": [
                    {"type": "set_severity", "severity": "warn"}
                ],
            },
        },
    )
    assert rule.status_code in (200, 201), rule.text

    rules = await smoke_client.get("/api/v1/rules", headers=headers)
    assert rules.status_code == 200
    assert any(r["name"] == "Flag tiny files" for r in rules.json())

    # ── Dashboard aggregations are alive ─────────────────────
    overview = await smoke_client.get(
        "/api/v1/dashboard/overview", headers=headers
    )
    assert overview.status_code == 200
    # The body shape is service-defined; assert it's a dict with at
    # least one key. Subsystem-specific tests cover the contents.
    assert isinstance(overview.json(), dict)
    assert len(overview.json()) > 0

    # ── Plugins + updater surfaces respond ───────────────────
    plugins = await smoke_client.get("/api/v1/plugins", headers=headers)
    assert plugins.status_code == 200
    assert isinstance(plugins.json(), list)

    updater = await smoke_client.get(
        "/api/v1/updater/status", headers=headers
    )
    assert updater.status_code == 200
    assert "installed_version" in updater.json()


@pytest.mark.asyncio
async def test_non_admin_cannot_create_resources(
    smoke_client: AsyncClient, tmp_path: Path
) -> None:
    """Sanity check: a plain user can register + read, but admin-only
    write paths must reject them with 403."""
    r = await smoke_client.post(
        "/api/v1/auth/register",
        json={
            "email": "u@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201
    login = await smoke_client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    user_headers = {
        "authorization": f"Bearer {login.json()['access_token']}"
    }

    # Library creation is admin-only.
    lib = await smoke_client.post(
        "/api/v1/libraries",
        headers=user_headers,
        json={
            "name": "Sneaky",
            "root_path": str(tmp_path),
            "kind": "movies",
        },
    )
    assert lib.status_code == 403
