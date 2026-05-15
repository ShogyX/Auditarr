"""ffmpeg runner with progress reporting.

Spawns ffmpeg as an async subprocess and parses its ``-progress pipe:1``
output to compute a percent-complete value relative to the input's
duration. Callers supply a progress callback that fires roughly every
second.

The runner is intentionally narrow: it builds the argv from a profile
+ paths, runs ffmpeg, parses progress, and returns. It does *not* know
about the queue, the DB, or backups. The worker (next module) composes
those concerns.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.optimization.profile_schema import ProfileDefinition

log = get_logger("auditarr.optimization.ffmpeg", category="optimization")


@dataclass(slots=True)
class TranscodeRequest:
    input_path: Path
    output_path: Path
    profile: ProfileDefinition
    # Total duration of the input in seconds; lets the runner compute %.
    input_duration_seconds: float | None = None


@dataclass(slots=True)
class TranscodeResult:
    success: bool
    return_code: int
    stderr_tail: str
    duration_seconds: float
    last_progress_pct: int


ProgressCallback = Callable[[int], Awaitable[None]]
"""Async callback invoked with the latest 0..100 percent."""


def build_ffmpeg_argv(req: TranscodeRequest, *, ffmpeg_bin: str = "ffmpeg") -> list[str]:
    """Translate a profile into the ffmpeg command line.

    Order matters: ffmpeg parses input/output as positional siblings of
    their preceding flags, so we keep the layout
    ``[ffmpeg, global flags, -i input, encode flags, output flags, output]``.
    """
    p = req.profile
    argv = [
        ffmpeg_bin,
        "-y",  # overwrite the output if it exists (the worker uses a temp path anyway)
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",  # noisy info goes through -progress instead
        # Progress events written to stdout in key=value form.
        "-progress",
        "pipe:1",
        # Input.
        "-i",
        str(req.input_path),
    ]

    # ── Stream selection ──
    # Video.
    argv += ["-map", "0:v:0?"]  # first video stream, optional
    # Audio. ``copy`` profiles still map all audio streams; transcoding
    # ones can be widened in the future via an explicit ``audio.stream``
    # selector.
    argv += ["-map", "0:a?"]
    # Subtitles.
    if p.subtitles.handling == "copy":
        argv += ["-map", "0:s?"]

    # ── Video encode flags ──
    argv += ["-c:v", p.video.codec]
    if p.video.codec != "copy":
        if p.video.crf is not None:
            argv += ["-crf", str(p.video.crf)]
        if p.video.preset:
            argv += ["-preset", p.video.preset]
        if p.video.max_bitrate_kbps is not None:
            argv += [
                "-maxrate",
                f"{p.video.max_bitrate_kbps}k",
                "-bufsize",
                f"{p.video.max_bitrate_kbps * 2}k",
            ]
        if p.video.scale_height is not None:
            argv += [
                "-vf",
                # -1 keeps the aspect ratio; ensure divisible-by-2 width
                # which several codecs require.
                f"scale=trunc(iw*({p.video.scale_height}/ih)/2)*2:"
                f"{p.video.scale_height}",
            ]

    # ── Audio encode flags ──
    argv += ["-c:a", p.audio.codec]
    if p.audio.codec != "copy":
        if p.audio.bitrate_kbps is not None:
            argv += ["-b:a", f"{p.audio.bitrate_kbps}k"]
        if p.audio.channels is not None:
            argv += ["-ac", str(p.audio.channels)]

    # ── Subtitles ──
    if p.subtitles.handling == "copy":
        argv += ["-c:s", "copy"]

    # ── Extra escape-hatch args, container, output ──
    if p.extra_args:
        argv += [str(a) for a in p.extra_args]
    argv += [str(req.output_path)]
    return argv


# ── Progress pipe parsing ──────────────────────────────────────
def _parse_out_time_us(line: str) -> int | None:
    """ffmpeg emits ``out_time_us=NNNNN`` (microseconds) per progress block."""
    if not line.startswith("out_time_us="):
        return None
    try:
        return int(line.split("=", 1)[1].strip())
    except (ValueError, IndexError):
        return None


async def _drain_stream(
    stream: asyncio.StreamReader, buffer: list[str]
) -> None:
    while True:
        chunk = await stream.readline()
        if not chunk:
            return
        try:
            buffer.append(chunk.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            pass
        # Cap buffer to last ~200 lines to bound memory.
        if len(buffer) > 200:
            del buffer[: len(buffer) - 200]


async def run_transcode(
    request: TranscodeRequest,
    *,
    on_progress: ProgressCallback | None = None,
    ffmpeg_bin: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> TranscodeResult:
    """Run ffmpeg for ``request``, reporting progress along the way."""
    binary = ffmpeg_bin or shutil.which("ffmpeg") or "ffmpeg"
    argv = build_ffmpeg_argv(request, ffmpeg_bin=binary)
    log.info(
        "optimization.ffmpeg_start",
        argv=shlex.join(argv),
        input=str(request.input_path),
        output=str(request.output_path),
    )

    # Ensure the output directory exists. Worker passes a temp path under
    # the original's directory; this is a defensive belt-and-braces.
    request.output_path.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_buffer: list[str] = []
    last_pct = 0
    duration_us = (
        int((request.input_duration_seconds or 0) * 1_000_000)
        if request.input_duration_seconds
        else 0
    )

    # ── stderr drain task ──
    stderr_task = asyncio.create_task(
        _drain_stream(proc.stderr, stderr_buffer)
    )

    # ── stdout progress loop ──
    assert proc.stdout is not None
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                log.warning(
                    "optimization.ffmpeg_cancelled",
                    input=str(request.input_path),
                )
                proc.terminate()
                # Give ffmpeg a brief grace period to exit cleanly; SIGKILL after.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                break

            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            out_time_us = _parse_out_time_us(text)
            if out_time_us is not None and duration_us > 0:
                pct = int(min(100, max(0, (out_time_us / duration_us) * 100)))
                if pct != last_pct:
                    last_pct = pct
                    if on_progress is not None:
                        try:
                            await on_progress(pct)
                        except Exception:  # noqa: BLE001
                            # Progress callback failures must not abort
                            # the transcode itself.
                            log.exception(
                                "optimization.progress_callback_failed"
                            )
    finally:
        # Make sure stderr drain has caught up before we read it.
        try:
            await asyncio.wait_for(stderr_task, timeout=2.0)
        except asyncio.TimeoutError:
            stderr_task.cancel()

    return_code = await proc.wait()
    success = return_code == 0 and request.output_path.exists()
    # Force progress to 100 on clean success — the last out_time_us is
    # rarely exactly the full duration.
    if success:
        last_pct = 100
        if on_progress is not None:
            try:
                await on_progress(100)
            except Exception:  # noqa: BLE001
                pass

    stderr_tail = "".join(stderr_buffer)[-2000:]
    log.info(
        "optimization.ffmpeg_done",
        return_code=return_code,
        success=success,
        output_size=(
            os.path.getsize(request.output_path)
            if request.output_path.exists()
            else None
        ),
    )
    return TranscodeResult(
        success=success,
        return_code=return_code,
        stderr_tail=stderr_tail,
        duration_seconds=0.0,  # filled in by the worker if needed
        last_progress_pct=last_pct,
    )


# ── Sanity-check the output ────────────────────────────────────
async def validate_output(
    *, output_path: Path, expected_duration_seconds: float | None
) -> tuple[bool, str | None]:
    """Quick ffprobe on the output. Returns (ok, reason_if_not).

    We accept the transcode if:

    * The output file exists and is non-empty.
    * It has a video codec.
    * Its duration is within ±2% of the input's duration (when the input
      duration is known). This catches cases where ffmpeg exits 0 but
      produced a truncated file because of a stream-mapping mistake.
    """
    if not output_path.exists():
        return False, "output file missing"
    size = output_path.stat().st_size
    if size <= 0:
        return False, "output file is empty"

    # Use the existing ffprobe service so we go through one code path.
    from app.services.media.ffprobe import get_ffprobe_service

    probe = await get_ffprobe_service().probe(str(output_path))
    if probe is None or not probe.ok:
        return False, (probe.error if probe else "ffprobe failed") or "ffprobe failed"
    if not probe.video_codec:
        return False, "output has no video stream"
    if expected_duration_seconds and probe.duration_seconds:
        ratio = probe.duration_seconds / expected_duration_seconds
        if ratio < 0.98 or ratio > 1.02:
            return (
                False,
                f"output duration {probe.duration_seconds:.1f}s differs "
                f"from input {expected_duration_seconds:.1f}s (>2%)",
            )
    return True, None


# Expose the module-level convenience for callers.
def _ensure_arg_quoting(_argv: list[str]) -> list[str]:
    """No-op shim retained as an explicit affordance for callers that
    used to want shell-quoting. The runner now passes argv to
    ``create_subprocess_exec``, which handles quoting safely."""
    return _argv


__all__ = [
    "ProgressCallback",
    "TranscodeRequest",
    "TranscodeResult",
    "build_ffmpeg_argv",
    "run_transcode",
    "validate_output",
]


# Keep this helper importable so future stages can introspect the argv.
_ = Any
