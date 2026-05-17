"""Stage 08 (v1.7) — ffmpeg hardware acceleration probe.

Plan §445-448 + addendum C.4:

  * Run ``ffmpeg -hide_banner -hwaccels`` once at process start.
  * If the output includes any of ``cuda``, ``vaapi``, ``qsv``,
    ``videotoolbox``, ``nvenc``, ``amf``, log INFO and record the
    list.
  * Otherwise log a WARNING and surface ``system.hwaccel_missing``
    on the bus so the dashboard can show a banner.
  * The probe runs under a 5-second ``asyncio.timeout`` (addendum
    C.4). On timeout, log a warning, treat as "no hwaccel
    detected", and proceed — never block startup.

The probe is pure (no DB, no settings reads) so unit tests can
drive it with a mocked subprocess.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.events.bus import EventBus

log = get_logger("auditarr.optimization.hwaccel", category="optimization")


# Acceleration codenames ffmpeg prints in ``-hwaccels`` output that
# Auditarr considers as "yes, hwaccel is available". Each one is a
# real ffmpeg hwaccel name (we don't invent or alias).
#
# The list intentionally includes both umbrella names (``cuda``,
# ``vaapi``, ``qsv``, ``videotoolbox``) and codec-specific encoder
# names (``nvenc``, ``amf``) because some ffmpeg builds report only
# the latter on ``-hwaccels``. Sniffing for either is the most
# permissive correct posture.
KNOWN_HWACCEL_NAMES = frozenset(
    {"cuda", "vaapi", "qsv", "videotoolbox", "nvenc", "amf"}
)

# Probe timeout per addendum C.4.
PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class HwaccelProbeResult:
    """Outcome of a single hwaccel probe.

    ``available`` is True when at least one accelerator name in
    ``names`` matched ``KNOWN_HWACCEL_NAMES``. The full ``names``
    list is the raw set ffmpeg reported (after deduplication +
    lower-casing) for forensic visibility.

    ``timed_out`` is True when the probe didn't finish inside
    ``PROBE_TIMEOUT_SECONDS``. The probe still completes (returning
    a non-available result) so startup never blocks.
    """

    available: bool
    names: list[str] = field(default_factory=list)
    timed_out: bool = False
    error: str | None = None


def _parse_hwaccels_output(stdout: bytes | str) -> list[str]:
    """Parse ``ffmpeg -hwaccels`` stdout into a list of accel names.

    ffmpeg prints something like::

        Hardware acceleration methods:
        cuda
        vaapi
        qsv

    First line is a header; subsequent non-empty lines are accel
    names. We strip + lowercase + dedup defensively.
    """
    if isinstance(stdout, bytes):
        try:
            text = stdout.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = ""
    else:
        text = stdout

    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line:
            continue
        # Skip the "Hardware acceleration methods:" header line.
        if "hardware acceleration" in line:
            continue
        # Anything left should be a single accel name. Filter out
        # punctuation / parens just in case ffmpeg adds them.
        token = line.split()[0].strip(",.;:()[]<>")
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


async def probe_hwaccels(
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> HwaccelProbeResult:
    """Run ``ffmpeg -hwaccels`` and parse the result.

    Returns a :class:`HwaccelProbeResult` always (no exceptions
    propagate). Subprocess failures, missing ffmpeg, and timeouts
    all flow through the ``error`` / ``timed_out`` fields so the
    caller can log appropriately without try/except scaffolding.

    Addendum C.4: timeouts MUST NOT block startup. We use
    ``asyncio.wait_for`` with a 5-second default and downgrade to
    "no hwaccel detected" on TimeoutError.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-hide_banner",
            "-hwaccels",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return HwaccelProbeResult(
            available=False,
            error=f"ffmpeg binary not found at {ffmpeg_bin!r}",
        )
    except Exception as exc:  # noqa: BLE001
        return HwaccelProbeResult(
            available=False,
            error=f"failed to spawn ffmpeg: {exc!s}",
        )

    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        # Best-effort cleanup. If kill() can't terminate the
        # process (extremely rare), proc.wait() in the finally
        # below would block — we don't await it. Startup
        # proceeds regardless.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return HwaccelProbeResult(
            available=False,
            timed_out=True,
            error=(
                f"ffmpeg -hwaccels did not respond within "
                f"{timeout_seconds:.1f}s; treating as no hwaccel "
                "(addendum C.4: probe must not block startup)"
            ),
        )

    if proc.returncode != 0:
        return HwaccelProbeResult(
            available=False,
            error=(
                f"ffmpeg -hwaccels returned non-zero exit code "
                f"{proc.returncode}"
            ),
        )

    names = _parse_hwaccels_output(stdout)
    matched = [n for n in names if n in KNOWN_HWACCEL_NAMES]
    return HwaccelProbeResult(available=bool(matched), names=names)


async def run_startup_probe(
    *,
    event_bus: EventBus | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> HwaccelProbeResult:
    """Run the probe and announce the result.

    Plan §445-448:
      * When at least one accelerator is detected, log at INFO.
      * Otherwise, log at WARNING and emit
        ``system.hwaccel_missing`` on the bus so the dashboard
        can surface a dismissable banner.

    Returns the underlying probe result for callers that want it
    (the worker logs it onto its own context for debugging).
    """
    result = await probe_hwaccels(ffmpeg_bin=ffmpeg_bin)
    if result.available:
        log.info(
            "optimization.hwaccel.detected",
            names=result.names,
            matched=[n for n in result.names if n in KNOWN_HWACCEL_NAMES],
        )
    else:
        log.warning(
            "optimization.hwaccel.missing",
            names=result.names,
            timed_out=result.timed_out,
            error=result.error,
        )
        if event_bus is not None:
            await event_bus.emit(
                "system.hwaccel_missing",
                {
                    "names": result.names,
                    "timed_out": result.timed_out,
                    "error": result.error,
                },
                source="optimization",
            )
    return result
