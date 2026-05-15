"""Scan run model.

Recorded for each scan invocation. Lets the UI show recent scans, surfaces
errors in the audit log, and gives the scheduler a `last_scan_at` anchor.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class ScanRun(Base, TimestampMixin):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    library_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="full",
        doc="full | incremental | targeted | rescan",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        doc="queued | running | completed | failed | cancelled",
    )
    started_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    files_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_orphaned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    probe_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
