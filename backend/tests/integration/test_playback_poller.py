"""End-to-end test for the playback poller (Stage 16).

We bypass HTTP by registering a stub :class:`IntegrationProvider` whose
``fetch_playback_events`` returns a hand-crafted batch of DTOs. The
poller is then exercised against a real DB so we verify:

* events are persisted
* path mappings are applied
* unresolved paths are stored with ``media_file_id=None``
* the cursor advances to the latest started_at
* a drift report writes degraded health when most paths don't resolve
* a second poll with the same batch dedupes via the unique constraint
"""

from __future__ import annotations

import datetime as _dt
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
from app.storage.base import Base
from app.storage.database import get_database


# ── Stub provider ────────────────────────────────────────────
class StubProvider:
    """Implements just enough of IntegrationProvider for tests."""

    kind = "stubplex"
    label = "Stub"
    config_schema: dict = {"type": "object", "properties": {}}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.next_batch: list[PlaybackEventDTO] = []

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since: _dt.datetime | None
    ) -> list[PlaybackEventDTO]:
        return list(self.next_batch)


@pytest_asyncio.fixture
async def seeded_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up an isolated DB with one Library, three MediaFiles, one
    Integration (kind=stubplex), and a manager wired to a stub
    provider."""
    db_path = tmp_path / "playback.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars")

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Seed a library + three media files matching real Auditarr paths.
    async with db.session() as session:
        lib = Library(name="Movies", root_path="/mnt/media/Movies", kind="movies")
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
                    size_bytes=1024 * 1024 * 100,
                    mtime=_dt.datetime.now(_dt.UTC),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    has_subtitles=False,
                    seen_at=_dt.datetime.now(_dt.UTC),
                    is_orphaned=False,
                )
            )

        # Integration with one path mapping configured: Plex sees
        # /data/movies/* which we rewrite to /mnt/media/Movies/*.
        integration = Integration(
            name="Stub Plex",
            kind="stubplex",
            enabled=True,
            poll_interval_seconds=900,
            config={
                "base_url": "http://stub/",
                "path_mappings": [
                    {"from": "/data/movies", "to": "/mnt/media/Movies"}
                ],
            },
            health_status="unknown",
        )
        session.add(integration)
        await session.commit()
        integration_id = integration.id

    # Register the stub provider on the registry under the capability
    # key the manager looks up: ``integration.<kind>``.
    from app.core.registry import get_registry

    registry = get_registry()
    bus = get_event_bus()
    stub = StubProvider()
    registry.register_capability("integration.stubplex", stub)

    def _make_manager(session):
        return IntegrationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )

    yield {
        "db": db,
        "make_manager": _make_manager,
        "stub": stub,
        "integration_id": integration_id,
        "bus": bus,
    }

    # Teardown — registry has no per-capability removal, so reset it.
    registry.clear()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_poller_inserts_events_and_remaps_paths(seeded_env) -> None:
    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]

    now = _dt.datetime.now(_dt.UTC)
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="evt1",
            source_path="/data/movies/a.mkv",  # will be remapped
            decision="direct_play",
            started_at=now - _dt.timedelta(minutes=10),
        ),
        PlaybackEventDTO(
            upstream_id="evt2",
            source_path="/data/movies/b.mkv",
            decision="transcode",
            reason_code="video.codec.unsupported",
            started_at=now - _dt.timedelta(minutes=5),
            device_kind="Roku",
            source_codec="hevc",
        ),
    ]

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        outcome = await poller.poll_one(integration)

    assert outcome.fetched == 2
    assert outcome.inserted == 2
    assert outcome.resolved == 2  # both paths matched after remap
    assert outcome.unresolved == 0
    assert outcome.drift_suspected is False

    # Verify rows landed with remapped paths + media_file_id set.
    async with db.session() as session:
        rows = (await session.execute(select(PlaybackEvent))).scalars().all()
    assert len(rows) == 2
    paths = {r.source_path for r in rows}
    assert paths == {
        "/mnt/media/Movies/a.mkv",
        "/mnt/media/Movies/b.mkv",
    }
    assert all(r.media_file_id is not None for r in rows)


@pytest.mark.asyncio
async def test_poller_deduplicates_via_unique_constraint(seeded_env) -> None:
    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]
    now = _dt.datetime.now(_dt.UTC)

    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="evt-dup",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=now,
        )
    ]

    # First poll inserts.
    async with db.session() as session:
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        integration = await session.get(Integration, integration_id)
        outcome1 = await poller.poll_one(integration)
    assert outcome1.inserted == 1

    # Second poll with the *same* upstream_id should insert 0.
    async with db.session() as session:
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        integration = await session.get(Integration, integration_id)
        outcome2 = await poller.poll_one(integration)
    assert outcome2.fetched == 1
    assert outcome2.inserted == 0

    # Still only one row exists.
    async with db.session() as session:
        rows = (await session.execute(select(PlaybackEvent))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_poller_records_unresolved_paths_with_null_media(seeded_env) -> None:
    """When a path doesn't match any indexed MediaFile, store it
    anyway with media_file_id=None so drift detection can see it."""
    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]
    now = _dt.datetime.now(_dt.UTC)

    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="evt-unknown",
            source_path="/data/movies/never-indexed.mkv",
            decision="transcode",
            started_at=now,
        )
    ]

    async with db.session() as session:
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        integration = await session.get(Integration, integration_id)
        outcome = await poller.poll_one(integration)
    assert outcome.fetched == 1
    assert outcome.inserted == 1
    assert outcome.resolved == 0
    assert outcome.unresolved == 1

    async with db.session() as session:
        row = (await session.execute(select(PlaybackEvent))).scalar_one()
    assert row.source_path == "/mnt/media/Movies/never-indexed.mkv"
    assert row.media_file_id is None


@pytest.mark.asyncio
async def test_poller_flags_drift_when_many_paths_unresolved(seeded_env) -> None:
    """Drift detection should fire when most paths don't resolve."""
    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]
    now = _dt.datetime.now(_dt.UTC)

    # 8 unresolved + 2 resolved → 80% drift, well over threshold.
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id=f"evt-drift-{i}",
            source_path=f"/wrong/prefix/file-{i}.mkv",
            decision="direct_play",
            started_at=now - _dt.timedelta(minutes=i),
        )
        for i in range(8)
    ] + [
        PlaybackEventDTO(
            upstream_id="evt-ok-1",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=now,
        ),
        PlaybackEventDTO(
            upstream_id="evt-ok-2",
            source_path="/data/movies/b.mkv",
            decision="direct_play",
            started_at=now,
        ),
    ]

    async with db.session() as session:
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        integration = await session.get(Integration, integration_id)
        outcome = await poller.poll_one(integration)

    assert outcome.fetched == 10
    assert outcome.resolved == 2
    assert outcome.drift_suspected is True

    # Verify the integration was marked degraded with a useful detail.
    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        assert integration.health_status == "degraded"
        assert integration.health_detail is not None
        assert "don't resolve" in integration.health_detail


@pytest.mark.asyncio
async def test_poller_advances_cursor(seeded_env) -> None:
    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]
    now = _dt.datetime.now(_dt.UTC).replace(microsecond=0)

    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="evt-cursor-1",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=now - _dt.timedelta(minutes=10),
        ),
        PlaybackEventDTO(
            upstream_id="evt-cursor-2",
            source_path="/data/movies/b.mkv",
            decision="direct_play",
            started_at=now - _dt.timedelta(minutes=2),
        ),
    ]

    async with db.session() as session:
        poller = PlaybackPoller(session=session, manager=make_manager(session), event_bus=seeded_env["bus"])
        integration = await session.get(Integration, integration_id)
        await poller.poll_one(integration)

    async with db.session() as session:
        cursor = (
            await session.execute(
                select(IntegrationPollingCursor).where(
                    IntegrationPollingCursor.integration_id == integration_id,
                    IntegrationPollingCursor.cursor_kind == "playback_events",
                )
            )
        ).scalar_one()
    # Stage 09 (v1.7): the cursor advances to
    # ``max(started_at) − CURSOR_SAFETY_SKEW`` (60s) rather
    # than ``max(started_at)`` itself, so slightly-out-of-order
    # events arriving on the next poll aren't dropped. Replays
    # are harmless via the unique constraint dedup.
    parsed = _dt.datetime.fromisoformat(cursor.cursor_value)
    from app.services.playback.poller import CURSOR_SAFETY_SKEW

    assert parsed == (now - _dt.timedelta(minutes=2)) - CURSOR_SAFETY_SKEW


# ── v1.9 OP-10 — reconciliation tests ────────────────────────────


@pytest.mark.asyncio
async def test_reconciliation_matches_closest_session_in_window(seeded_env) -> None:
    """v1.9 OP-10 caveat 3: when two sessions share a rating_key
    within the ±5min reconciliation window, the poller picks the
    closest one by absolute |started_at - viewed_at|."""
    from app.models.playback import PlaybackSession

    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]

    target_time = _dt.datetime(2026, 5, 18, 12, 0, 0, tzinfo=_dt.UTC)

    # Seed two SSE sessions with the same rating_key: one 4 min
    # before target (further), one 1 min after target (closer).
    async with db.session() as session:
        far = PlaybackSession(
            integration_id=integration_id,
            session_key="sk-far",
            rating_key="rk-42",
            state="stopped",
            decision="direct_play",
            started_at=target_time - _dt.timedelta(minutes=4),
            last_event_at=target_time - _dt.timedelta(minutes=3),
        )
        near = PlaybackSession(
            integration_id=integration_id,
            session_key="sk-near",
            rating_key="rk-42",
            state="stopped",
            decision="direct_play",
            started_at=target_time + _dt.timedelta(minutes=1),
            last_event_at=target_time + _dt.timedelta(minutes=2),
        )
        session.add_all([far, near])
        await session.commit()
        await session.refresh(near)
        near_id = near.id

    # History DTO at target_time + rating_key=rk-42.
    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="hist-42",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=target_time,
            rating_key="rk-42",
        )
    ]

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=seeded_env["bus"],
        )
        await poller.poll_one(integration)

    # Verify the CLOSER session was the one marked reconciled,
    # and the event row got tagged with its id (caveat 4: insert
    # preserved for diagnosability).
    async with db.session() as session:
        sessions = (
            await session.execute(select(PlaybackSession))
        ).scalars().all()
        events = (
            await session.execute(select(PlaybackEvent))
        ).scalars().all()

    by_key = {s.session_key: s for s in sessions}
    assert by_key["sk-far"].reconciled_with_history is False
    assert by_key["sk-near"].reconciled_with_history is True
    assert len(events) == 1
    assert events[0].reconciled_with_session_id == near_id


@pytest.mark.asyncio
async def test_reconciliation_skips_when_rating_key_null(seeded_env) -> None:
    """v1.9 OP-10 caveat 11: a DTO with rating_key=None (Jellyfin
    shape) must NEVER match a session — even one whose rating_key
    is also None — to prevent cross-provider accidental joins."""
    from app.models.playback import PlaybackSession

    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]

    target_time = _dt.datetime(2026, 5, 18, 13, 0, 0, tzinfo=_dt.UTC)

    async with db.session() as session:
        session.add(
            PlaybackSession(
                integration_id=integration_id,
                session_key="sk-jelly",
                rating_key=None,  # Jellyfin SSE row.
                state="stopped",
                decision="direct_play",
                started_at=target_time,
                last_event_at=target_time,
            )
        )
        await session.commit()

    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="hist-no-rk",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=target_time,
            rating_key=None,
        )
    ]

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=seeded_env["bus"],
        )
        await poller.poll_one(integration)

    async with db.session() as session:
        sessions = (
            await session.execute(select(PlaybackSession))
        ).scalars().all()
        events = (
            await session.execute(select(PlaybackEvent))
        ).scalars().all()

    assert all(s.reconciled_with_history is False for s in sessions)
    assert len(events) == 1
    assert events[0].reconciled_with_session_id is None


@pytest.mark.asyncio
async def test_reconciliation_preserves_event_row(seeded_env) -> None:
    """v1.9 OP-10 caveat 4: even when a session matches, the
    PlaybackEvent row is still INSERTED (tagged with the session
    id). This preserves diagnosability — if reconciliation is
    wrong, operators can inspect the event row."""
    from app.models.playback import PlaybackSession

    db = seeded_env["db"]
    make_manager = seeded_env["make_manager"]
    stub = seeded_env["stub"]
    integration_id = seeded_env["integration_id"]

    target_time = _dt.datetime(2026, 5, 18, 14, 0, 0, tzinfo=_dt.UTC)

    async with db.session() as session:
        sess = PlaybackSession(
            integration_id=integration_id,
            session_key="sk-preserved",
            rating_key="rk-99",
            state="stopped",
            decision="direct_play",
            started_at=target_time,
            last_event_at=target_time + _dt.timedelta(minutes=1),
        )
        session.add(sess)
        await session.commit()
        await session.refresh(sess)
        sess_id = sess.id

    stub.next_batch = [
        PlaybackEventDTO(
            upstream_id="hist-99",
            source_path="/data/movies/a.mkv",
            decision="direct_play",
            started_at=target_time,
            rating_key="rk-99",
        )
    ]

    async with db.session() as session:
        integration = await session.get(Integration, integration_id)
        poller = PlaybackPoller(
            session=session,
            manager=make_manager(session),
            event_bus=seeded_env["bus"],
        )
        outcome = await poller.poll_one(integration)

    # Event was inserted, not skipped.
    assert outcome.inserted == 1
    async with db.session() as session:
        events = (
            await session.execute(select(PlaybackEvent))
        ).scalars().all()
        sessions = (
            await session.execute(select(PlaybackSession))
        ).scalars().all()
    assert len(events) == 1
    assert events[0].reconciled_with_session_id == sess_id
    assert sessions[0].reconciled_with_history is True
