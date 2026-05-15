"""ffprobe service.

Async wrapper around the ``ffprobe`` binary. Handles per-file timeouts,
graceful degradation when the binary is missing, and structured parsing of
the ``-print_format json`` output into a small denormalized summary plus
the original full payload (kept for plugin extensions).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger("auditarr.media.ffprobe", category="media")

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(slots=True)
class FfprobeResult:
    """Structured result of probing a single file."""

    ok: bool
    container: str | None = None
    duration_seconds: float | None = None
    bitrate_kbps: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    subtitle_codec: str | None = None
    width: int | None = None
    height: int | None = None
    framerate: float | None = None
    has_subtitles: bool = False
    subtitle_languages: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    error: str | None = None


class FfprobeUnavailable(RuntimeError):
    """Raised when the ``ffprobe`` binary cannot be located on PATH."""


class FfprobeService:
    """Run ``ffprobe`` against files and return :class:`FfprobeResult`."""

    def __init__(
        self,
        *,
        binary: str = "ffprobe",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_concurrency: int = 4,
    ) -> None:
        self._binary = binary
        self._timeout = timeout_seconds
        self._sem = asyncio.Semaphore(max_concurrency)
        self._resolved: str | None = None

    @property
    def is_available(self) -> bool:
        """Lazy lookup of the ffprobe binary."""
        if self._resolved is None:
            self._resolved = shutil.which(self._binary) or ""
        return bool(self._resolved)

    # ── Public API ────────────────────────────────────────────
    async def probe(self, path: str) -> FfprobeResult:
        if not self.is_available:
            return FfprobeResult(ok=False, error="ffprobe binary not available")
        async with self._sem:
            return await self._probe_one(path)

    async def probe_many(self, paths: list[str]) -> dict[str, FfprobeResult]:
        """Probe many files in parallel (capped by ``max_concurrency``)."""
        if not paths:
            return {}
        results = await asyncio.gather(
            *(self.probe(p) for p in paths), return_exceptions=False
        )
        return dict(zip(paths, results, strict=True))

    # ── Internals ─────────────────────────────────────────────
    async def _probe_one(self, path: str) -> FfprobeResult:
        cmd = [
            self._binary,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "--",
            path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return FfprobeResult(ok=False, error=f"ffprobe spawn failed: {exc}")
        except OSError as exc:
            return FfprobeResult(ok=False, error=f"ffprobe spawn failed: {exc}")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return FfprobeResult(ok=False, error=f"ffprobe timeout (>{self._timeout}s)")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip() or "unknown error"
            return FfprobeResult(
                ok=False, error=f"ffprobe exited {proc.returncode}: {err[:400]}"
            )

        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            return FfprobeResult(ok=False, error=f"ffprobe json invalid: {exc}")

        return parse_ffprobe(payload)


def parse_ffprobe(payload: dict[str, Any]) -> FfprobeResult:
    """Pure parsing function — fully unit-testable without a subprocess."""
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    container = (fmt.get("format_name") or "").split(",")[0].strip() or None

    duration = _coerce_float(fmt.get("duration"))
    bitrate = _coerce_int(fmt.get("bit_rate"))
    bitrate_kbps = round(bitrate / 1000) if bitrate is not None else None

    width = _coerce_int((video or {}).get("width"))
    height = _coerce_int((video or {}).get("height"))
    framerate = _parse_framerate((video or {}).get("avg_frame_rate"))

    audio_languages = sorted(
        {
            lang
            for s in audios
            if (lang := _stream_language(s)) is not None
        }
    )
    subtitle_languages = sorted(
        {
            lang
            for s in subs
            if (lang := _stream_language(s)) is not None
        }
    )

    return FfprobeResult(
        ok=True,
        container=container,
        duration_seconds=duration,
        bitrate_kbps=bitrate_kbps,
        video_codec=(video or {}).get("codec_name") or None,
        audio_codec=(audios[0] if audios else {}).get("codec_name") or None,
        subtitle_codec=(subs[0] if subs else {}).get("codec_name") or None,
        width=width,
        height=height,
        framerate=framerate,
        has_subtitles=bool(subs),
        subtitle_languages=subtitle_languages,
        audio_languages=audio_languages,
        raw=payload,
    )


# ── helpers ──────────────────────────────────────────────────
def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_framerate(value: Any) -> float | None:
    """Convert ffprobe's ``"24000/1001"`` style fractions to a float."""
    if not isinstance(value, str) or not value:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            n, d = float(num), float(den)
            if d == 0:
                return None
            return round(n / d, 4)
        except ValueError:
            return None
    return _coerce_float(value)


def _stream_language(stream: dict[str, Any]) -> str | None:
    """Pull a normalized language tag from an ffprobe stream entry."""
    tags = stream.get("tags") or {}
    for key in ("language", "LANGUAGE", "Language"):
        v = tags.get(key)
        if isinstance(v, str) and v.strip() and v.strip().lower() != "und":
            return v.strip().lower()
    return None


_service: FfprobeService | None = None


def get_ffprobe_service() -> FfprobeService:
    global _service
    if _service is None:
        _service = FfprobeService()
    return _service


def reset_ffprobe_service() -> None:
    global _service
    _service = None
