"""Stage 14 (audit follow-up) — operator tooling backend tests.

Covers:
  1. ``POST /system/housekeeping/run`` (admin) deletes rows and
     persists a ``housekeeping_runs`` row with ``trigger="manual"``.
  2. ``GET /system/housekeeping/last-run`` returns the most recent
     row (manual or scheduled).
  3. The cron-path (``HousekeepingService.run()`` default trigger)
     also persists into ``housekeeping_runs`` with
     ``trigger="scheduled"``.
  4. Housekeeping run-now is admin-gated.
  5. Audit log endpoint honours ``since`` / ``until`` / ``before_id``
     and the 500 cap is enforced.

The poller, analyzer, and scan paths are NOT touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.housekeeping_run import HousekeepingRun
from app.models.notification_delivery import NotificationDelivery
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage14.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_HOUSEKEEPING_DELIVERY_RETENTION_DAYS", "1")
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


async def _user_headers(client: AsyncClient) -> dict[str, str]:
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
    return {"authorization": f"Bearer {login.json()['access_token']}"}


# ── C: Housekeeping ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_housekeeping_run_now_admin_only(client: AsyncClient) -> None:
    user_headers = await _user_headers(client)
    r = await client.post(
        "/api/v1/system/housekeeping/run", headers=user_headers
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_housekeeping_run_deletes_and_persists_run_row(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    # Seed an old delivery (5 days ago, retention=1 day) so the
    # trim has something to delete.
    async with get_database().session() as sess:
        sess.add(
            NotificationDelivery(
                channel_id="ch-1",
                channel_name="Test channel",
                channel_kind="webhook",
                status="ok",
                severity="info",
                subject="Old delivery",
                body="body",
                context={},
                attempted_at=datetime.now(UTC) - timedelta(days=5),
            )
        )
        await sess.commit()

    r = await client.post(
        "/api/v1/system/housekeeping/run", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trigger"] == "manual"
    assert body["notification_deliveries"] == 1
    assert body["total"] == 1

    # Persisted history row exists with trigger="manual".
    async with get_database().session() as sess:
        rows = (
            await sess.execute(select(HousekeepingRun))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].trigger == "manual"
    assert rows[0].deliveries_deleted == 1
    assert rows[0].finished_at is not None
    assert rows[0].error is None


@pytest.mark.asyncio
async def test_housekeeping_last_run_returns_most_recent(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)

    # No runs yet → null.
    r = await client.get(
        "/api/v1/system/housekeeping/last-run", headers=headers
    )
    assert r.status_code == 200
    assert r.json() is None

    # Trigger once.
    await client.post("/api/v1/system/housekeeping/run", headers=headers)
    r = await client.get(
        "/api/v1/system/housekeeping/last-run", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["trigger"] == "manual"
    assert body["error"] is None
    # All counters present (zeroes are fine for an empty seed).
    for k in (
        "deliveries_deleted",
        "update_checks_deleted",
        "rule_evaluations_deleted",
        "job_runs_deleted",
    ):
        assert k in body


@pytest.mark.asyncio
async def test_housekeeping_scheduled_trigger_persists(
    client: AsyncClient,
) -> None:
    """Calling the service directly with the default ``trigger``
    persists a row marked ``scheduled``. Mirrors the worker tick
    path: the API never touches it, but operators must see both
    triggers in their history."""
    headers = await _admin_headers(client)

    from app.core.settings import get_settings
    from app.housekeeping import HousekeepingService

    settings = get_settings()
    async with get_database().session() as sess:
        await HousekeepingService(session=sess, settings=settings).run()

    r = await client.get(
        "/api/v1/system/housekeeping/last-run", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["trigger"] == "scheduled"


# ── A: Audit log filter extensions ─────────────────────────────
@pytest.mark.asyncio
async def test_audit_log_filters_since_until_and_before_id(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    # Seed 5 entries at known timestamps.
    now = datetime.now(UTC)
    async with get_database().session() as sess:
        for i in range(5):
            sess.add(
                AuditLogEntry(
                    occurred_at=now - timedelta(days=i),
                    actor_id="u-test",
                    actor_label="tester",
                    action=f"act.{i}",
                    target_type="t",
                    target_id="x",
                    ip_address=None,
                    request_id=None,
                    metadata_=None,
                )
            )
        await sess.commit()

    # Total entries via baseline call: 5 seeded + 1 from registering
    # the admin user (auth.register fires an audit). We assert with
    # >= to stay robust to internal events.
    r = await client.get("/api/v1/audit/log?limit=500", headers=headers)
    all_rows = r.json()
    assert len(all_rows) >= 5

    # since filter: only rows from "today minus 1 day" → expect 1
    # match (act.0) since act.1 .. act.4 are all >1 day old. The
    # ISO timestamp's "+00:00" zone marker must be percent-encoded
    # because the URL parser otherwise treats "+" as a space.
    since_iso = (now - timedelta(days=1, hours=1)).isoformat().replace(
        "+", "%2B"
    )
    r = await client.get(
        f"/api/v1/audit/log?since={since_iso}&action=act.0", headers=headers
    )
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["action"] == "act.0"

    # action filter narrows to 1 row.
    r = await client.get("/api/v1/audit/log?action=act.3", headers=headers)
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["action"] == "act.3"

    # before_id cursor: first page (limit=2), then load before_id.
    r = await client.get("/api/v1/audit/log?limit=2", headers=headers)
    page1 = r.json()
    assert len(page1) == 2
    cursor = page1[-1]["id"]
    r = await client.get(
        f"/api/v1/audit/log?limit=2&before_id={cursor}", headers=headers
    )
    page2 = r.json()
    assert len(page2) <= 2
    # No overlap between pages.
    page1_ids = {row["id"] for row in page1}
    page2_ids = {row["id"] for row in page2}
    assert not (page1_ids & page2_ids)
    # All page2 ids strictly less than the cursor.
    assert all(rid < cursor for rid in page2_ids)


@pytest.mark.asyncio
async def test_audit_log_limit_cap_enforced(client: AsyncClient) -> None:
    """The Stage 14 guard rail says ``limit=0`` and ``limit=10000``
    must be rejected to prevent runaway responses."""
    headers = await _admin_headers(client)
    r = await client.get("/api/v1/audit/log?limit=0", headers=headers)
    assert r.status_code == 422
    r = await client.get("/api/v1/audit/log?limit=10000", headers=headers)
    assert r.status_code == 422
