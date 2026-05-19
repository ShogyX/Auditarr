"""v1.9 Stage 8.1 — system logs API.

Pins:
  1. GET /api/v1/system/logs requires admin.
  2. Records added to the ring buffer come back via the API.
  3. ``service`` filter narrows by category.
  4. ``level`` filter narrows by minimum severity.
  5. ``since`` filter drops earlier records.
  6. Pagination via ``cursor`` walks back through history.
  7. ``last_error_at`` is surfaced after an error record.
  8. NDJSON export streams one record per line.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.log_buffer import LogRecord, LogRingBuffer, set_log_buffer
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "stage8.db"
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

    # Isolate the buffer per test.
    fresh_buffer = LogRingBuffer(capacity=100)
    set_log_buffer(fresh_buffer)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield {"client": c, "buffer": fresh_buffer, "db": db}
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        set_log_buffer(LogRingBuffer(capacity=5000))  # restore default
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
    return {"authorization": f"Bearer {login.json()['access_token']}"}


def _push(
    buffer: LogRingBuffer,
    *,
    level: str = "info",
    category: str = "api",
    event: str = "msg",
    ts: _dt.datetime | None = None,
    context: dict | None = None,
) -> LogRecord:
    record = LogRecord(
        timestamp=(ts or _dt.datetime.now(_dt.UTC)).isoformat(),
        level=level,
        logger="auditarr.test",
        category=category,
        event=event,
        context=context or {},
    )
    buffer.push(record)
    return record


# ── Auth ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logs_requires_admin(env) -> None:
    client = env["client"]
    # Register a non-admin user.
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
    r = await client.get("/api/v1/system/logs", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_logs_unauthenticated_returns_401(env) -> None:
    client = env["client"]
    r = await client.get("/api/v1/system/logs")
    assert r.status_code == 401


# ── Basic retrieval ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_records_are_returned_newest_first(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    # Authenticate FIRST (this writes API records to the buffer);
    # then clear and push our test records so the assertion sees
    # only those.
    headers = await _admin_headers(client)
    buffer.clear()
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    _push(buffer, ts=base, event="first")
    _push(buffer, ts=base + _dt.timedelta(seconds=1), event="second")
    _push(buffer, ts=base + _dt.timedelta(seconds=2), event="third")

    r = await client.get("/api/v1/system/logs", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # The GET request itself adds an api.request log entry — so
    # filter to just the events we seeded.
    events = [
        rec["event"]
        for rec in body["records"]
        if rec["event"] in ("first", "second", "third")
    ]
    assert events == ["third", "second", "first"]


# ── Filters ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_filter_narrows_by_category(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    _push(buffer, category="api", event="api-evt")
    _push(buffer, category="worker", event="worker-evt")
    _push(buffer, category="api", event="api-evt-2")

    headers = await _admin_headers(client)
    r = await client.get(
        "/api/v1/system/logs?service=api", headers=headers
    )
    body = r.json()
    # The `api.request` access-log event the API middleware emits for
    # THIS request also lands in the buffer with category=api now that
    # the deferred logger correctly routes module-top loggers through
    # stdlib (previously it bypassed the buffer). Filter to just the
    # events this test seeded, same pattern as
    # `test_records_are_ordered_newest_first` above.
    events = {
        rec["event"]
        for rec in body["records"]
        if rec["event"] in ("api-evt", "worker-evt", "api-evt-2")
    }
    assert events == {"api-evt", "api-evt-2"}


@pytest.mark.asyncio
async def test_level_filter_narrows_by_severity(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    _push(buffer, level="info", event="info-msg")
    _push(buffer, level="warning", event="warn-msg")
    _push(buffer, level="error", event="error-msg")
    _push(buffer, level="critical", event="crit-msg")

    headers = await _admin_headers(client)
    # level=error → error + critical.
    r = await client.get(
        "/api/v1/system/logs?level=error", headers=headers
    )
    body = r.json()
    events = {rec["event"] for rec in body["records"]}
    assert events == {"error-msg", "crit-msg"}


@pytest.mark.asyncio
async def test_since_filter_drops_earlier_records(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    old = _dt.datetime(2025, 1, 1, tzinfo=_dt.UTC)
    new = _dt.datetime(2026, 5, 18, tzinfo=_dt.UTC)
    _push(buffer, ts=old, event="ancient")
    _push(buffer, ts=new, event="fresh")

    cutoff = _dt.datetime(2025, 6, 1, tzinfo=_dt.UTC).isoformat()
    r = await client.get(
        f"/api/v1/system/logs?since={cutoff}", headers=headers
    )
    body = r.json()
    # The seeded "ancient" record is in 2025 and should NOT
    # appear; the seeded "fresh" record IS post-cutoff. The
    # GET request's own api.request log row may also appear
    # (it's after the cutoff) — that's fine; we just check
    # that "ancient" is filtered out and "fresh" survived.
    events = {rec["event"] for rec in body["records"]}
    assert "fresh" in events
    assert "ancient" not in events


# ── Pagination ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_paginates_through_history(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    for i in range(10):
        _push(buffer, ts=base + _dt.timedelta(seconds=i), event=f"evt-{i}")

    # The GET request itself pushes an api.request log entry that
    # will be NEWEST in the buffer. To make the pagination
    # assertion deterministic, use the ``service`` filter to
    # narrow to our seeded category (default "api" matches the
    # api.request rows too, so use a distinct category).
    buffer.clear()
    for i in range(10):
        _push(
            buffer,
            ts=base + _dt.timedelta(seconds=i),
            event=f"evt-{i}",
            category="test-pagination",
        )
    r1 = await client.get(
        "/api/v1/system/logs?limit=4&service=test-pagination",
        headers=headers,
    )
    body1 = r1.json()
    assert body1["count"] == 4
    assert body1["next_cursor"] == 4
    events1 = [rec["event"] for rec in body1["records"]]
    assert events1 == ["evt-9", "evt-8", "evt-7", "evt-6"]

    r2 = await client.get(
        f"/api/v1/system/logs?limit=4&service=test-pagination&cursor={body1['next_cursor']}",
        headers=headers,
    )
    body2 = r2.json()
    events2 = [rec["event"] for rec in body2["records"]]
    assert events2 == ["evt-5", "evt-4", "evt-3", "evt-2"]
    assert body2["next_cursor"] == 8


# ── last_error_at ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_last_error_at_is_surfaced_after_error(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    _push(buffer, level="info", event="ok")

    headers = await _admin_headers(client)
    r = await client.get("/api/v1/system/logs", headers=headers)
    assert r.json()["last_error_at"] is None

    _push(buffer, level="error", event="boom")
    r = await client.get("/api/v1/system/logs", headers=headers)
    assert r.json()["last_error_at"] is not None


# ── NDJSON export ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_streams_ndjson(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    _push(
        buffer, ts=base, event="first", category="export-test"
    )
    _push(
        buffer,
        ts=base + _dt.timedelta(seconds=1),
        event="second",
        category="export-test",
    )

    r = await client.get(
        "/api/v1/system/logs/export?service=export-test",
        headers=headers,
    )
    assert r.status_code == 200
    assert "ndjson" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = [line for line in r.text.strip().split("\n") if line]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    events = {entry["event"] for entry in parsed}
    assert events == {"first", "second"}


@pytest.mark.asyncio
async def test_export_respects_filters(env) -> None:
    client = env["client"]
    buffer = env["buffer"]
    _push(buffer, category="api", level="info", event="api-info")
    _push(buffer, category="worker", level="error", event="worker-err")

    headers = await _admin_headers(client)
    r = await client.get(
        "/api/v1/system/logs/export?service=worker", headers=headers
    )
    lines = [line for line in r.text.strip().split("\n") if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "worker-err"


# ── v1.9 audit fixes ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_since_tz_naive_does_not_crash(env) -> None:
    """v1.9 audit fix (LOG-1): operator passing a since without a
    tz suffix must not 500. Treat as UTC."""
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    _push(
        buffer,
        ts=_dt.datetime(2020, 1, 1, tzinfo=_dt.UTC),
        event="ancient",
    )
    _push(
        buffer,
        ts=_dt.datetime(2026, 5, 18, tzinfo=_dt.UTC),
        event="fresh",
    )
    # No tz suffix.
    r = await client.get(
        "/api/v1/system/logs?since=2025-06-01T00:00:00",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    events = {rec["event"] for rec in r.json()["records"]}
    assert "fresh" in events
    assert "ancient" not in events


@pytest.mark.asyncio
async def test_negative_cursor_clamped_to_zero(env) -> None:
    """v1.9 audit fix (LOG-2): negative cursor clamps to 0 rather
    than slicing from the end."""
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    for i in range(5):
        _push(
            buffer,
            ts=base + _dt.timedelta(seconds=i),
            event=f"e{i}",
            category="cursor-test",
        )
    r = await client.get(
        "/api/v1/system/logs?service=cursor-test&limit=3&cursor=-5",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    events = [rec["event"] for rec in r.json()["records"]]
    # Newest first, starting from 0.
    assert events == ["e4", "e3", "e2"]


@pytest.mark.asyncio
async def test_last_error_at_filtered_to_request(env) -> None:
    """v1.9 audit fix (LOG-3): last_error_at reflects the
    filtered records, not the global buffer state. Filtering
    to a service with no errors → no pulse."""
    client = env["client"]
    buffer = env["buffer"]
    headers = await _admin_headers(client)
    buffer.clear()
    # Worker has an error.
    _push(
        buffer,
        category="worker",
        level="error",
        event="worker-boom",
    )
    # API category has only info.
    _push(buffer, category="api-test", level="info", event="ok")
    # Without filter — last_error_at is populated.
    r_all = await client.get(
        "/api/v1/system/logs?service=all", headers=headers
    )
    assert r_all.json()["last_error_at"] is not None
    # Filtered to api-test — no error in that category → None.
    r_api = await client.get(
        "/api/v1/system/logs?service=api-test", headers=headers
    )
    assert r_api.json()["last_error_at"] is None
