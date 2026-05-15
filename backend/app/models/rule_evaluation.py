"""Rule evaluation log.

One row per (media_file, rule) match. Used for:
* the Files page detail panel ("why is this file flagged?")
* the rule edit page ("show me what this rule matched")
* downstream stages (optimization queue picks up files by rule match).

We keep only the latest evaluation per (file, rule) pair to keep the
table compact — every re-evaluation upserts the same row.
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "media_file_id", "rule_id", name="uq_rule_eval_file_rule"
        ),
        Index("ix_rule_eval_rule", "rule_id"),
        Index("ix_rule_eval_severity", "severity_rank"),
        Index("ix_rule_eval_file", "media_file_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    media_file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Severity this rule contributed for this file. We persist both the
    # label (for display) and the numeric rank (for sorting/aggregation).
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    severity_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot of the actions applied — primarily for audit, not for replay.
    actions_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
