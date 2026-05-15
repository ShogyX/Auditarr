"""Cron evaluator tests."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.automation.cron import next_run, validate_cron


def _utc(year, month, day, hour=0, minute=0) -> _dt.datetime:
    return _dt.datetime(year, month, day, hour, minute, tzinfo=_dt.UTC)


def test_empty_spec_fires_every_minute() -> None:
    nxt = next_run({}, _utc(2026, 1, 1, 12, 30))
    assert nxt == _utc(2026, 1, 1, 12, 31)


def test_specific_minute_hour() -> None:
    spec = {"minute": 30, "hour": 3}
    nxt = next_run(spec, _utc(2026, 1, 1, 0, 0))
    assert nxt == _utc(2026, 1, 1, 3, 30)


def test_rolls_over_to_next_day() -> None:
    spec = {"minute": 0, "hour": 3}
    nxt = next_run(spec, _utc(2026, 1, 1, 3, 30))
    assert nxt == _utc(2026, 1, 2, 3, 0)


def test_minute_list() -> None:
    spec = {"minute": [0, 15, 30, 45]}
    nxt = next_run(spec, _utc(2026, 1, 1, 12, 20))
    assert nxt == _utc(2026, 1, 1, 12, 30)


def test_weekday_filter_monday_only() -> None:
    # 2026-01-01 is a Thursday (weekday 3); next Monday (0) at 09:00 is Jan 5
    spec = {"minute": 0, "hour": 9, "weekday": 0}
    nxt = next_run(spec, _utc(2026, 1, 1, 9, 0))
    assert nxt == _utc(2026, 1, 5, 9, 0)


def test_after_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_run({}, _dt.datetime(2026, 1, 1, 12, 0))


def test_invalid_minute_rejected() -> None:
    with pytest.raises(ValueError, match="out of range"):
        validate_cron({"minute": 60})


def test_empty_list_rejected() -> None:
    with pytest.raises(ValueError, match="empty list"):
        validate_cron({"minute": []})


def test_string_value_rejected() -> None:
    with pytest.raises(ValueError):
        validate_cron({"minute": "thirty"})


def test_validate_passes_for_sensible_spec() -> None:
    validate_cron({"minute": 0, "hour": [2, 14]})


def test_next_run_skips_current_minute() -> None:
    """If ``after`` already matches the spec, return the *next* match."""
    spec = {"minute": 0}
    nxt = next_run(spec, _utc(2026, 1, 1, 12, 0))
    assert nxt == _utc(2026, 1, 1, 13, 0)
