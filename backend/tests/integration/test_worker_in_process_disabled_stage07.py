"""Stage 07 (v1.7) — in-process runner kill-switch + routing-target dispatch.

Plan §415:
    Flip ``optimization_in_process_runner_enabled=False``, queue
    an in-process item, assert it's failed with a clear reason.

Plus the addendum A.1 §114 contract: the worker emits
``optimization.routed`` when a non-``in_process`` profile is
picked up; the item is marked ``routed`` (not run locally).
"""

from __future__ import annotations

import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
    routing_target: str = "in_process",
    extra_settings: dict[str, Any] | None = None,
) -> tuple[OptimizationItem, Path]:
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
        "routing_target": routing_target,
    }
    if extra_settings:
        profile_settings.update(extra_settings)
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


# ── In-process kill-switch (plan §415) ─────────────────────────


@pytest.mark.asyncio
async def test_in_process_disabled_fails_in_process_item_with_clear_reason(
    setup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §415: flip the runtime setting off, queue an
    in-process item, assert it's failed with a clear reason
    pointing at the routing-target reconfiguration."""
    session, media_dir, bus = setup
    item, _ = await _seed(session, media_dir, routing_target="in_process")

    # Flip the runtime setting via env override (the Settings
    # cache reads env on construction; get_settings cache was
    # cleared in fixture so this takes effect).
    monkeypatch.setenv(
        "AUDITARR_OPTIMIZATION_IN_PROCESS_RUNNER_ENABLED", "false"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "failed"
    detail = (report.detail or "").lower()
    assert "in-process" in detail or "in_process" in detail
    # Operator-facing actionable text per plan §401.
    assert "plex" in detail or "jellyfin" in detail or "tdarr" in detail

    await session.refresh(item)
    assert item.status == "failed"
    assert "in-process" in (item.error or "").lower()


@pytest.mark.asyncio
async def test_in_process_enabled_default_runs_normally(setup) -> None:
    """Regression guard: the default setting (True) preserves
    pre-Stage-07 behaviour. An in-process item runs and completes."""
    session, media_dir, bus = setup
    item, _ = await _seed(session, media_dir, routing_target="in_process")

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "completed", (
        f"expected completed, got {report.status}: {report.detail}"
    )
    await session.refresh(item)
    assert item.status == "completed"


@pytest.mark.asyncio
async def test_in_process_disabled_does_not_affect_routed_items(
    setup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill-switch is in-process-specific. A profile routed to
    a non-in_process target should still be marked ``routed`` —
    not failed."""
    session, media_dir, bus = setup
    item, _ = await _seed(session, media_dir, routing_target="tdarr")

    monkeypatch.setenv(
        "AUDITARR_OPTIMIZATION_IN_PROCESS_RUNNER_ENABLED", "false"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    # Routed, not failed — the kill-switch doesn't apply to
    # non-in_process profiles.
    assert report.status == "routed"
    await session.refresh(item)
    assert item.status == "routed"


# ── routing_target dispatch (addendum A.1 §114) ───────────────


@pytest.mark.asyncio
async def test_routing_target_non_in_process_marks_item_routed(setup) -> None:
    """Profiles with ``routing_target != in_process`` are marked
    ``routed`` and the worker skips ffmpeg. Stage 08 will wire
    the provider call; Stage 07 just lays the seam."""
    session, media_dir, bus = setup
    item, input_path = await _seed(session, media_dir, routing_target="tdarr")

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "routed"
    assert "tdarr" in (report.detail or "").lower()

    await session.refresh(item)
    assert item.status == "routed"
    # Metadata records the routing target + a timestamp.
    md = item.item_metadata or {}
    assert md.get("routing_target") == "tdarr"
    assert "routed_at" in md
    # And the input file is UNTOUCHED — no in-process work.
    assert input_path.exists()


@pytest.mark.asyncio
async def test_routing_target_emits_optimization_routed_event(setup) -> None:
    """Per addendum A.1 §114: ``optimization.routed`` is emitted
    when a non-in_process profile is picked up. WebSocket relay
    is wildcard-subscribed; the bus emit is what we verify."""
    session, media_dir, bus = setup
    await _seed(session, media_dir, routing_target="plex")

    captured: list[dict] = []
    bus.subscribe(
        "optimization.routed",
        lambda e: captured.append(dict(getattr(e, "payload", {}))),
    )

    worker = OptimizationWorker(session=session, event_bus=bus)
    await worker.run_one()

    assert len(captured) == 1
    payload = captured[0]
    assert payload["profile"] == "shrink-hevc"
    assert payload["routing_target"] == "plex"


@pytest.mark.asyncio
async def test_routing_target_default_in_process_unchanged(setup) -> None:
    """Pre-Stage-07 profiles (no routing_target key) default to
    in_process and the worker still completes them in-process."""
    session, media_dir, bus = setup
    # Seed with the legacy shape — no routing_target key.
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
    profile = OptimizationProfile(
        name="legacy",
        enabled=True,
        settings={
            "video": {"codec": "libx265", "crf": 22, "preset": "fast"},
            "audio": {"codec": "copy"},
            "output": {"container": "mkv", "replace_input": False},
        },
    )
    session.add(profile)
    await session.flush()
    item = OptimizationItem(
        media_file_id=media.id,
        profile="legacy",
        status="queued",
        queued_at=utcnow(),
    )
    session.add(item)
    await session.commit()

    worker = OptimizationWorker(session=session, event_bus=bus)
    report = await worker.run_one()
    assert report.status == "completed", (
        f"expected completed, got {report.status}: {report.detail}"
    )
