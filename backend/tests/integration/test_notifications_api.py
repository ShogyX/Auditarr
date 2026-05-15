"""Notifications API + dispatcher integration tests.

We register a recording in-memory provider so the dispatcher's behaviour
(threshold filtering, audit log, channel ``last_delivery_*`` mirror,
event emission) can be observed deterministically without real network.
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
from app.models.notification_delivery import NotificationDelivery
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


class RecordingProvider:
    """Records each ``send`` call and reports configurable status."""

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
        self.next_report = DeliveryReport(status="sent", detail="ok")

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        self.calls.append((config, message))
        return self.next_report


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "notifications.db"
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

    # Register a recording provider so the test-send + dispatcher paths
    # have something to call without touching the network. We keep a
    # reference so individual tests can inspect or rewrite next_report.
    registry = get_registry()
    registry.clear()
    recorder = RecordingProvider()
    registry.register_capability("notifications.channel", recorder)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            # Stash the recorder so individual tests can flip ``next_report``.
            c._recorder = recorder  # type: ignore[attr-defined]
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


# ── Kinds directory ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_kinds_includes_builtins_and_plugin(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/notifications/kinds", headers=headers)
    assert response.status_code == 200
    kinds = {k["kind"] for k in response.json()}
    # Built-ins.
    assert {"email", "webhook", "discord", "slack", "apprise"} <= kinds
    # Plugin-registered recording provider.
    assert "recording" in kinds


# ── CRUD ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_channel_crud(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Ops chat",
            "kind": "recording",
            "config": {"target": "ops"},
            "min_severity_rank": 40,
        },
    )
    assert create.status_code == 201, create.text
    channel_id = create.json()["id"]

    listing = await client.get("/api/v1/notifications", headers=headers)
    assert {c["id"] for c in listing.json()} == {channel_id}

    patch = await client.patch(
        f"/api/v1/notifications/{channel_id}",
        headers=headers,
        json={"enabled": False, "min_severity_rank": 60},
    )
    body = patch.json()
    assert body["enabled"] is False
    assert body["min_severity_rank"] == 60

    delete = await client.delete(
        f"/api/v1/notifications/{channel_id}", headers=headers
    )
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_create_rejects_unknown_kind(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={"name": "x", "kind": "does_not_exist", "config": {}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_missing_required_config(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "x",
            "kind": "recording",
            "config": {},  # ``target`` is required
        },
    )
    assert response.status_code == 422
    assert "Missing required" in str(response.json())


# ── Test-send ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_test_send_records_delivery(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Ops chat",
            "kind": "recording",
            "config": {"target": "ops"},
        },
    )
    channel_id = create.json()["id"]

    response = await client.post(
        f"/api/v1/notifications/{channel_id}/test",
        headers=headers,
        json={"severity": "warn", "message": "hi"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["channel_name"] == "Ops chat"
    assert "hi" in body["body"]

    # The recorder saw the call.
    recorder = client._recorder  # type: ignore[attr-defined]
    assert len(recorder.calls) == 1

    # The channel row's last_delivery_status got updated.
    refresh = await client.get(
        f"/api/v1/notifications/{channel_id}", headers=headers
    )
    assert refresh.json()["last_delivery_status"] == "sent"

    # A delivery row exists in the log.
    log = await client.get(
        "/api/v1/notifications/deliveries", headers=headers
    )
    assert len(log.json()) == 1
    assert log.json()[0]["status"] == "sent"


@pytest.mark.asyncio
async def test_test_send_records_failure(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Broken",
            "kind": "recording",
            "config": {"target": "ops"},
        },
    )
    channel_id = create.json()["id"]

    # Flip the recorder to fail.
    recorder = client._recorder  # type: ignore[attr-defined]
    recorder.next_report = DeliveryReport(status="failed", detail="went away")

    response = await client.post(
        f"/api/v1/notifications/{channel_id}/test",
        headers=headers,
        json={"severity": "warn"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "went away"

    refresh = await client.get(
        f"/api/v1/notifications/{channel_id}", headers=headers
    )
    detail = refresh.json()
    assert detail["last_delivery_status"] == "failed"
    assert detail["last_delivery_error"] == "went away"


# ── Dispatcher threshold filtering ─────────────────────────────
@pytest.mark.asyncio
async def test_dispatch_skips_below_threshold(client: AsyncClient) -> None:
    """A channel with min_severity_rank=60 must skip a warn alert."""
    from app.notifications.dispatcher import NotificationDispatcher

    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "High-only",
            "kind": "recording",
            "config": {"target": "ops"},
            "min_severity_rank": 60,
        },
    )
    assert create.status_code == 201

    recorder = client._recorder  # type: ignore[attr-defined]
    recorder.calls.clear()

    async with get_database().session() as session:
        dispatcher = NotificationDispatcher(
            session=session, registry=get_registry(), event_bus=get_event_bus()
        )
        report = await dispatcher.dispatch(
            severity="warn",
            rule_id="r1",
            rule_name="Some rule",
            media_file_id="m1",
            context={"path": "/data/movies/x.mkv", "filename": "x.mkv"},
        )
        await session.commit()

    # No call to the provider — threshold filtered it out.
    assert recorder.calls == []
    assert report.sent == 0
    assert report.skipped == 1

    # The audit log shows the skipped attempt.
    log = await client.get(
        "/api/v1/notifications/deliveries?status=skipped", headers=headers
    )
    assert len(log.json()) == 1
    assert log.json()[0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_dispatch_delivers_above_threshold(client: AsyncClient) -> None:
    from app.notifications.dispatcher import NotificationDispatcher

    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "All",
            "kind": "recording",
            "config": {"target": "ops"},
            "min_severity_rank": 0,  # accept everything
        },
    )

    recorder = client._recorder  # type: ignore[attr-defined]
    recorder.calls.clear()

    async with get_database().session() as session:
        dispatcher = NotificationDispatcher(
            session=session, registry=get_registry(), event_bus=get_event_bus()
        )
        report = await dispatcher.dispatch(
            severity="high",
            rule_id="r1",
            rule_name="Some rule",
            media_file_id="m1",
            context={"path": "/data/movies/x.mkv", "filename": "x.mkv"},
        )
        await session.commit()

    assert report.sent == 1
    assert report.skipped == 0
    assert len(recorder.calls) == 1
    # The message subject + body should have been rendered through the
    # default templates.
    _, message = recorder.calls[0]
    assert "Some rule" in message.subject
    assert "x.mkv" in message.body


@pytest.mark.asyncio
async def test_disabled_channel_not_dispatched(client: AsyncClient) -> None:
    from app.notifications.dispatcher import NotificationDispatcher

    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Snoozed",
            "kind": "recording",
            "config": {"target": "ops"},
            "min_severity_rank": 0,
        },
    )
    channel_id = create.json()["id"]
    await client.patch(
        f"/api/v1/notifications/{channel_id}",
        headers=headers,
        json={"enabled": False},
    )

    recorder = client._recorder  # type: ignore[attr-defined]
    recorder.calls.clear()

    async with get_database().session() as session:
        dispatcher = NotificationDispatcher(
            session=session, registry=get_registry(), event_bus=get_event_bus()
        )
        report = await dispatcher.dispatch(
            severity="high",
            rule_id="r1",
            rule_name="r",
            media_file_id="m1",
        )
        await session.commit()

    assert report.sent + report.failed + report.skipped == 0
    assert recorder.calls == []


# ── Channel deletion preserves audit log ───────────────────────
@pytest.mark.asyncio
async def test_delete_channel_keeps_delivery_log(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/notifications",
        headers=headers,
        json={
            "name": "Ephemeral",
            "kind": "recording",
            "config": {"target": "ops"},
        },
    )
    channel_id = create.json()["id"]
    await client.post(
        f"/api/v1/notifications/{channel_id}/test",
        headers=headers,
        json={"severity": "info"},
    )

    # Delete the channel.
    delete = await client.delete(
        f"/api/v1/notifications/{channel_id}", headers=headers
    )
    assert delete.status_code == 204

    # The delivery row should still be in the log with denormalized
    # channel_name preserved. (The FK ``ON DELETE SET NULL`` behaviour
    # is enforced on Postgres; SQLite-on-CI needs ``PRAGMA foreign_keys
    # = ON`` for it to fire, so we don't assert ``channel_id IS NULL``
    # here — the denormalized name is what the audit log actually
    # relies on.)
    async with get_database().session() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(select(NotificationDelivery))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].channel_name == "Ephemeral"
        assert rows[0].channel_kind == "recording"
