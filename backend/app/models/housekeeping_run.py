"""Housekeeping run history.

Stage 14 (audit follow-up). The :class:`HousekeepingService` was
previously fire-and-forget — operators had no way to see when it
last ran or what it deleted. This table records each run (cron-
scheduled or manual via the new admin endpoint) so the Settings
page can surface "Last run: <timestamp> — deleted N rows" and so
the audit trail of cleanup activity is durable.

One row per run. Rows are NOT trimmed by the housekeeping service
itself — they're cheap (a handful of integer counters per day) and
operators legitimately want history beyond the standard retention
windows ("when did we stop deleting evaluations?").
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class HousekeepingRun(Base):
    __tablename__ = "housekeeping_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # ``manual`` (admin-triggered via the new run-now endpoint) or
    # ``scheduled`` (cron tick). The distinction is purely for the
    # audit trail.
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled"
    )
    started_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deliveries_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    update_checks_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    rule_evaluations_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    job_runs_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)


__all__ = ["HousekeepingRun"]
