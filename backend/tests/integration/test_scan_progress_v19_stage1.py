"""Scanner progress cadence (v1.9 Stage 1.1).

Pins the v1.9 contract for ``scan.progress`` emissions, refining the
Stage 8 contract:

  1. ``PROGRESS_EVERY`` is 25 (down from Stage 8's 100).
  2. The initial enumerate emit fires before any per-file work, so the
     UI's progress bar has a denominator on first paint.
  3. Across a synthetic 80-file library, the scanner emits at least
     3 ``scan.progress`` events (initial + at least 25 + final),
     with monotonically non-decreasing ``files_seen``.
  4. The payload shape is exactly the four keys the UI hook reads.

The heartbeat (every 5 s when ``seen`` hasn't crossed a modulo
boundary) is intentionally NOT exercised here — it requires either
clock-mocking or a real slow probe, both of which add test fragility
for a property the modulo path already covers (the heartbeat fires
the same payload shape, just on a different trigger).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus
from app.events.types import SCAN_PROGRESS
from app.models.library import Library
from app.services.media import FfprobeResult
from app.services.media.scanner import ScanOptions, Scanner
from app.services.repositories import LibraryRepository
from app.storage.base import Base
from app.storage.database import get_database


class _StubFfprobe:
    """Lightweight ffprobe stub — matches the shape used by Stage 8."""

    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        return FfprobeResult(ok=True, container="matroska", video_codec="h264")


@pytest_asyncio.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Per-test DB session backed by a tmp_path sqlite file."""
    db_path = tmp_path / "v19s1_scan.db"
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

    sess = db._sessionmaker()  # type: ignore[misc]
    try:
        yield sess
    finally:
        await sess.close()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        get_settings.cache_clear()


def _seed_files(root: Path, count: int) -> None:
    (root / "lib").mkdir()
    for i in range(count):
        (root / "lib" / f"file_{i:04d}.mkv").write_bytes(b"x" * 10)


@pytest.mark.asyncio
async def test_scan_progress_emits_at_least_three_events_for_80_files(
    session: AsyncSession, tmp_path: Path
) -> None:
    """An 80-file library should emit:
      * initial enumerate event (seen=0)
      * at least one modulo event (25, 50, 75)
      * final flush (seen=80)

    Total: at least 5 events with the v1.9 cadence of 25. The plan
    asks for ``>= 3`` so we keep the assertion loose against the
    plan's wording, but in practice the count is 5.
    """
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=80)

    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    events: list[dict[str, object]] = []
    bus.subscribe(
        SCAN_PROGRESS,
        lambda e: events.append(dict(getattr(e, "payload", {}))),
    )
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=_StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    assert len(events) >= 3, (
        f"expected at least 3 scan.progress events on an 80-file scan; "
        f"got {len(events)}"
    )
    # Initial emit: seen=0, denominator already known.
    assert events[0]["files_seen"] == 0
    assert events[0]["files_total_estimate"] == 80
    # Final emit: seen reflects the total.
    assert events[-1]["files_seen"] == 80
    # Monotonic non-decrease — never goes backward.
    seens = [e["files_seen"] for e in events]
    assert seens == sorted(seens), (
        f"files_seen must be monotonically non-decreasing; got {seens}"
    )


@pytest.mark.asyncio
async def test_scan_progress_modulo_is_25_not_100(
    session: AsyncSession, tmp_path: Path
) -> None:
    """v1.9 Stage 1 dropped PROGRESS_EVERY from 100 to 25. Verify by
    scanning 50 files: the Stage 8 cadence of 100 would emit only
    the initial and final events (2 total). The v1.9 cadence of 25
    emits initial + at 25 + at 50 (final flush) = 3 distinct
    ``files_seen`` values."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=50)

    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    events: list[dict[str, object]] = []
    bus.subscribe(
        SCAN_PROGRESS,
        lambda e: events.append(dict(getattr(e, "payload", {}))),
    )
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=_StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    # Distinct seen values observed across all emits.
    distinct_seens = {e["files_seen"] for e in events}
    # With cadence=25 over 50 files we must observe at least
    # {0, 25, 50}. Stage 8's cadence=100 would only have {0, 50}.
    assert {0, 25, 50}.issubset(distinct_seens), (
        f"expected at least one emit per 25 files; got distinct "
        f"files_seen values: {sorted(distinct_seens)}"
    )


@pytest.mark.asyncio
async def test_scan_progress_payload_shape_unchanged(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Each emit carries the four keys the UI hook reads. v1.9 must
    NOT add new fields here — the hook is shape-strict."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=3)
    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    events: list[dict[str, object]] = []
    bus.subscribe(
        SCAN_PROGRESS,
        lambda e: events.append(dict(getattr(e, "payload", {}))),
    )
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=_StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    for e in events:
        assert set(e.keys()) == {
            "run_id",
            "library_id",
            "files_seen",
            "files_total_estimate",
        }, f"unexpected keys in payload: {sorted(e.keys())}"
