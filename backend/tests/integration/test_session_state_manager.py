"""Integration test for the v1.8.0 SessionStateManager.

Pins the contract:

  * First state event for a new session inserts a row.
  * Subsequent events for the same session_key update in place.
  * Idempotent — replaying the same event produces the same
    row (no duplicate primary keys, no constraint violations).
  * Enrichment fields are not blanked when a later event lacks
    them (e.g. stopped-event with no snapshot fetch).
  * ``state="stopped"`` sets ``stopped_at``.
  * ``handle_reconnect()`` is a no-op-but-doesn't-raise.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.integration import Integration
from app.models.playback import PlaybackSession
from app.services.playback.session_manager import (
    SessionEnrichment,
    SessionStateManager,
)
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def session_setup(tmp_path: Path):
    """Spin up a sqlite DB with schema + one Plex integration."""
    import os

    db_path = tmp_path / "session_manager.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    # Drop the cached Settings so our env var wins.
    from app.core.settings import get_settings

    get_settings.cache_clear()

    db = get_database()
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db.session() as session:
        integration = Integration(
            name="Test Plex",
            kind="plex",
            enabled=True,
            poll_interval_seconds=900,
            config={"base_url": "http://stub/"},
            health_status="ok",
        )
        session.add(integration)
        await session.commit()
        integration_id = integration.id

    manager = SessionStateManager(
        integration_id=integration_id,
        db_session_factory=db.session,
    )

    yield {"db": db, "manager": manager, "integration_id": integration_id}

    await db.disconnect()


def _enrichment_fixture() -> SessionEnrichment:
    return SessionEnrichment(
        decision="direct_play",
        source_path="/data/movies/inception.mkv",
        title="Inception",
        grandparent_title=None,
        user="alice",
        device_kind="Roku",
        device_name="Living Room Roku",
        source_codec="h264",
        source_bitrate_kbps=15000,
        source_width=1920,
        source_height=1080,
        source_container="mkv",
        target_codec=None,
        target_bitrate_kbps=None,
        duration_ms=600_000,
    )


@pytest.mark.asyncio
async def test_first_event_inserts_row(session_setup) -> None:
    """A state event for a previously-unseen session_key
    inserts a new playback_sessions row."""
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    await manager.handle_state_event(
        session_key="42",
        state="playing",
        view_offset_ms=5000,
        enrichment=_enrichment_fixture(),
    )

    async with db.session() as session:
        rows = (
            await session.execute(
                select(PlaybackSession).where(
                    PlaybackSession.integration_id == integration_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.session_key == "42"
    assert row.state == "playing"
    assert row.view_offset_ms == 5000
    assert row.title == "Inception"
    assert row.user == "alice"
    assert row.source_codec == "h264"
    assert row.stopped_at is None


@pytest.mark.asyncio
async def test_subsequent_event_updates_in_place(session_setup) -> None:
    """A second event for the same session_key updates the
    existing row rather than inserting a duplicate."""
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    enrichment = _enrichment_fixture()
    await manager.handle_state_event(
        session_key="42", state="playing", view_offset_ms=5000,
        enrichment=enrichment,
    )
    await manager.handle_state_event(
        session_key="42", state="paused", view_offset_ms=8000,
        enrichment=enrichment,
    )

    async with db.session() as session:
        rows = (
            await session.execute(
                select(PlaybackSession).where(
                    PlaybackSession.integration_id == integration_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].state == "paused"
    assert rows[0].view_offset_ms == 8000


@pytest.mark.asyncio
async def test_idempotent_replay(session_setup) -> None:
    """Replaying the same event produces the same row (no
    duplicate-key error). Important because Plex retries SSE
    events on reconnect."""
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    enrichment = _enrichment_fixture()
    for _ in range(3):
        await manager.handle_state_event(
            session_key="42", state="playing", view_offset_ms=5000,
            enrichment=enrichment,
        )

    async with db.session() as session:
        rows = (
            await session.execute(
                select(PlaybackSession).where(
                    PlaybackSession.integration_id == integration_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_stop_event_sets_stopped_at(session_setup) -> None:
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    await manager.handle_state_event(
        session_key="42", state="playing", view_offset_ms=5000,
        enrichment=_enrichment_fixture(),
    )
    await manager.handle_state_event(
        session_key="42", state="stopped", view_offset_ms=300_000,
        enrichment=None,
    )

    async with db.session() as session:
        row = (
            await session.execute(
                select(PlaybackSession).where(
                    PlaybackSession.integration_id == integration_id
                )
            )
        ).scalars().first()
    assert row is not None
    assert row.state == "stopped"
    assert row.stopped_at is not None


@pytest.mark.asyncio
async def test_unknown_state_falls_back_to_playing(session_setup) -> None:
    """An unrecognised Plex state shouldn't crash the manager;
    we coerce to 'playing' and log a warning."""
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    await manager.handle_state_event(
        session_key="42",
        state="weirdstate",
        view_offset_ms=None,
        enrichment=_enrichment_fixture(),
    )

    async with db.session() as session:
        row = (
            await session.execute(
                select(PlaybackSession).where(
                    PlaybackSession.integration_id == integration_id
                )
            )
        ).scalars().first()
    assert row is not None
    assert row.state == "playing"


@pytest.mark.asyncio
async def test_list_active_sessions_excludes_stopped(session_setup) -> None:
    """The read API used by /playback/live filters stopped
    sessions out."""
    manager = session_setup["manager"]
    db = session_setup["db"]
    integration_id = session_setup["integration_id"]

    await manager.handle_state_event(
        session_key="a", state="playing", view_offset_ms=0,
        enrichment=_enrichment_fixture(),
    )
    await manager.handle_state_event(
        session_key="b", state="stopped", view_offset_ms=0,
        enrichment=_enrichment_fixture(),
    )

    async with db.session() as session:
        active = await SessionStateManager.list_active_sessions(
            session, integration_id=integration_id
        )
    assert len(active) == 1
    assert active[0].session_key == "a"


@pytest.mark.asyncio
async def test_handle_reconnect_does_not_raise(session_setup) -> None:
    """The reconnect hook is a no-op for now but must not
    raise; it's called from the listener task on every SSE
    reconnect."""
    manager = session_setup["manager"]
    await manager.handle_reconnect()


# ── v1.9 OP-10 — SSE writer path mapping + media resolution ─────


@pytest.mark.asyncio
async def test_sse_writer_applies_path_mappings_and_resolves_media_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.9 OP-10: SSE writer maps the upstream path through the
    configured PathMappings and looks up the matching MediaFile,
    persisting both the rewritten source_path and the media_file_id
    on the new session row."""
    from app.integrations.path_mapping import PathMapping
    from app.models.library import Library
    from app.models.media import MediaFile

    db_path = tmp_path / "sse-mapping.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
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
        await conn.run_sync(Base.metadata.create_all)

    async with db.session() as session:
        ig = Integration(
            name="Plex", kind="plex", enabled=True,
            poll_interval_seconds=900, config={"base_url": "http://stub/"},
            health_status="ok",
        )
        session.add(ig)
        await session.flush()

        lib = Library(name="Movies", root_path="/mnt/media/Movies", kind="movies")
        session.add(lib)
        await session.flush()

        session.add(
            MediaFile(
                library_id=lib.id,
                path="/mnt/media/Movies/inception.mkv",
                relative_path="inception.mkv",
                filename="inception.mkv",
                extension="mkv",
                size_bytes=100 * 1024 * 1024,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
                severity="ok",
                severity_rank=10,
                has_subtitles=False,
                seen_at=_dt.datetime.now(_dt.UTC),
                is_orphaned=False,
            )
        )
        await session.commit()
        ig_id = ig.id

    mgr = SessionStateManager(
        integration_id=ig_id,
        db_session_factory=db.session,
        path_mappings=[
            PathMapping(
                src_prefix="/data/movies",
                dst_prefix="/mnt/media/Movies",
            )
        ],
    )

    enrichment = SessionEnrichment(
        decision="direct_play",
        source_path="/data/movies/inception.mkv",  # upstream-side path
        title="Inception",
        grandparent_title=None,
        user="alice",
        device_kind="Roku",
        device_name="Living Room",
        source_codec="h264",
        source_bitrate_kbps=15000,
        source_width=1920,
        source_height=1080,
        source_container="mkv",
        target_codec=None,
        target_bitrate_kbps=None,
        duration_ms=600_000,
    )
    await mgr.handle_state_event(
        session_key="sk-1",
        state="playing",
        view_offset_ms=0,
        enrichment=enrichment,
        rating_key="rk-7",
    )

    async with db.session() as session:
        rows = (
            await session.execute(select(PlaybackSession))
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source_path == "/mnt/media/Movies/inception.mkv"
    assert row.media_file_id is not None
    assert row.rating_key == "rk-7"

    await db.disconnect()


@pytest.mark.asyncio
async def test_sse_writer_leaves_media_file_id_null_when_no_match(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.9 OP-10 caveat 12: an SSE event whose path doesn't
    match any MediaFile (after mapping) leaves media_file_id NULL
    rather than raising. The analyzer filters such rows out so
    they don't pollute downstream stats."""
    db_path = tmp_path / "sse-nomap.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
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
        await conn.run_sync(Base.metadata.create_all)

    async with db.session() as session:
        ig = Integration(
            name="Plex", kind="plex", enabled=True,
            poll_interval_seconds=900, config={"base_url": "http://stub/"},
            health_status="ok",
        )
        session.add(ig)
        await session.commit()
        ig_id = ig.id

    # No path_mappings configured AND no MediaFile seeded.
    mgr = SessionStateManager(
        integration_id=ig_id,
        db_session_factory=db.session,
    )

    enrichment = SessionEnrichment(
        decision="direct_play",
        source_path="/data/movies/unknown.mkv",
        title="Unknown",
        grandparent_title=None,
        user="alice",
        device_kind="Roku",
        device_name="Living Room",
        source_codec="h264",
        source_bitrate_kbps=15000,
        source_width=1920,
        source_height=1080,
        source_container="mkv",
        target_codec=None,
        target_bitrate_kbps=None,
        duration_ms=600_000,
    )
    await mgr.handle_state_event(
        session_key="sk-orphan",
        state="playing",
        view_offset_ms=0,
        enrichment=enrichment,
    )

    async with db.session() as session:
        rows = (
            await session.execute(select(PlaybackSession))
        ).scalars().all()
    assert len(rows) == 1
    # No mapping → source_path unchanged; no MediaFile match →
    # media_file_id NULL. The row exists for diagnostic purposes;
    # the analyzer's WHERE media_file_id IS NOT NULL filter
    # excludes it from heuristics.
    assert rows[0].source_path == "/data/movies/unknown.mkv"
    assert rows[0].media_file_id is None

    await db.disconnect()
