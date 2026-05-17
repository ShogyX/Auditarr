"""Stage 07 (v1.7) — worker honours ``schedule_window``.

Plan §414:
    Freeze time inside / outside the window, assert the worker
    picks up items only inside.

We exercise the gate by patching ``schedule_window_is_open`` to a
deterministic value rather than messing with the system clock —
the helper itself is unit-tested in
``test_profile_schema_stage07.py``. This file confirms the worker
gates correctly on the helper's verdict, releases the item back
to ``queued`` when the window is closed, and emits the
``optimization.skipped_window`` event.
"""

from __future__ import annotations

import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

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


FAKE_FFMPEG = r"""#!/usr/bin/env bash
set -e
input=""
output=""
expect_input=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i) expect_input=1; shift ;;
        -*) shift ;;
        *)
            if [[ $expect_input -eq 1 ]]; then input="$1"; expect_input=0
            else output="$1"; fi
            shift ;;
    esac
done
for pct in 25 50 75 100; do
    us=$(( pct * 600000 ))
    echo "out_time_us=$us"
done
echo "progress=end"
cp "$input" "$output"
exit 0
"""

FAKE_FFPROBE = r"""#!/usr/bin/env bash
echo '{"format":{"duration":"60.0","bit_rate":"1000000","format_name":"matroska"},"streams":[{"codec_type":"video","codec_name":"h264","width":1920,"height":1080,"r_frame_rate":"24/1"},{"codec_type":"audio","codec_name":"aac"}]}'
"""


def _install_fake(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ffmpeg", FAKE_FFMPEG), ("ffprobe", FAKE_FFPROBE)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest_asyncio.fixture
async def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncSession, Path, EventBus]]:
    db_path = tmp_path / "opt.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    bin_dir = _install_fake(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    from app.core.settings import get_settings

    get_settings.cache_clear()
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


async def _seed(
    session: AsyncSession,
    media_dir: Path,
    *,
    schedule_window: dict[str, Any] | None = None,
) -> tuple[OptimizationItem, Path]:
    """Seed library + file + profile (with optional schedule_window) +
    queued item."""
    input_path = media_dir / "movie.mkv"
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
    profile_settings: dict[str, Any] = {
        "video": {"codec": "libx265", "crf": 22, "preset": "fast"},
        "audio": {"codec": "copy"},
        "output": {"container": "mkv", "replace_input": False},
    }
    if schedule_window is not None:
        profile_settings["schedule_window"] = schedule_window
    profile = OptimizationProfile(
        name="shrink-hevc",
        description="",
        enabled=True,
        settings=profile_settings,
    )
    session.add(profile)
    await session.flush()
    item = OptimizationItem(
        media_file_id=media.id,
        profile="shrink-hevc",
        status="queued",
        queued_at=utcnow(),
    )
    session.add(item)
    await session.commit()
    return item, input_path


# ── Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_runs_item_when_schedule_window_is_open(
    setup,
) -> None:
    """When the window helper says open, the worker proceeds with
    the in-process ffmpeg path and completes the item."""
    session, media_dir, _bus = setup
    item, _ = await _seed(
        session,
        media_dir,
        schedule_window={"start_hour": 0, "end_hour": 23, "timezone": "UTC"},
    )

    # Force the helper to return True regardless of the actual
    # wall clock.
    worker = OptimizationWorker(session=session, event_bus=_bus)
    with patch(
        "app.optimization.worker.schedule_window_is_open",
        return_value=True,
    ):
        report = await worker.run_one()
    assert report.status == "completed", (
        f"expected completed, got {report.status}: {report.detail}"
    )
    await session.refresh(item)
    assert item.status == "completed"


@pytest.mark.asyncio
async def test_worker_releases_item_when_schedule_window_is_closed(
    setup,
) -> None:
    """Outside the window, the item is released back to ``queued``
    (NOT terminally skipped/failed) so the next tick re-picks it
    when the window opens."""
    session, media_dir, _bus = setup
    item, _ = await _seed(
        session,
        media_dir,
        schedule_window={"start_hour": 22, "end_hour": 2, "timezone": "UTC"},
    )

    worker = OptimizationWorker(session=session, event_bus=_bus)
    with patch(
        "app.optimization.worker.schedule_window_is_open",
        return_value=False,
    ):
        report = await worker.run_one()

    # Worker reports skipped this tick.
    assert report.status == "skipped"
    assert "schedule" in (report.detail or "").lower()
    # The item is back in queued state — it's NOT a terminal outcome.
    await session.refresh(item)
    assert item.status == "queued"
    assert item.started_at is None
    assert item.progress_pct == 0


@pytest.mark.asyncio
async def test_worker_emits_optimization_skipped_window_event(
    setup,
) -> None:
    """Per addendum A.1 §114: the worker emits
    ``optimization.skipped_window`` so the dashboard can show
    "X items waiting for schedule"."""
    session, media_dir, _bus = setup
    await _seed(
        session,
        media_dir,
        schedule_window={"start_hour": 9, "end_hour": 17, "timezone": "UTC"},
    )

    captured: list[dict] = []
    _bus.subscribe(
        "optimization.skipped_window",
        lambda e: captured.append(dict(getattr(e, "payload", {}))),
    )

    worker = OptimizationWorker(session=session, event_bus=_bus)
    with patch(
        "app.optimization.worker.schedule_window_is_open",
        return_value=False,
    ):
        await worker.run_one()

    assert len(captured) == 1, (
        f"expected 1 optimization.skipped_window event, got {len(captured)}"
    )
    payload = captured[0]
    assert payload["profile"] == "shrink-hevc"
    assert "reason" in payload


@pytest.mark.asyncio
async def test_worker_re_picks_item_on_next_tick_after_window_opens(
    setup,
) -> None:
    """After the window opens, the same item the worker released
    earlier is picked up + completes."""
    session, media_dir, _bus = setup
    await _seed(
        session,
        media_dir,
        schedule_window={"start_hour": 22, "end_hour": 2, "timezone": "UTC"},
    )

    worker = OptimizationWorker(session=session, event_bus=_bus)

    # First tick: window closed.
    with patch(
        "app.optimization.worker.schedule_window_is_open",
        return_value=False,
    ):
        r1 = await worker.run_one()
    assert r1.status == "skipped"

    # Second tick: window now open.
    with patch(
        "app.optimization.worker.schedule_window_is_open",
        return_value=True,
    ):
        r2 = await worker.run_one()
    assert r2.status == "completed", (
        f"expected completed on re-pick, got {r2.status}: {r2.detail}"
    )


@pytest.mark.asyncio
async def test_worker_runs_item_when_profile_has_no_schedule_window(
    setup,
) -> None:
    """No schedule_window field = no gate = always allowed. The
    Stage 07 changes must NOT regress the pre-Stage-07 default."""
    session, media_dir, _bus = setup
    await _seed(session, media_dir, schedule_window=None)

    worker = OptimizationWorker(session=session, event_bus=_bus)
    report = await worker.run_one()
    assert report.status == "completed", (
        f"expected completed, got {report.status}: {report.detail}"
    )
