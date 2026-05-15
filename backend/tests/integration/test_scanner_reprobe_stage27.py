"""Stage 27 — Scanner.reprobe_one tests.

Covers the per-file re-probe entrypoint:

  - Successful reprobe overwrites probe columns and clears
    ``probe_failed`` / ``probe_error``.
  - Failed reprobe sets ``probe_failed`` but preserves prior probe
    data (some data is better than none).
  - Reprobing a file whose path no longer exists marks it
    ``is_orphaned`` rather than 404ing or wiping the row.
  - ``seen_at`` is bumped on every reprobe attempt regardless of
    outcome (so the next library scan doesn't mis-orphan it).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus
from app.models.library import Library
from app.models.media import MediaFile
from app.services.media import FfprobeResult
from app.services.media.scanner import Scanner
from app.services.repositories import LibraryRepository, MediaRepository
from app.storage.base import Base
from app.storage.database import get_database
from app.utils.datetime import utcnow


class StubFfprobe:
    """Configurable in-process FfprobeService.

    ``results`` maps absolute path → :class:`FfprobeResult`. Anything
    not in the map returns an "ok=False" result so we can test the
    failure branch without rigging filesystem-specific behavior.
    """

    def __init__(self, results: dict[str, FfprobeResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        self.calls.append(path)
        if path in self._results:
            return self._results[path]
        return FfprobeResult(ok=False, error="not configured in stub")


@pytest_asyncio.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "scanner_stage27.db"
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
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with db.session() as sess:
        yield sess

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_reprobe_one_overwrites_probe_columns_on_success(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The canonical happy path: file exists, ffprobe succeeds,
    probe columns reflect the new data."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    media_path = tmp_path / "a.mkv"
    media_path.write_bytes(b"x" * 100)

    mf = MediaFile(
        library_id=library.id,
        path=str(media_path),
        relative_path="a.mkv",
        filename="a.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        # Stale probe data we expect to be overwritten:
        container="mp4",
        video_codec="hevc",
        audio_codec="opus",
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=False,
    )
    session.add(mf)
    await session.commit()

    stub = StubFfprobe(
        results={
            str(media_path): FfprobeResult(
                ok=True,
                container="matroska",
                video_codec="av1",
                audio_codec="aac",
                width=1280,
                height=720,
                duration_seconds=30.0,
            )
        }
    )
    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=stub,  # type: ignore[arg-type]
    )
    await scanner.reprobe_one(mf)
    await session.flush()

    assert mf.container == "matroska"
    assert mf.video_codec == "av1"
    assert mf.audio_codec == "aac"
    assert mf.width == 1280
    assert mf.height == 720
    assert mf.probe_failed is False
    assert mf.probe_error is None
    assert mf.is_orphaned is False
    # Confirm stub was actually called (no caching shortcut).
    assert str(media_path) in stub.calls


@pytest.mark.asyncio
async def test_reprobe_one_failed_probe_preserves_existing_data(
    session: AsyncSession, tmp_path: Path
) -> None:
    """If ffprobe fails on reprobe, we don't blow away the prior
    probe columns. Some data is better than no data."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    media_path = tmp_path / "b.mkv"
    media_path.write_bytes(b"x" * 100)

    mf = MediaFile(
        library_id=library.id,
        path=str(media_path),
        relative_path="b.mkv",
        filename="b.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        container="matroska",  # prior good data
        video_codec="h264",
        audio_codec="aac",
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=False,
    )
    session.add(mf)
    await session.commit()

    # Stub returns ok=False — the path is not configured.
    stub = StubFfprobe()
    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=stub,  # type: ignore[arg-type]
    )
    await scanner.reprobe_one(mf)
    await session.flush()

    # Prior probe columns preserved.
    assert mf.container == "matroska"
    assert mf.video_codec == "h264"
    # Failure flagged.
    assert mf.probe_failed is True
    assert mf.probe_error is not None


@pytest.mark.asyncio
async def test_reprobe_one_missing_file_marks_orphan(
    session: AsyncSession, tmp_path: Path
) -> None:
    """If the file path no longer exists on disk, we mark the row
    orphaned and skip the probe attempt."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    mf = MediaFile(
        library_id=library.id,
        path=str(tmp_path / "vanished.mkv"),
        relative_path="vanished.mkv",
        filename="vanished.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=False,
    )
    session.add(mf)
    await session.commit()

    stub = StubFfprobe()
    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=stub,  # type: ignore[arg-type]
    )
    await scanner.reprobe_one(mf)
    await session.flush()

    assert mf.is_orphaned is True
    # ffprobe was NOT called — saves IO when the file is gone.
    assert stub.calls == []


@pytest.mark.asyncio
async def test_reprobe_one_clears_orphan_when_file_reappears(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Reverse of the above: row was orphaned, file is now back,
    reprobe should clear the orphan flag and pick up data."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    media_path = tmp_path / "back.mkv"
    media_path.write_bytes(b"x" * 100)

    mf = MediaFile(
        library_id=library.id,
        path=str(media_path),
        relative_path="back.mkv",
        filename="back.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=True,  # was orphaned
    )
    session.add(mf)
    await session.commit()

    stub = StubFfprobe(
        results={
            str(media_path): FfprobeResult(
                ok=True, container="matroska", video_codec="h264"
            )
        }
    )
    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=stub,  # type: ignore[arg-type]
    )
    await scanner.reprobe_one(mf)
    await session.flush()

    assert mf.is_orphaned is False
    assert mf.video_codec == "h264"


@pytest.mark.asyncio
async def test_reprobe_one_bumps_seen_at(
    session: AsyncSession, tmp_path: Path
) -> None:
    """seen_at is bumped on every reprobe so a subsequent library
    scan doesn't mistake a freshly-reprobed file for an orphan."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    media_path = tmp_path / "c.mkv"
    media_path.write_bytes(b"x" * 100)

    import datetime as _dt

    old_seen = _dt.datetime(2020, 1, 1, tzinfo=_dt.UTC)
    mf = MediaFile(
        library_id=library.id,
        path=str(media_path),
        relative_path="c.mkv",
        filename="c.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        has_subtitles=False,
        seen_at=old_seen,
        is_orphaned=False,
    )
    session.add(mf)
    await session.commit()

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.reprobe_one(mf)
    await session.flush()

    assert mf.seen_at > old_seen


@pytest.mark.asyncio
async def test_reprobe_one_emits_event(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Reprobing emits ``media.reprobed`` so the dashboard and any
    listeners (job runs, telemetry) can pick up the action."""
    library = Library(name="L", root_path=str(tmp_path), kind="movies")
    await LibraryRepository(session).add(library)
    await session.flush()

    media_path = tmp_path / "d.mkv"
    media_path.write_bytes(b"x" * 100)
    mf = MediaFile(
        library_id=library.id,
        path=str(media_path),
        relative_path="d.mkv",
        filename="d.mkv",
        extension="mkv",
        size_bytes=100,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=False,
    )
    session.add(mf)
    await session.commit()

    events: list[dict] = []
    bus = EventBus()
    bus.subscribe("media.reprobed", lambda e: events.append(dict(e.payload)))

    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=StubFfprobe(  # type: ignore[arg-type]
            results={
                str(media_path): FfprobeResult(ok=True, container="matroska")
            }
        ),
    )
    await scanner.reprobe_one(mf)

    # Give the event bus a moment to dispatch (it's async).
    import asyncio

    await asyncio.sleep(0)

    assert len(events) == 1
    assert events[0]["id"] == mf.id
    assert events[0]["ok"] is True
    assert events[0]["orphaned"] is False
