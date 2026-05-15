"""Minimal cron-like scheduling.

Schedules carry a JSON document with optional ``minute``/``hour``/``day``/
``month``/``weekday`` keys. Each can be an int or a list of ints. An
absent key means "any". This is the same shape ``arq.cron`` accepts —
intentional, so we can hand schedules straight off to ARQ later — but
we evaluate it ourselves here to compute ``next_run_at`` for persistence
and UI display.

Resolution is 1 minute. The scheduler's poll loop runs every minute and
checks ``next_run_at <= now``; jitter on either side is fine.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any


def _as_set(value: Any, valid_range: range) -> set[int]:
    """Normalize a cron field to a set of accepted values."""
    if value is None:
        return set(valid_range)
    if isinstance(value, int):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = [int(v) for v in value]
    else:
        raise ValueError(f"cron field must be int or list, got {type(value).__name__}")
    out = set()
    for v in items:
        if v not in valid_range:
            raise ValueError(
                f"cron value {v} out of range {valid_range.start}..{valid_range.stop - 1}"
            )
        out.add(v)
    if not out:
        # Empty list — treat as "no valid times" which would loop forever;
        # reject early instead.
        raise ValueError("cron field cannot be an empty list")
    return out


def validate_cron(spec: dict[str, Any]) -> None:
    """Validate a cron spec dict. Raises on problems, returns None on success."""
    _as_set(spec.get("minute"), range(0, 60))
    _as_set(spec.get("hour"), range(0, 24))
    _as_set(spec.get("day"), range(1, 32))
    _as_set(spec.get("month"), range(1, 13))
    _as_set(spec.get("weekday"), range(0, 7))  # 0 = Monday


def next_run(spec: dict[str, Any], after: _dt.datetime) -> _dt.datetime:
    """Return the next minute >= ``after`` whose fields satisfy ``spec``.

    ``after`` must be timezone-aware. The returned datetime is in the same
    timezone (UTC throughout the system; the operator's local-time display
    is a frontend concern).
    """
    if after.tzinfo is None:
        raise ValueError("``after`` must be timezone-aware")

    minutes = _as_set(spec.get("minute"), range(0, 60))
    hours = _as_set(spec.get("hour"), range(0, 24))
    days = _as_set(spec.get("day"), range(1, 32))
    months = _as_set(spec.get("month"), range(1, 13))
    weekdays = _as_set(spec.get("weekday"), range(0, 7))

    # Round up to the next whole minute. If ``after`` is exactly on a
    # minute boundary we want the *next* match, not the current minute —
    # otherwise a freshly-fired schedule would keep matching itself.
    candidate = after.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)

    # Loop with a generous upper bound. The matrix of (minute, hour, day,
    # month, weekday) is finite; if no candidate matches within ~4 years
    # the spec is unsatisfiable (e.g. Feb 30) and we surface that.
    for _ in range(60 * 24 * 366 * 4):
        if (
            candidate.minute in minutes
            and candidate.hour in hours
            and candidate.day in days
            and candidate.month in months
            and candidate.weekday() in weekdays
        ):
            return candidate
        candidate = candidate + _dt.timedelta(minutes=1)
    raise ValueError(f"cron spec has no matching time within 4 years: {spec}")
