"""Scanner integration tests.

We use a stub ``FfprobeService`` so the test suite doesn't depend on the
``ffprobe`` binary being installed on the runner. The real wrapper is
exercised end-to-end during manual deployment verification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus
from app.models.library import Library
from app.services.media import FfprobeResult
from app.services.media.scanner import ScanOptions, Scanner
from app.services.repositories import LibraryRepository, MediaRepository
from app.storage.base import Base
from app.storage.database import get_database


class StubFfprobe:
    """In-process replacement for :class:`FfprobeService`."""

    def __init__(self, results: dict[str, FfprobeResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        self.calls.append(path)
        return self._results.get(
            path,
            FfprobeResult(
                ok=True,
                container="matroska",
                video_codec="h264",
                audio_codec="aac",
                width=1920,
                height=1080,
                duration_seconds=42.0,
            ),
        )


def _seed(root: Path) -> None:
    (root / "Movies" / "Cool Movie (2020)").mkdir(parents=True)
    (root / "Movies" / "Cool Movie (2020)" / "movie.mkv").write_bytes(b"x" * 100)
    (root / "Movies" / "Cool Movie (2020)" / "movie.eng.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nhi\n"
    )
    (root / "Movies" / "Cool Movie (2020)" / "poster.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "Movies" / "Cool Movie (2020)" / "info.nfo").write_text("<nfo/>")
    (root / "Movies" / "Cool Movie (2020)" / "Thumbs.db").write_bytes(b"x")
    (root / "Movies" / "Cool Movie (2020)" / "._junk").write_bytes(b"x")


@pytest_asyncio.fixture
async def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "scanner.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # force reconnect with new URL  # noqa: SLF001
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


@pytest.mark.asyncio
async def test_scanner_classifies_and_inserts(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed(library_root)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    scanner = Scanner(session=session, event_bus=bus, ffprobe=StubFfprobe())  # type: ignore[arg-type]
    report = await scanner.scan(library, options=ScanOptions(mode="full"))

    assert report.status == "completed"
    assert report.files_seen == 6
    assert report.files_added == 6
    assert report.files_orphaned == 0

    page = await MediaRepository(session).list(limit=100)
    by_category: dict[str, int] = {}
    for item in page.items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
    assert by_category == {"media": 1, "subtitle": 1, "image": 1, "metadata": 1, "junk": 2}

    movie = next(i for i in page.items if i.filename == "movie.mkv")
    assert movie.video_codec == "h264"
    assert movie.audio_codec == "aac"
    assert movie.width == 1920


@pytest.mark.asyncio
async def test_scanner_marks_orphans_on_second_pass(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed(library_root)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    scanner = Scanner(session=session, event_bus=bus, ffprobe=StubFfprobe())  # type: ignore[arg-type]
    await scanner.scan(library)

    # Delete a file and rescan — it should now be flagged orphaned.
    (library_root / "Movies" / "Cool Movie (2020)" / "movie.mkv").unlink()

    report = await scanner.scan(library)
    assert report.status == "completed"
    assert report.files_orphaned == 1

    page = await MediaRepository(session).list(limit=100)
    orphans = [i for i in page.items if i.is_orphaned]
    assert len(orphans) == 1
    assert orphans[0].filename == "movie.mkv"


@pytest.mark.asyncio
async def test_scanner_handles_missing_root(
    session: AsyncSession, tmp_path: Path
) -> None:
    library = Library(
        name="ghost", root_path=str(tmp_path / "does-not-exist"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    scanner = Scanner(session=session, event_bus=bus, ffprobe=StubFfprobe())  # type: ignore[arg-type]
    report = await scanner.scan(library)
    assert report.status == "failed"
    assert "does not exist" in (report.error or "")
