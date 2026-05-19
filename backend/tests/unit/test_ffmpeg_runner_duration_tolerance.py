"""Duration-tolerance check for the in-process transcode validator.

Pins the contract that very short clips don't fail the validator on a
single frame's worth of drift. Before this fix, a 1-second mkv came
back as ``"output duration 1.0s differs from input 1.0s (>2%)"`` —
the strict 2% relative bound on rounded display values exceeded the
threshold for any clip under ~5 s.
"""

from __future__ import annotations

import pytest

from app.optimization.ffmpeg_runner import _duration_within_tolerance


# ── Long-form: 2% relative bound applies normally ─────────────────


def test_long_clip_within_2pct_passes() -> None:
    ok, msg = _duration_within_tolerance(actual_seconds=100.0, expected_seconds=100.5)
    assert ok and msg is None


def test_long_clip_outside_2pct_fails() -> None:
    ok, msg = _duration_within_tolerance(actual_seconds=110.0, expected_seconds=100.0)
    assert not ok
    assert "10.000s" in msg


# ── Short clips: absolute 0.5s bound saves us ─────────────────────


@pytest.mark.parametrize(
    "actual,expected",
    [
        (1.0, 1.0),
        (1.0, 1.04),   # 4% drift on a 1s clip — 0.04s absolute → OK
        (1.0, 0.7),    # 30% drift — 0.3s absolute → OK
        (0.95, 1.0),
        (2.0, 1.5),    # 33% drift, 0.5s absolute → OK (boundary)
    ],
)
def test_short_clip_within_absolute_05s_passes(
    actual: float, expected: float
) -> None:
    ok, msg = _duration_within_tolerance(
        actual_seconds=actual, expected_seconds=expected
    )
    assert ok, msg


def test_short_clip_beyond_absolute_05s_fails() -> None:
    """A 1-second clip producing a 0.6-second output is genuinely
    broken — the absolute bound has to catch it."""
    ok, msg = _duration_within_tolerance(actual_seconds=1.7, expected_seconds=1.0)
    assert not ok
    assert "0.700s" in msg
