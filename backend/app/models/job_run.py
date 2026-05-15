"""Job run audit log.

One row per job execution. Created when the scheduler dispatches a job
(``status='queued'``), updated on transition to ``running``/``completed``/
``failed`` by the worker. Powers the **Automation → Run history** view
and is the source of truth for "what fired last night and why".

We keep this table compact by capping retention to ~30 days of runs at
the housekeeping job (Stage 13 follow-up). For now, rows accumulate.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_started_at", "started_at"),
        Index("ix_job_runs_status", "status"),
        Index("ix_job_runs_schedule", "schedule_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # ``schedule_id`` is nullable: jobs triggered manually from the UI
    # (or by an integration's healthcheck hook) don't belong to a schedule.
    schedule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_args: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        doc="queued | running | completed | failed | cancelled",
    )
    started_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Source of the trigger: ``schedule`` | ``manual`` | ``scanner`` | ``rule``
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
