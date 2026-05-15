"""Automation API + scheduler integration tests."""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.automation.catalogue import (
    JobCatalogue,
    JobSpec,
    get_catalogue,
    reset_catalogue,
)
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


async def _record_run(_session, _args, _ctx):  # type: ignore[no-untyped-def]
    return {"echo": "ok"}


def _install_test_catalogue() -> JobCatalogue:
    """Reset the global catalogue to one with predictable jobs for tests."""
    reset_catalogue()
    from app.automation.jobs import register_builtin_jobs

    cat = get_catalogue()  # this populates with built-ins
    cat.register(
        JobSpec(
            key="echo_test",
            label="Echo (test)",
            description="Always succeeds, returns a fixed dict.",
            args_schema={"type": "object"},
            timeout_seconds=10,
            runner=_record_run,
        )
    )
    return cat


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "automation.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    _install_test_catalogue()

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
        get_settings.cache_clear()
        reset_catalogue()


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


@pytest.mark.asyncio
async def test_list_job_kinds_includes_builtins_and_test_job(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/automation/jobs", headers=headers)
    assert response.status_code == 200
    keys = {k["key"] for k in response.json()}
    assert {
        "scan_library",
        "healthcheck_integration",
        "sync_integration_tags",
        "evaluate_library",
        "echo_test",
    } <= keys


@pytest.mark.asyncio
async def test_schedule_crud_and_priming(client: AsyncClient) -> None:
    headers = await _admin_headers(client)

    create = await client.post(
        "/api/v1/automation/schedules",
        headers=headers,
        json={
            "name": "Nightly echo",
            "job_kind": "echo_test",
            "cron": {"minute": 0, "hour": 3},
        },
    )
    assert create.status_code == 201, create.text
    schedule_id = create.json()["id"]
    # next_run_at must be primed.
    assert create.json()["next_run_at"] is not None

    listing = await client.get("/api/v1/automation/schedules", headers=headers)
    assert {s["id"] for s in listing.json()} == {schedule_id}

    update = await client.patch(
        f"/api/v1/automation/schedules/{schedule_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert update.json()["enabled"] is False

    delete = await client.delete(
        f"/api/v1/automation/schedules/{schedule_id}", headers=headers
    )
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_create_rejects_unknown_job_kind(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/automation/schedules",
        headers=headers,
        json={
            "name": "Bad",
            "job_kind": "no_such_job",
            "cron": {},
        },
    )
    assert response.status_code == 422
    assert "Unknown job_kind" in str(response.json())


@pytest.mark.asyncio
async def test_create_rejects_bad_cron(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/automation/schedules",
        headers=headers,
        json={
            "name": "Bad cron",
            "job_kind": "echo_test",
            "cron": {"minute": 70},  # out of range
        },
    )
    assert response.status_code == 422
    assert "cron" in str(response.json()).lower()


@pytest.mark.asyncio
async def test_run_job_now_records_a_run(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/automation/run",
        headers=headers,
        json={"job_kind": "echo_test", "job_args": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"] == {"echo": "ok"}
    assert body["trigger"] == "manual"
    assert body["schedule_id"] is None
    assert body["duration_ms"] is not None

    # The run should show up in the recent-runs list.
    runs = await client.get("/api/v1/automation/runs", headers=headers)
    assert any(r["id"] == body["id"] for r in runs.json())


@pytest.mark.asyncio
async def test_run_now_propagates_runner_failure(client: AsyncClient) -> None:
    # Replace the echo_test runner with one that always raises.
    cat = get_catalogue()
    cat._jobs["echo_test"].runner = lambda *_args, **_kw: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        RuntimeError("boom")
    )

    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/automation/run",
        headers=headers,
        json={"job_kind": "echo_test", "job_args": {}},
    )
    # The endpoint returns 200 with status=failed — the failure is recorded
    # in the run, not surfaced as an HTTP error.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "boom" in (body["error"] or "")


@pytest.mark.asyncio
async def test_scheduler_tick_runs_due_schedules() -> None:
    """Direct exercise of Scheduler.tick() without going through HTTP."""
    from app.automation.scheduler import Scheduler

    _install_test_catalogue()

    monkeypatch_db = None
    from app.core.settings import get_settings
    get_settings.cache_clear()
    # Use a fresh per-test sqlite for isolation.
    import os
    import tempfile

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    os.environ["AUDITARR_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_db.name}"
    os.environ["AUDITARR_SECRET_KEY"] = "test-key-must-be-at-least-sixteen-chars"
    get_settings.cache_clear()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        async with db.session() as session:
            from app.models.schedule import Schedule

            schedule = Schedule(
                name="due-now",
                job_kind="echo_test",
                job_args={},
                cron={},
                # Mark as due *before* now so the tick picks it up.
                next_run_at=_dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=1),
                timeout_seconds=10,
                enabled=True,
            )
            session.add(schedule)
            await session.commit()
            schedule_id = schedule.id

        async with db.session() as session:
            scheduler = Scheduler(session=session, event_bus=bus)
            report = await scheduler.tick(
                {"registry": None, "bus": bus, "ffprobe": None}
            )
            assert len(report.enqueued) == 1
            assert schedule_id in report.rescheduled

        # The schedule's next_run_at should have advanced.
        async with db.session() as session:
            from app.models.schedule import Schedule

            refreshed = await session.get(Schedule, schedule_id)
            assert refreshed is not None
            assert refreshed.last_run_at is not None
            assert refreshed.last_status == "completed"
            assert refreshed.next_run_at is not None
            # SQLite may return tz-naive datetimes even for TIMESTAMPTZ
            # columns; normalize before comparing.
            next_at = refreshed.next_run_at
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=_dt.UTC)
            assert next_at > _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=2)
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        bus.clear()
        get_settings.cache_clear()
        reset_catalogue()
        os.unlink(tmp_db.name)
