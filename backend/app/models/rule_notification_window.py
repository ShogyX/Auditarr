"""Rule notification throttle window (Stage 06 v1.7).

Per plan §358:
    "Track per-rule notification timestamps in a small in-memory
    ring + DB-backed counter so throttling survives restart. New
    table ``rule_notification_window`` ``(rule_id, window_start,
    count)``. Cheap; one row per rule per active window."

When a rule with a ``Notify(throttle=...)`` action fires, the
service layer:

  1. Looks up the current window's row (or creates one with
     ``count=0``). The window is keyed by ``rule_id`` +
     ``window_start`` (the rule's throttle ``window_seconds``
     bucketing).
  2. If ``count < max_per_window``, increments and sends.
  3. If ``count >= max_per_window``, suppresses the send and
     emits ``rule.throttled`` on the bus. Per addendum A.2
     §125, ONE summary audit-log entry per window per rule
     records the suppression (not one per suppressed event).

The table is bounded: at most one row per rule per active
window. A housekeeping pass periodically deletes rows whose
``window_end`` is in the past.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class RuleNotificationWindow(Base, TimestampMixin):
    __tablename__ = "rule_notification_windows"
    __table_args__ = (
        # One active window per (rule, window_start). The service
        # layer uses INSERT ... ON CONFLICT (rule_id,
        # window_start) DO UPDATE SET count = count + 1 to
        # atomically increment without a SELECT-then-UPDATE race.
        UniqueConstraint(
            "rule_id",
            "window_start",
            name="uq_rule_notification_window_rule_start",
        ),
        # Lookup by rule_id is the hot path (the service layer
        # asks "what's this rule's current window count?" on
        # every Notify-with-throttle action).
        Index(
            "ix_rule_notification_windows_rule_id_window_start",
            "rule_id",
            "window_start",
        ),
    )

    # Surrogate PK — keeps the (rule_id, window_start) uniqueness
    # at the index level, not the PK, so housekeeping deletes
    # don't have to fight a composite primary key.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``window_start`` is the bucketed start-of-window timestamp.
    # For a throttle with ``window_seconds=300``, valid starts
    # are :00, :05, :10, etc. — the service layer floors
    # ``utcnow()`` to the window boundary before lookup. This
    # keeps the row count bounded and the suppression behaviour
    # deterministic (one window's events never bleed into the
    # next).
    window_start: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # ``window_end`` is recorded so housekeeping can prune
    # expired rows with a simple ``WHERE window_end < utcnow()``.
    # Stored explicitly (rather than computed from start +
    # seconds) because the rule's throttle config might have
    # changed since the window was created; the end is
    # immutable.
    window_end: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Current count of notifications delivered in this window.
    # The service layer increments this BEFORE sending so a
    # crash mid-send doesn't allow a duplicate on retry.
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
