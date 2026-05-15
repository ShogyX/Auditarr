"""Housekeeping service tests."""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.events.bus import get_event_bus
from app.housekeeping import HousekeepingService
from app.models.job_run import JobRun
from app.models.notification_delivery import NotificationDelivery
from app.models.update_check import UpdateCheck
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


@pytest_asyncio.fixture
async def session_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple]:
    db_path = tmp_path / "hk.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with db.session() as session:
            yield session, settings
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


@pytest.mark.asyncio
async def test_housekeeping_trims_old_deliveries(session_and_settings) -> None:
    session, settings = session_and_settings
    now = utcnow()
    old = now - _dt.timedelta(days=90)
    recent = now - _dt.timedelta(days=2)

    for ts, name in ((old, "old"), (old, "old2"), (recent, "recent")):
        session.add(
            NotificationDelivery(
                channel_id=None,
                channel_name=name,
                channel_kind="webhook",
                status="sent",
                severity="warn",
                subject="x",
                body="x",
                context={},
                attempted_at=ts,
            )
        )
    await session.commit()

    service = HousekeepingService(session=session, settings=settings)
    report = await service.run()
    # default retention = 30 days, so the two old rows go.
    assert report.notification_deliveries == 2

    remaining = (
        await session.execute(select(NotificationDelivery))
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].channel_name == "recent"


@pytest.mark.asyncio
async def test_housekeeping_retention_zero_disables_trim(
    session_and_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _settings = session_and_settings
    # Re-import settings with retention zeroed out.
    monkeypatch.setenv("AUDITARR_HOUSEKEEPING_DELIVERY_RETENTION_DAYS", "0")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    session.add(
        NotificationDelivery(
            channel_id=None,
            channel_name="ancient",
            channel_kind="webhook",
            status="sent",
            severity="warn",
            subject="x",
            body="x",
            context={},
            attempted_at=utcnow() - _dt.timedelta(days=10_000),
        )
    )
    await session.commit()

    service = HousekeepingService(session=session, settings=settings)
    report = await service.run()
    assert report.notification_deliveries == 0  # retention=0 means skipped

    rows = (
        await session.execute(select(NotificationDelivery))
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_housekeeping_trims_old_update_checks_and_job_runs(
    session_and_settings,
) -> None:
    session, settings = session_and_settings
    now = utcnow()
    old = now - _dt.timedelta(days=200)
    recent = now - _dt.timedelta(days=1)

    for ts in (old, recent):
        session.add(
            UpdateCheck(
                checked_at=ts,
                ok=True,
                latest_version="1.0.0",
                changelog=None,
                detail=None,
                feed_url="https://x.test",
            )
        )
        session.add(
            JobRun(
                schedule_id=None,
                job_kind="scan_library",
                job_args={},
                status="completed",
                started_at=ts,
                finished_at=ts,
            )
        )
    await session.commit()

    service = HousekeepingService(session=session, settings=settings)
    report = await service.run()
    # default: update_check retention 90d, job_run retention 60d.
    assert report.update_checks == 1
    assert report.job_runs == 1
