"""v1.9 Stage 6.2 — playback poller "unpolled" fix.

Pins the bug + fix:

Bug: ``IntegrationPollingCursor.updated_at`` is the source of
truth for the dashboard's "Last polled N ago" line. Before this
fix, ``updated_at`` was only written when ``_upsert_cursor`` was
called, which itself only fired when at least one event was
inserted. A quiet but healthy integration (no plays since the
last successful poll) appeared "unpolled" — the timestamp froze
at the time of the last event-bearing poll.

Fix: after every successful poll, the cursor's ``updated_at``
is touched regardless of whether events were inserted. The
``cursor_value`` is NOT advanced (that would slide the
watermark forward incorrectly when zero events arrived).

Tests:
  1. Zero-event poll on an integration with no prior cursor →
     a cursor row is created with empty ``cursor_value`` and
     ``updated_at`` set to now.
  2. Zero-event poll on an integration with a prior cursor →
     ``cursor_value`` unchanged, ``updated_at`` advances.
  3. After a zero-event poll seeds the cursor with empty
     value, a subsequent event-bearing poll writes the real
     watermark (the empty sentinel doesn't poison the
     ``since`` filter).
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
from app.models.playback import IntegrationPollingCursor, PlaybackEvent
from app.security.secrets import get_secret_box
from app.services.playback import PlaybackPoller
from app.storage.base import Base
from app.storage.database import get_database


class _StubProvider:
    """Minimal IntegrationProvider — returns whatever batch the
    test installs into ``next_batch``."""

    kind = "stubplex62"
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
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "poller_unpolled.db"
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
        lib = Library(
            name="L", root_path="/mnt/media", kind="movies"
        )
        sess.add(lib)
        await sess.flush()
        integration = Integration(
            name="Stub Plex",
            kind="stubplex62",
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
    stub = _StubProvider()
    registry.register_capability("integration.stubplex62", stub)

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
    """Run one poll cycle against the env's stub integration."""
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


async def _cursor_for(env_dict) -> IntegrationPollingCursor | None:
    db = env_dict["db"]
    async with db.session() as sess:
        return (
            await sess.execute(
                select(IntegrationPollingCursor).where(
                    IntegrationPollingCursor.integration_id
                    == env_dict["integration_id"]
                )
            )
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_zero_event_poll_creates_cursor_row_with_empty_value(
    env,
) -> None:
    """Before the fix, a poll with zero events did NOT create a
    cursor row — the upsert was gated on ``latest_started_at is
    not None``. After the fix, the row is seeded with empty
    ``cursor_value`` so the dashboard has an ``updated_at`` to
    show; the empty sentinel signals "first cursor, never had
    events"."""
    # No batch installed → fetch returns [].
    assert (await _cursor_for(env)) is None
    await _run_poll(env)
    row = await _cursor_for(env)
    assert row is not None
    assert row.cursor_value == ""
    # updated_at should be within the last few seconds. SQLite
    # returns timezone-naive datetimes — coerce to UTC-aware
    # for the subtraction.
    updated_at = row.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=_dt.UTC)
    age = _dt.datetime.now(_dt.UTC) - updated_at
    assert age < _dt.timedelta(seconds=10)


@pytest.mark.asyncio
async def test_zero_event_poll_touches_existing_cursor_updated_at(
    env,
) -> None:
    """The core "unpolled" bug. Seed a cursor with a real
    ``cursor_value`` and ``updated_at`` from "a while ago", run
    a poll with zero events, then assert ``updated_at`` moved
    forward while ``cursor_value`` stayed put."""
    db = env["db"]
    # SQLite stores tz-naive; seed naive so we can compare
    # later without surprises.
    old_ts = _dt.datetime(2026, 1, 1)
    cursor_value = "2026-01-01T00:00:00+00:00"
    async with db.session() as sess:
        sess.add(
            IntegrationPollingCursor(
                integration_id=env["integration_id"],
                cursor_kind="playback_events",
                cursor_value=cursor_value,
                updated_at=old_ts,
            )
        )
        await sess.commit()
    await _run_poll(env)
    row = await _cursor_for(env)
    assert row is not None
    # cursor_value untouched — zero events means the watermark
    # should NOT slide forward.
    assert row.cursor_value == cursor_value
    # updated_at moved from 2026-01-01 to "now". Compare in
    # naive-UTC space (SQLite strips tz info).
    updated_at = row.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=_dt.UTC)
    assert updated_at > old_ts.replace(tzinfo=_dt.UTC)
    age = _dt.datetime.now(_dt.UTC) - updated_at
    assert age < _dt.timedelta(seconds=10)


@pytest.mark.asyncio
async def test_empty_cursor_sentinel_does_not_poison_next_poll(
    env,
) -> None:
    """After a zero-event poll seeds the cursor with empty
    ``cursor_value``, a subsequent event-bearing poll must
    fetch full history (since=None) rather than treating the
    empty string as a stuck timestamp. The fix's ``_parse_cursor``
    returns None on empty input precisely for this case."""
    # First poll: zero events.
    await _run_poll(env)
    row = await _cursor_for(env)
    assert row is not None and row.cursor_value == ""

    # Now install a batch and re-poll.
    started = _dt.datetime(2026, 5, 17, 12, 0, 0, tzinfo=_dt.UTC)
    env["stub"].next_batch = [
        PlaybackEventDTO(
            upstream_id="ev-1",
            source_path="/mnt/media/x.mkv",
            decision="direct_play",
            started_at=started,
        )
    ]
    await _run_poll(env)

    # The event landed; the cursor advanced to a real timestamp.
    db = env["db"]
    async with db.session() as sess:
        events = (
            await sess.execute(
                select(PlaybackEvent).where(
                    PlaybackEvent.integration_id == env["integration_id"]
                )
            )
        ).scalars().all()
        assert len(events) == 1
    row = await _cursor_for(env)
    assert row is not None
    assert row.cursor_value != ""
    # The cursor value should parse cleanly as ISO datetime now.
    parsed = _dt.datetime.fromisoformat(row.cursor_value)
    # Cursor sits behind `started_at` by CURSOR_SAFETY_SKEW (60s).
    assert parsed < started
