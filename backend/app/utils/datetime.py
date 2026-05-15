"""Datetime helpers that paper over SQLite's lack of tz-aware columns."""

from __future__ import annotations

import datetime as _dt


def utcnow() -> _dt.datetime:
    """Return the current UTC time, always tz-aware."""
    return _dt.datetime.now(_dt.UTC)


def ensure_aware(value: _dt.datetime | None) -> _dt.datetime | None:
    """Promote a naive datetime to UTC-aware (SQLite reads return naive)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.UTC)
    return value


def is_past(value: _dt.datetime | None) -> bool:
    """True iff *value* (treated as UTC if naive) is in the past."""
    if value is None:
        return True
    aware = ensure_aware(value)
    assert aware is not None
    return aware < utcnow()
