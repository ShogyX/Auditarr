"""Optimization queue.

When the rules engine matches a file with a ``queue_optimization`` action,
an :class:`OptimizationItem` row is upserted. Stage 10 (Optimization
system) will read from this queue to drive transcodes, repacks, etc.

We keep the schema deliberately minimal here so Stage 10 can decorate it
with its own metadata (workers, attempts, output paths) without a
breaking change — anything Stage 10 needs that isn't already an integer
or a JSON dict can go in ``metadata``.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class OptimizationItem(Base, TimestampMixin):
    __tablename__ = "optimization_items"
    __table_args__ = (
        # One (file, profile) tuple per queue. Re-evaluating the rule
        # upserts rather than duplicating.
        UniqueConstraint(
            "media_file_id", "profile", name="uq_optimization_file_profile"
        ),
        Index("ix_optimization_status", "status"),
        Index("ix_optimization_profile", "profile"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    media_file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stage 10 owns this state machine. Stage 7 only sets ``queued``.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        doc="queued | running | completed | failed | cancelled | skipped",
    )
    # Audit: which rule queued this item.
    queued_by_rule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    queued_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 0..100, updated by the worker as ffmpeg progresses.
    progress_pct: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # Bytes before/after for the UI's "saved 4.2 GB" indicator.
    original_size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    optimized_size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Path the swap-with-backup left the pre-transcode file at, in case
    # the operator wants to undo a recent run. ``None`` when no backup
    # was kept (e.g. ``keep_backup=false`` on the profile).
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form Stage 10 metadata: ffmpeg argv used, probe diffs, etc.
    item_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
