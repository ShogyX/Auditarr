"""v1.9 Stage 6.3 — live + history merge.

The poller's history fetch only sees sessions that crossed
upstream's "watched" threshold (Plex default ~90%; Jellyfin
similar). Stage 6.3 adds a second pass: ``fetch_live_playbacks``
is called, and live sessions that have crossed Auditarr's
"completed enough" threshold (>= 30s elapsed OR >= 90% progress)
are synthesized as PlaybackEvent rows with upstream_id
``live:<session_id>``.

Pins:
  1. Live session past 30s threshold synthesized as a
     PlaybackEvent.
  2. Live session past 90% progress threshold synthesized
     even if elapsed < 30s.
  3. Live session that fails both thresholds is NOT synthesized.
  4. Synthetic rows dedupe across consecutive polls — second
     poll of the same live session doesn't double-insert.
  5. Path mappings are applied to live session paths before
     persisting (synthesized rows sit in Auditarr-side path
     space, same as history events).
  6. Provider without ``fetch_live_playbacks`` is a no-op
     (back-compat).
  7. Provider that raises in ``fetch_live_playbacks`` doesn't
     abort the history portion of the poll.
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
    LivePlaybackDTO,
    PlaybackEventDTO,
    TagSync,
)
from app.models.integration import Integration
from app.models.library import Library
from app.models.playback import PlaybackEvent
from app.security.secrets import get_secret_box
from app.services.playback import PlaybackPoller
from app.storage.base import Base
from app.storage.database import get_database


class _StubProvider:
    kind = "stubplex63"
    label = "Stub"
    config_schema: dict = {"type": "object", "properties": {}}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.history: list[PlaybackEventDTO] = []
        self.live: list[LivePlaybackDTO] = []
        self.live_raises: Exception | None = None
        self.live_implemented = True

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
        return list(self.history)

    async def fetch_live_playbacks(
        self, _config: IntegrationConfig
    ) -> list[LivePlaybackDTO]:
        if not self.live_implemented:
            raise NotImplementedError
        if self.live_raises is not None:
            raise self.live_raises
        return list(self.live)


class _ProviderWithoutLive:
    """A provider that lacks ``fetch_live_playbacks`` entirely —
    exercises the hasattr-skip path."""

    kind = "stubplex63b"
    label = "Stub no-live"
    config_schema: dict = {"type": "object", "properties": {}}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.history: list[PlaybackEventDTO] = []

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
        return list(self.history)


@pytest_asyncio.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "live_merge.db"
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

    async with db.session() as sess:
        lib = Library(name="L", root_path="/mnt/media", kind="movies")
        sess.add(lib)
        await sess.flush()
        integration = Integration(
            name="Stub",
            kind="stubplex63",
            enabled=True,
            poll_interval_seconds=900,
            config={
                "base_url": "http://stub/",
                "path_mappings": [
                    {"from": "/upstream", "to": "/mnt/media"},
                ],
            },
            health_status="unknown",
        )
        sess.add(integration)
        await sess.commit()
        integration_id = integration.id

    from app.core.registry import get_registry

    registry = get_registry()
    bus = get_event_bus()
    stub = _StubProvider()
    registry.register_capability("integration.stubplex63", stub)

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
    }

    registry.clear()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


async def _run_poll(env_dict) -> None:
    db = env_dict["db"]
    make_manager = env_dict["make_manager"]
    async with db.session() as sess:
        manager = make_manager(sess)
        poller = PlaybackPoller(session=sess, manager=manager)
        integration = (
            await sess.execute(
                select(Integration).where(
                    Integration.id == env_dict["integration_id"]
                )
            )
        ).scalar_one()
        await poller.poll_one(integration)
        await sess.commit()


async def _events_for(env_dict) -> list[PlaybackEvent]:
    db = env_dict["db"]
    async with db.session() as sess:
        return (
            (
                await sess.execute(
                    select(PlaybackEvent).where(
                        PlaybackEvent.integration_id
                        == env_dict["integration_id"]
                    )
                )
            )
            .scalars()
            .all()
        )


def _live(
    *,
    upstream_id: str,
    elapsed_seconds: float | None = None,
    progress_pct: float | None = None,
    source_path: str = "/upstream/x.mkv",
    decision: str = "transcode",
) -> LivePlaybackDTO:
    """Build a LivePlaybackDTO whose ``started_at`` lines up
    with the requested elapsed time (helps tests express
    threshold cases declaratively)."""
    started = _dt.datetime.now(_dt.UTC)
    if elapsed_seconds is not None:
        started -= _dt.timedelta(seconds=elapsed_seconds)
    return LivePlaybackDTO(
        upstream_id=upstream_id,
        source_path=source_path,
        decision=decision,
        started_at=started,
        progress_pct=progress_pct,
    )


# ── Threshold tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_past_30s_threshold_synthesized(env) -> None:
    """A live session playing for 45s with no progress_pct
    information (Plex client kinds that don't report it) still
    qualifies via the elapsed-time floor."""
    env["stub"].live = [_live(upstream_id="s1", elapsed_seconds=45)]
    await _run_poll(env)
    events = await _events_for(env)
    assert len(events) == 1
    assert events[0].upstream_id == "live:s1"
    assert events[0].source_path == "/mnt/media/x.mkv"  # path mapped
    assert events[0].decision == "transcode"


@pytest.mark.asyncio
async def test_live_past_90pct_progress_synthesized(env) -> None:
    """A live session at 92% progress with only 5s elapsed
    (rare but possible for short content) still synthesizes —
    the 90% progress ceiling is the second eligibility branch."""
    env["stub"].live = [
        _live(upstream_id="s2", elapsed_seconds=5, progress_pct=92.0)
    ]
    await _run_poll(env)
    events = await _events_for(env)
    assert len(events) == 1
    assert events[0].upstream_id == "live:s2"


@pytest.mark.asyncio
async def test_live_below_both_thresholds_not_synthesized(env) -> None:
    """A live session at 5s elapsed and 10% progress is too
    brief to count — operator just hit play, didn't actually
    watch anything yet."""
    env["stub"].live = [
        _live(upstream_id="s3", elapsed_seconds=5, progress_pct=10.0)
    ]
    await _run_poll(env)
    events = await _events_for(env)
    assert events == []


# ── Dedup across polls ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthetic_dedup_across_polls(env) -> None:
    """The same live session id reported on two consecutive
    polls must not double-insert — the unique constraint on
    ``(integration_id, upstream_id)`` and the pre-insert IN
    query both protect against this."""
    env["stub"].live = [_live(upstream_id="s4", elapsed_seconds=60)]
    await _run_poll(env)
    events = await _events_for(env)
    assert len(events) == 1

    # Second poll, same live session, slightly more elapsed.
    env["stub"].live = [_live(upstream_id="s4", elapsed_seconds=120)]
    await _run_poll(env)
    events = await _events_for(env)
    # Still one row.
    assert len(events) == 1


# ── Robustness ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_and_live_combined_in_one_poll(env) -> None:
    """Both branches fire in one poll: a history event AND a
    live session that crossed threshold. Both rows persist."""
    env["stub"].history = [
        PlaybackEventDTO(
            upstream_id="hist-1",
            source_path="/upstream/y.mkv",
            decision="direct_play",
            started_at=_dt.datetime(2026, 5, 17, 10, 0, tzinfo=_dt.UTC),
        )
    ]
    env["stub"].live = [_live(upstream_id="s5", elapsed_seconds=60)]
    await _run_poll(env)
    events = await _events_for(env)
    ids = sorted(e.upstream_id for e in events)
    assert ids == ["hist-1", "live:s5"]


@pytest.mark.asyncio
async def test_provider_raise_in_live_does_not_abort_history(env) -> None:
    """The live pass is best-effort. A history event present in
    the same poll must still persist if ``fetch_live_playbacks``
    raises an exception."""
    env["stub"].history = [
        PlaybackEventDTO(
            upstream_id="hist-2",
            source_path="/upstream/z.mkv",
            decision="direct_play",
            started_at=_dt.datetime(2026, 5, 17, 11, 0, tzinfo=_dt.UTC),
        )
    ]
    env["stub"].live_raises = RuntimeError("upstream is on fire")
    await _run_poll(env)
    events = await _events_for(env)
    ids = [e.upstream_id for e in events]
    # History row landed; no synthetic row for live.
    assert ids == ["hist-2"]


@pytest.mark.asyncio
async def test_provider_without_live_method_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that doesn't implement ``fetch_live_playbacks``
    (older plugin version, kind that doesn't expose live data)
    silently skips the live pass."""
    db_path = tmp_path / "live_merge_noimpl.db"
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

    async with db.session() as sess:
        lib = Library(name="L", root_path="/mnt/media", kind="movies")
        sess.add(lib)
        await sess.flush()
        integration = Integration(
            name="Stub no-live",
            kind="stubplex63b",
            enabled=True,
            poll_interval_seconds=900,
            config={"base_url": "http://stub/"},
            health_status="unknown",
        )
        sess.add(integration)
        await sess.commit()
        integration_id = integration.id

    from app.core.registry import get_registry

    registry = get_registry()
    bus = get_event_bus()
    stub = _ProviderWithoutLive()
    registry.register_capability("integration.stubplex63b", stub)

    async with db.session() as sess:
        manager = IntegrationManager(
            session=sess,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )
        poller = PlaybackPoller(session=sess, manager=manager)
        integ = (
            await sess.execute(
                select(Integration).where(Integration.id == integration_id)
            )
        ).scalar_one()
        outcome = await poller.poll_one(integ)
        await sess.commit()
    # No history, no live, no rows, no error.
    assert outcome.error is None
    async with db.session() as sess:
        events = (
            (
                await sess.execute(
                    select(PlaybackEvent).where(
                        PlaybackEvent.integration_id == integration_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert events == []

    registry.clear()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()
