"""Schedule model.

An operator-configured cron entry that fires a named job from the job
catalogue on a cadence. Each row is one schedule; the scheduler service
(``app.automation.scheduler``) reads enabled schedules every minute and
enqueues runs whose ``next_run_at`` has passed.

The ``cron`` column is a simple JSON document, not a crontab string: we
already have ARQ doing minute-resolution scheduling and don't want to
parse arbitrary cron in front of it. Supported keys are exactly the
fields ``arq.cron`` accepts (``minute``, ``hour``, ``day``, ``month``,
``weekday``). When none are set the schedule fires every minute (mostly
useful for tests; the UI nudges operators toward something less noisy).
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Catalogue key — e.g. ``scan_library``, ``sync_integration_tags``.
    job_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Frozen arguments bound to the job invocation, e.g. ``{"library_id": "..."}``.
    job_args: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Cron-like spec; see app.automation.scheduler.
    cron: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Filled in by the scheduler. ``next_run_at`` is the planned next firing;
    # ``last_run_at`` and ``last_status`` give the UI a recency indicator.
    next_run_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Per-schedule timeout, in seconds. The job catalogue can override
    # with a lower bound; this acts as the operator-visible knob.
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=3600, nullable=False
    )
