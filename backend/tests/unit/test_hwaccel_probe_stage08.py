"""Stage 08 (v1.7) — hwaccel probe.

Plan §457:
    Mock the ``ffmpeg -hwaccels`` subprocess; with and without
    ``cuda``, assert the event/warning surfaces appropriately.

Addendum C.4:
    The probe runs with a 5-second ``asyncio.timeout``. On
    timeout, log a warning, default to "no hwaccel detected", and
    proceed. Do not block startup.

We don't actually spawn ffmpeg in these tests; we monkey-patch
``asyncio.create_subprocess_exec`` with an async stand-in that
behaves like the subset of asyncio's process API the probe uses
(``communicate``, ``kill``, ``returncode``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.events.bus import EventBus
from app.optimization.hwaccel import (
    KNOWN_HWACCEL_NAMES,
    PROBE_TIMEOUT_SECONDS,
    _parse_hwaccels_output,
    probe_hwaccels,
    run_startup_probe,
)


# ── _parse_hwaccels_output ─────────────────────────────────────


def test_parse_recognises_standard_ffmpeg_output() -> None:
    """ffmpeg prints a header line followed by one name per line."""
    output = "Hardware acceleration methods:\ncuda\nvaapi\nqsv\n"
    assert _parse_hwaccels_output(output) == ["cuda", "vaapi", "qsv"]


def test_parse_filters_blank_lines() -> None:
    output = "Hardware acceleration methods:\n\ncuda\n\nvaapi\n"
    assert _parse_hwaccels_output(output) == ["cuda", "vaapi"]


def test_parse_handles_bytes_input() -> None:
    output = b"Hardware acceleration methods:\nvideotoolbox\n"
    assert _parse_hwaccels_output(output) == ["videotoolbox"]


def test_parse_lowercases_and_dedupes() -> None:
    output = "Hardware acceleration methods:\nCUDA\ncuda\nVaapi\n"
    assert _parse_hwaccels_output(output) == ["cuda", "vaapi"]


def test_parse_empty_output_returns_empty_list() -> None:
    assert _parse_hwaccels_output("") == []
    assert _parse_hwaccels_output("Hardware acceleration methods:\n") == []


def test_parse_handles_invalid_utf8_in_bytes() -> None:
    """The decode is errors='replace'; junk bytes don't crash."""
    output = b"Hardware acceleration methods:\n\xff\xfe\ncuda\n"
    # The garbage line decodes to replacement chars; cuda is still found.
    assert "cuda" in _parse_hwaccels_output(output)


# ── probe_hwaccels — happy path ────────────────────────────────


class _FakeProc:
    """Stand-in for an asyncio.subprocess.Process."""

    def __init__(
        self,
        *,
        stdout: bytes,
        stderr: bytes = b"",
        returncode: int = 0,
        communicate_delay: float = 0.0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._communicate_delay = communicate_delay
        self._killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._communicate_delay > 0:
            await asyncio.sleep(self._communicate_delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self._killed = True


@pytest.mark.asyncio
async def test_probe_detects_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg reports cuda → ``available=True`` with cuda in names."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"Hardware acceleration methods:\ncuda\n")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    result = await probe_hwaccels()
    assert result.available is True
    assert result.names == ["cuda"]
    assert result.timed_out is False
    assert result.error is None


@pytest.mark.asyncio
async def test_probe_detects_all_known_accelerators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each name in KNOWN_HWACCEL_NAMES triggers ``available=True``."""
    for name in KNOWN_HWACCEL_NAMES:
        async def fake_spawn(
            *args: Any, _name: str = name, **kwargs: Any
        ) -> _FakeProc:
            return _FakeProc(
                stdout=f"Hardware acceleration methods:\n{_name}\n".encode()
            )

        monkeypatch.setattr(
            "app.optimization.hwaccel.asyncio.create_subprocess_exec",
            fake_spawn,
        )
        result = await probe_hwaccels()
        assert result.available is True, f"failed for {name}"
        assert name in result.names


@pytest.mark.asyncio
async def test_probe_reports_unavailable_when_no_known_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg can return obscure names we don't recognise.
    available=False but names is preserved for the operator to see."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(
            stdout=b"Hardware acceleration methods:\nsomething_exotic\n"
        )

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    result = await probe_hwaccels()
    assert result.available is False
    assert result.names == ["something_exotic"]


@pytest.mark.asyncio
async def test_probe_reports_unavailable_on_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    result = await probe_hwaccels()
    assert result.available is False
    assert result.names == []


# ── probe_hwaccels — failure paths ─────────────────────────────


@pytest.mark.asyncio
async def test_probe_handles_missing_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ffmpeg on PATH → graceful HwaccelProbeResult, no crash."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    result = await probe_hwaccels(ffmpeg_bin="/nonexistent/ffmpeg")
    assert result.available is False
    assert result.error is not None
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_probe_handles_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"", returncode=1)

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    result = await probe_hwaccels()
    assert result.available is False
    assert result.error is not None
    assert "non-zero" in result.error.lower()


# ── Addendum C.4: timeout MUST NOT block startup ───────────────


@pytest.mark.asyncio
async def test_probe_times_out_and_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hanging ffmpeg subprocess must NOT block startup.
    The probe returns ``timed_out=True`` after the configured
    timeout."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        # Configure the fake process to wait longer than the
        # test's timeout; the probe's asyncio.wait_for should
        # cancel it.
        return _FakeProc(stdout=b"", communicate_delay=5.0)

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    # Use a short timeout for the test so it doesn't actually
    # wait 5 seconds.
    result = await probe_hwaccels(timeout_seconds=0.1)
    assert result.timed_out is True
    assert result.available is False
    assert result.error is not None
    assert "did not respond" in result.error.lower()


@pytest.mark.asyncio
async def test_probe_timeout_calls_kill_on_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort cleanup: kill() is invoked on timeout."""
    captured: list[_FakeProc] = []

    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(stdout=b"", communicate_delay=5.0)
        captured.append(proc)
        return proc

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    await probe_hwaccels(timeout_seconds=0.05)
    assert len(captured) == 1
    assert captured[0]._killed is True


@pytest.mark.asyncio
async def test_default_timeout_is_5_seconds() -> None:
    """Pin the addendum C.4 contract value explicitly so a future
    refactor can't silently change the timeout."""
    assert PROBE_TIMEOUT_SECONDS == 5.0


# ── run_startup_probe — event emission ─────────────────────────


@pytest.mark.asyncio
async def test_startup_probe_emits_event_when_no_hwaccel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No hwaccel → ``system.hwaccel_missing`` fires on the bus."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )

    bus = EventBus()
    received: list[dict[str, Any]] = []
    bus.subscribe(
        "system.hwaccel_missing",
        lambda e: received.append(dict(getattr(e, "payload", {}))),
    )

    result = await run_startup_probe(event_bus=bus)
    assert result.available is False
    assert len(received) == 1
    assert received[0]["names"] == []


@pytest.mark.asyncio
async def test_startup_probe_no_event_when_hwaccel_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hwaccel detected → no ``system.hwaccel_missing`` event."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"Hardware acceleration methods:\ncuda\n")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )

    bus = EventBus()
    received: list[dict[str, Any]] = []
    bus.subscribe(
        "system.hwaccel_missing",
        lambda e: received.append(dict(getattr(e, "payload", {}))),
    )

    result = await run_startup_probe(event_bus=bus)
    assert result.available is True
    assert received == []


@pytest.mark.asyncio
async def test_startup_probe_works_without_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``event_bus=None`` is allowed — the probe just logs."""
    async def fake_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout=b"")

    monkeypatch.setattr(
        "app.optimization.hwaccel.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    # No exceptions = pass.
    result = await run_startup_probe(event_bus=None)
    assert result.available is False
