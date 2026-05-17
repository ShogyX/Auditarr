"""Stage 09 (v1.7) — Playback poller cursor safety-skew test.

Plan §491:
    Feed two events with started_at 30s apart, then a third
    backdated 45s; assert all three are inserted.

This file pins the contract of the new ``CURSOR_SAFETY_SKEW``
behaviour: the cursor advances to ``max(started_at) − 60s``
rather than ``max(started_at)`` itself, so a follow-up batch
with an event that arrives slightly out of order isn't dropped.
A 45s-backdated event lands cleanly because the previous poll's
cursor was rewound 60s.

The dedup safety net is exercised too — re-fetching the same
two events on the second poll is harmless thanks to the
``(integration_id, upstream_id)`` unique constraint.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.events.bus import get_event_bus
from app.integrations.manager import IntegrationManager
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    PlaybackEventDTO,
    TagSync,
)
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import IntegrationPollingCursor, PlaybackEvent
from app.security.secrets import get_secret_box
from app.services.playback import PlaybackPoller
from app.services.playback.poller import CURSOR_SAFETY_SKEW
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


# ── Stub provider ────────────────────────────────────────────


class _RecordingStub:
    """Stub provider that records the ``since`` value the poller
    passes in (so we can pin the cursor-rewind behaviour) and
    serves a per-test batch via ``next_batch``."""

    kind = "stubplex09"
    label = "Stage 09 Stub"
    config_schema: dict = {"type": "object", "properties": {}}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.next_batch: list[PlaybackEventDTO] = []
        self.received_since: list[_dt.datetime | None] = []

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, since: _dt.datetime | None
    ) -> list[PlaybackEventDTO]:
        # Record what the poller asked for so the test can pin
        # the rewind.
        self.received_since.append(since)
        batch = list(self.next_batch)
        self.next_batch = []
        return batch

    # Stage 07 / Stage 08 protocol additions — required so
    # ``runtime_checkable`` isinstance check passes when the
    # manager looks up the provider.
    async def submit_transcode_job(self, _config, _job_spec):  # noqa: ANN001, ANN202
        from app.integrations.types import JobSubmitResult

        return JobSubmitResult(status="rejected", detail="stub")

    async def list_transcode_profiles(self, _config):  # noqa: ANN001, ANN202
        return []

    async def get_transcode_job_status(self, _config, _upstream_job_id):  # noqa: ANN001, ANN202
        from app.integrations.types import TranscodeJobStatus

        return TranscodeJobStatus(status="unknown")


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "playback09.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Seed library + media files at the Auditarr-side paths so
    # the poller can resolve them.
    async with db.session() as session:
        lib = Library(
            name="Movies", root_path="/mnt/media/Movies", kind="movies"
        )
        session.add(lib)
        await session.flush()
        for fname in ("a.mkv", "b.mkv", "c.mkv"):
            session.add(
                MediaFile(
                    library_id=lib.id,
                    path=f"/mnt/media/Movies/{fname}",
                    relative_path=fname,
                    filename=fname,
                    extension="mkv",
                    size_bytes=1024 * 1024,
                    mtime=_dt.datetime.now(_dt.UTC),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    has_subtitles=False,
                    seen_at=_dt.datetime.now(_dt.UTC),
                    is_orphaned=False,
                )
            )
        integration = Integration(
            name="Stub Plex 09",
            kind="stubplex09",
            enabled=True,
            poll_interval_seconds=900,
            config={"base_url": "http://stub/"},
            health_status="unknown",
        )
        session.add(integration)
        await session.commit()
        integration_id = integration.id

    from app.core.registry import get_registry

    registry = get_registry()
    bus = get_event_bus()
    stub = _RecordingStub()
    registry.register_capability("integration.stubplex09", stub)

    def _make_manager(session):
        return IntegrationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )

    try:
        yield {
            "db": db,
            "make_manager": _make_manager,
            "stub": stub,
            "integration_id": integration_id,
            "bus": bus,
        }
    finally:
        registry.clear()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


# ── Test 1 — Plan §491 contract ────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_rewinds_safety_skew_so_backdated_event_isnt_dropped(
    env,
) -> None:
    """Plan §491: feed two events with started_at 30s apart,
    then a third backdated 45s; assert all three are inserted.

    This is the load-bearing scenario: without the safety skew
    the third event's started_at is *before* the cursor and the
    poller silently drops it. With ``CURSOR_SAFETY_SKEW=60s``
    the cursor was rewound past the third event's timestamp so
    it makes it through.
    """
    db = env["db"]
    stub = env["stub"]
    integration_id = env["integration_id"]
    make_manager = env["make_manager"]

    # All three events occur within a 1-minute window. The
    # second's started_at is the latest in batch 1; the third's
    # is between the first and the second (backdated 45s
    # relative to the second).
    base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
    evt1 = PlaybackEventDTO(
        upstream_id="evt-skew-1",
        source_path="/mnt/media/Movies/a.mkv",
        decision="direct_play",
        started_at=base,
    )
    evt2 = PlaybackEventDTO(
        upstream_id="evt-skew-2",
        source_path="/mnt/media/Movies/b.mkv",
        decision="direct_play",
        started_at=base + _dt.timedelta(seconds=30),
    )
    # Third event arrives on the SECOND poll batch but with a
    # started_at backdated 45s from the previous batch's max
    # (i.e. T+30 → backdated to T-15).
    evt3 = PlaybackEventDTO(
        upstream_id="evt-skew-3",
        source_path="/mnt/media/Movies/c.mkv",
        decision="direct_play",
        started_at=base + _dt.timedelta(seconds=30) - _dt.timedelta(seconds=45),
    )

    # ── Batch 1: poll the first two events ──────────────────
    stub.next_batch = [evt1, evt2]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        outcome = await poller.poll_one(integration)
        assert outcome.fetched == 2
        assert outcome.inserted == 2

    # After batch 1 the cursor sits at ``max(started_at) − skew``.
    # max(started_at) = base + 30s; cursor = base + 30s − 60s
    # = base − 30s.
    expected_cursor = (base + _dt.timedelta(seconds=30)) - CURSOR_SAFETY_SKEW
    async with db.session() as session:
        rows = (
            await session.execute(
                select(IntegrationPollingCursor).where(
                    IntegrationPollingCursor.integration_id == integration_id
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        cursor_val = _dt.datetime.fromisoformat(rows[0].cursor_value)
        # Cursor should equal base − 30s, NOT base + 30s.
        assert cursor_val == expected_cursor

    # ── Batch 2: feed the backdated event ───────────────────
    # The poller passes ``since=cursor_val`` to fetch_playback_events.
    # The backdated event's started_at = base − 15s, which is
    # >= cursor_val (base − 30s), so the provider WOULD include
    # it. Crucially, the poller does NOT drop it on insert.
    stub.next_batch = [evt3]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        outcome = await poller.poll_one(integration)
        assert outcome.fetched == 1
        assert outcome.inserted == 1

    # Confirm: the poller asked the provider for events since
    # the rewound cursor (not since the un-rewound max), so a
    # provider that respects ``since`` would have surfaced the
    # backdated event.
    assert stub.received_since[1] == expected_cursor

    # Final state: three rows in playback_events.
    async with db.session() as session:
        rows = (
            await session.execute(
                select(PlaybackEvent).order_by(PlaybackEvent.started_at)
            )
        ).scalars().all()
        assert len(rows) == 3
        upstream_ids = sorted(r.upstream_id for r in rows)
        assert upstream_ids == ["evt-skew-1", "evt-skew-2", "evt-skew-3"]


# ── Test 2 — Replay safety ─────────────────────────────────────


@pytest.mark.asyncio
async def test_replayed_events_dedup_cleanly_via_unique_constraint(
    env,
) -> None:
    """Because the cursor rewinds by 60s, the next poll may
    receive events it already has. The unique constraint on
    ``(integration_id, upstream_id)`` dedups them — re-fetched
    events are NOT inserted again, and the rest of the batch
    isn't disturbed.
    """
    db = env["db"]
    stub = env["stub"]
    integration_id = env["integration_id"]
    make_manager = env["make_manager"]

    base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
    evt1 = PlaybackEventDTO(
        upstream_id="dup-1",
        source_path="/mnt/media/Movies/a.mkv",
        decision="direct_play",
        started_at=base,
    )
    new_evt = PlaybackEventDTO(
        upstream_id="dup-2-new",
        source_path="/mnt/media/Movies/b.mkv",
        decision="direct_play",
        started_at=base + _dt.timedelta(seconds=10),
    )

    # Batch 1: just evt1.
    stub.next_batch = [evt1]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        out1 = await poller.poll_one(integration)
        assert out1.inserted == 1

    # Batch 2: evt1 AGAIN (the provider re-emits it because the
    # cursor was rewound past its started_at) plus one new event.
    stub.next_batch = [evt1, new_evt]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        out2 = await poller.poll_one(integration)
        assert out2.fetched == 2
        # Only the new event was inserted; evt1 was deduped.
        assert out2.inserted == 1

    # Final: two distinct rows in the table.
    async with db.session() as session:
        rows = (
            await session.execute(select(PlaybackEvent))
        ).scalars().all()
        assert len(rows) == 2
        upstream_ids = sorted(r.upstream_id for r in rows)
        assert upstream_ids == ["dup-1", "dup-2-new"]


# ── Test 3 — Last-poll health detail surfaces (plan §481) ──────


@pytest.mark.asyncio
async def test_last_poll_health_detail_surfaces_counts(env) -> None:
    """Plan §481: on a successful poll without drift, the
    integration's ``health_detail`` should carry a short
    summary so the dashboard can render "Last poll: N events
    ingested at T"."""
    db = env["db"]
    stub = env["stub"]
    integration_id = env["integration_id"]
    make_manager = env["make_manager"]

    base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="hd-1",
            source_path="/mnt/media/Movies/a.mkv",
            decision="direct_play",
            started_at=base,
        ),
        PlaybackEventDTO(
            upstream_id="hd-2",
            source_path="/mnt/media/Movies/b.mkv",
            decision="direct_play",
            started_at=base + _dt.timedelta(seconds=10),
        ),
    ]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        outcome = await poller.poll_one(integration)
        assert outcome.inserted == 2

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        # Both events resolved cleanly (paths match seeded media).
        # The detail uses the resolved-only format (no
        # "unresolved" tail).
        assert integration.health_detail is not None
        assert "Last poll:" in integration.health_detail
        assert "2 of 2 events ingested" in integration.health_detail
        # Resolved-only format does not surface the split.
        assert "unresolved" not in integration.health_detail
        assert integration.health_checked_at is not None


@pytest.mark.asyncio
async def test_last_poll_health_detail_surfaces_unresolved_split(
    env,
) -> None:
    """When some events couldn't be resolved (path mappings
    not catching the file) but the batch is below the drift
    threshold (DriftReport.drift_suspected requires seen>=5),
    the last-poll detail line surfaces the
    resolved/unresolved split AND a "check path mappings"
    hint. This is what the dashboard banner reads on small
    batches with partial resolution."""
    db = env["db"]
    stub = env["stub"]
    integration_id = env["integration_id"]
    make_manager = env["make_manager"]

    base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
    # 3 events — one resolves, two don't. Below the 5-sample
    # drift floor so the drift branch is skipped and the last-
    # poll detail handles the operator-visible reporting.
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="hd-r-1",
            source_path="/mnt/media/Movies/a.mkv",
            decision="direct_play",
            started_at=base,
        ),
        PlaybackEventDTO(
            upstream_id="hd-u-1",
            source_path="/wrong/path/x.mkv",
            decision="direct_play",
            started_at=base + _dt.timedelta(seconds=10),
        ),
        PlaybackEventDTO(
            upstream_id="hd-u-2",
            source_path="/wrong/path/y.mkv",
            decision="direct_play",
            started_at=base + _dt.timedelta(seconds=20),
        ),
    ]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        outcome = await poller.poll_one(integration)
        assert outcome.fetched == 3
        assert outcome.resolved == 1
        assert outcome.unresolved == 2
        # Below the 5-sample drift floor.
        assert outcome.drift_suspected is False

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        # Last-poll detail wins this slot (no drift). The format
        # surfaces the resolved/unresolved split + the
        # path-mappings hint.
        assert integration.health_detail is not None
        assert "Last poll:" in integration.health_detail
        assert "3 of 3 events ingested" in integration.health_detail
        assert "1 resolved" in integration.health_detail
        assert "2 unresolved" in integration.health_detail
        assert "path mappings" in integration.health_detail


# ── Test 4 — Cursor doesn't advance on empty fetch ─────────────


@pytest.mark.asyncio
async def test_cursor_does_not_advance_on_empty_fetch(env) -> None:
    """Defensive guard: a transient empty response from the
    provider must not stomp a known-good cursor. The Stage 09
    skew change must not regress this."""
    db = env["db"]
    stub = env["stub"]
    integration_id = env["integration_id"]
    make_manager = env["make_manager"]

    base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="seed",
            source_path="/mnt/media/Movies/a.mkv",
            decision="direct_play",
            started_at=base,
        )
    ]
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        await poller.poll_one(integration)

    # Read the cursor after the seed poll.
    async with db.session() as session:
        cursor_v1 = (
            await session.execute(
                select(IntegrationPollingCursor.cursor_value).where(
                    IntegrationPollingCursor.integration_id == integration_id
                )
            )
        ).scalar_one()

    # Empty batch.
    stub.next_batch = []
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=env["bus"],
        )
        await poller.poll_one(integration)

    async with db.session() as session:
        cursor_v2 = (
            await session.execute(
                select(IntegrationPollingCursor.cursor_value).where(
                    IntegrationPollingCursor.integration_id == integration_id
                )
            )
        ).scalar_one()
    assert cursor_v1 == cursor_v2
