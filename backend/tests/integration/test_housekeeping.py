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


@pytest.mark.asyncio
async def test_housekeeping_sweeps_stuck_playback_sessions(
    session_and_settings,
) -> None:
    """v1.9 OP-10 caveat 7: a PlaybackSession stuck in non-stopped
    state with last_event_at > 24h ago gets forcibly marked
    stopped so the analyzer can ingest it and operators don't
    see a ghost "now playing" row."""
    from app.models.integration import Integration
    from app.models.playback import PlaybackSession

    session, settings = session_and_settings
    now = utcnow()

    ig = Integration(
        name="Plex", kind="plex", enabled=True,
        poll_interval_seconds=900,
        config={"base_url": "http://stub/"},
        health_status="ok",
    )
    session.add(ig)
    await session.flush()
    ig_id = ig.id

    # Stuck row: state=playing, last_event_at 25h ago.
    session.add(
        PlaybackSession(
            integration_id=ig_id,
            session_key="sk-stuck",
            state="playing",
            decision="direct_play",
            started_at=now - _dt.timedelta(hours=26),
            last_event_at=now - _dt.timedelta(hours=25),
        )
    )
    # Fresh row: state=playing, last_event_at 30 min ago — must NOT be swept.
    session.add(
        PlaybackSession(
            integration_id=ig_id,
            session_key="sk-fresh",
            state="playing",
            decision="direct_play",
            started_at=now - _dt.timedelta(minutes=45),
            last_event_at=now - _dt.timedelta(minutes=30),
        )
    )
    await session.commit()

    service = HousekeepingService(session=session, settings=settings)
    report = await service.run()
    assert report.stuck_sessions_swept == 1

    rows = (
        (await session.execute(select(PlaybackSession)))
        .scalars()
        .all()
    )
    by_key = {r.session_key: r for r in rows}
    assert by_key["sk-stuck"].state == "stopped"
    assert by_key["sk-stuck"].stopped_at is not None
    assert by_key["sk-fresh"].state == "playing"
    assert by_key["sk-fresh"].stopped_at is None
