"""Optimization worker tests.

We can't run real ffmpeg in CI — it'd require ffmpeg + ffprobe + a media
file. Instead we install a *fake* ffmpeg binary on PATH that:

1. Copies its ``-i`` input to the last positional path (the output).
2. Prints ``out_time_us`` lines on stdout to drive the runner's progress
   parser.

That covers everything the runner cares about: argv construction,
progress parsing, output validation gating, and the swap.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus, get_event_bus
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.optimization.worker import OptimizationWorker
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


# ── Fake ffmpeg/ffprobe scripts ─────────────────────────────────
FAKE_FFMPEG = r"""#!/usr/bin/env bash
# Fake ffmpeg: copy input to output and emit a few progress events.
set -e
input=""
output=""
expect_input=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i) expect_input=1; shift ;;
        -*) shift ;;
        *)
            if [[ $expect_input -eq 1 ]]; then
                input="$1"; expect_input=0
            else
                output="$1"
            fi
            shift ;;
    esac
done
# Stream progress to stdout (the runner reads pipe:1 only).
for pct in 25 50 75 100; do
    us=$(( pct * 600000 ))  # 60s clip * 1M us/s * pct/100
    echo "out_time_us=$us"
done
echo "progress=end"
# Produce the output by copying the input bytes (preserves probe-able
# format, which ffprobe will read fine).
cp "$input" "$output"
exit 0
"""

FAKE_FFPROBE = r"""#!/usr/bin/env bash
# Fake ffprobe: emit a minimal JSON payload describing a 60s mkv with a
# video stream. The real service parses this same shape.
cat <<'JSON'
{
  "format": {
    "format_name": "matroska,webm",
    "duration": "60.000000",
    "bit_rate": "1500000"
  },
  "streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
     "r_frame_rate": "24000/1001"}
  ]
}
JSON
"""


def _install_fake(tmp_path: Path) -> Path:
    """Install fake ffmpeg/ffprobe + return the bin dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ffmpeg", FAKE_FFMPEG), ("ffprobe", FAKE_FFPROBE)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


# ── DB fixture ──────────────────────────────────────────────────
@pytest_asyncio.fixture
async def session_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncSession, Path, EventBus]]:
    db_path = tmp_path / "opt.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    # Put the fake binaries on PATH.
    bin_dir = _install_fake(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    from app.core.settings import get_settings

    get_settings.cache_clear()
    # Reset the ffprobe service so it re-resolves the binary on PATH.
    from app.services.media.ffprobe import reset_ffprobe_service

    reset_ffprobe_service()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    media_dir = tmp_path / "media"
    media_dir.mkdir()

    try:
        async with db.session() as session:
            yield session, media_dir, bus
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


async def _seed_one(
    session: AsyncSession,
    media_dir: Path,
    *,
    profile_name: str = "shrink-hevc",
    profile_settings: dict | None = None,
) -> tuple[OptimizationItem, Path]:
    """Insert library + file + profile + queued item. Returns (item, input_path)."""
    input_path = media_dir / "movie.mkv"
    # Write a minimal mkv-shaped placeholder. The fake ffmpeg just copies
    # the bytes; the fake ffprobe ignores file content.
    input_path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

    lib = Library(name="Movies", root_path=str(media_dir), kind="movies")
    session.add(lib)
    await session.flush()
    media = MediaFile(
        library_id=lib.id,
        path=str(input_path),
        relative_path="movie.mkv",
        filename="movie.mkv",
        extension="mkv",
        size_bytes=input_path.stat().st_size,
        mtime=utcnow(),
        category="media",
        severity="ok",
        severity_rank=10,
        container="matroska",
        video_codec="h264",
        duration_seconds=60.0,
        bitrate_kbps=1500,
        has_subtitles=False,
        seen_at=utcnow(),
        is_orphaned=False,
    )
    session.add(media)
    profile = OptimizationProfile(
        name=profile_name,
        enabled=True,
        settings=profile_settings
        or {
            "video": {"codec": "libx265", "crf": 23, "preset": "fast"},
            "audio": {"codec": "copy"},
            "output": {"container": "mkv", "replace_input": True, "keep_backup": True},
        },
    )
    session.add(profile)
    await session.flush()
    item = OptimizationItem(
        media_file_id=media.id,
        profile=profile_name,
        status="queued",
        queued_at=utcnow(),
        item_metadata={},
    )
    session.add(item)
    await session.commit()
    return item, input_path


# ── Tests ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_one_idle_when_queue_empty(
    session_and_paths,
) -> None:
    session, _media, bus = session_and_paths
    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "idle"
    assert report.item_id is None


@pytest.mark.asyncio
async def test_run_one_completes_queued_item_and_swaps(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, input_path = await _seed_one(session, media_dir)
    original_bytes = input_path.read_bytes()

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()

    assert report.status == "completed", report.detail
    # Item moved to completed.
    await session.refresh(item)
    assert item.status == "completed"
    assert item.progress_pct == 100
    assert item.started_at is not None
    assert item.finished_at is not None
    assert item.original_size_bytes == len(original_bytes)
    assert item.optimized_size_bytes is not None

    # Original got moved aside as .bak; output exists at .mkv.
    backup = input_path.with_suffix(input_path.suffix + ".bak")
    final = input_path.with_suffix(".mkv")
    assert backup.exists(), "expected .bak to be retained when keep_backup=true"
    assert final.exists()
    assert final.read_bytes() == original_bytes  # fake ffmpeg copies in place


@pytest.mark.asyncio
async def test_run_one_skipped_when_input_below_threshold(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, _path = await _seed_one(
        session,
        media_dir,
        profile_settings={
            "video": {"codec": "libx265"},
            "audio": {"codec": "copy"},
            "output": {"container": "mkv"},
            # Source bitrate is 1500kbps; this threshold is higher.
            "skip_if_bitrate_below_kbps": 5000,
        },
    )
    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "skipped"
    await session.refresh(item)
    assert item.status == "skipped"
    assert "bitrate" in (item.error or "").lower()


@pytest.mark.asyncio
async def test_run_one_skipped_when_profile_disabled(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, _ = await _seed_one(session, media_dir)
    # Disable the profile.
    profile = (
        await session.execute(
            __import__("sqlalchemy").select(OptimizationProfile).where(
                OptimizationProfile.name == "shrink-hevc"
            )
        )
    ).scalar_one()
    profile.enabled = False
    await session.commit()

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "skipped"
    await session.refresh(item)
    assert item.status == "skipped"


@pytest.mark.asyncio
async def test_run_one_fails_when_input_missing(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, input_path = await _seed_one(session, media_dir)
    input_path.unlink()  # gone

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "failed"
    await session.refresh(item)
    assert item.status == "failed"
    assert "missing" in (item.error or "").lower()


@pytest.mark.asyncio
async def test_run_one_keep_backup_false_deletes_original(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, input_path = await _seed_one(
        session,
        media_dir,
        profile_settings={
            "video": {"codec": "libx265"},
            "audio": {"codec": "copy"},
            "output": {
                "container": "mkv",
                "replace_input": True,
                "keep_backup": False,
            },
        },
    )
    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "completed", report.detail

    backup = input_path.with_suffix(input_path.suffix + ".bak")
    assert not backup.exists()
    await session.refresh(item)
    assert item.backup_path is None


@pytest.mark.asyncio
async def test_run_one_no_swap_when_replace_input_false(
    session_and_paths,
) -> None:
    session, media_dir, bus = session_and_paths
    item, input_path = await _seed_one(
        session,
        media_dir,
        profile_settings={
            "video": {"codec": "libx265"},
            "audio": {"codec": "copy"},
            "output": {
                "container": "mkv",
                "replace_input": False,
                "keep_backup": False,
            },
        },
    )
    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "completed"

    # The original is untouched; the temp output lives next to it.
    assert input_path.exists()
    await session.refresh(item)
    assert item.item_metadata.get("output_path", "").endswith(
        ".auditarr.tmp.mkv"
    )
