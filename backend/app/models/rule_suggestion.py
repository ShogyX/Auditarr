"""Rule suggestion model (Stage 16).

A :class:`RuleSuggestion` is one machine-emitted recommendation for a
rule the operator could add. Suggestions live in their own table
(separate from :class:`app.models.rule.Rule`) so:

* the operator must explicitly deploy each one — no auto-creation
* dismissed suggestions can be tracked sticky (don't re-suggest a
  pattern the operator already rejected for 30 days)
* a deployed suggestion still keeps a back-reference to the rule it
  produced, so re-running the analyzer doesn't re-suggest a pattern
  the operator already addressed

The ``definition`` column is the same JSON shape that
:class:`app.rules.schema.RuleDefinition` validates — meaning a
suggestion can be reviewed and deployed with a single round-trip
through the Stage 15 visual rule builder.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class RuleSuggestion(Base):
    """One rule the analyzer thinks the operator should consider."""

    __tablename__ = "rule_suggestions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Human-facing summary. Always provided. The analyzer generates
    # something like "Flag HEVC 1080p files transcoded on Apple TV".
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # The RuleDefinition this suggestion would deploy. Same JSON shape
    # as Rule.definition so it round-trips through the visual builder.
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)

    # The heuristic that produced this suggestion — used to group
    # suggestions on the dashboard and to apply per-heuristic rate
    # limiting / dedup. Examples: "high_transcode_codec",
    # "bitrate_ceiling", "container_compat", "resolution_mismatch",
    # "failed_playback".
    heuristic: Mapped[str] = mapped_column(String(64), nullable=False)

    # Observed-evidence JSON. The analyzer fills this with the
    # counters and sample data that produced the suggestion. The
    # frontend renders an "Evidence" tab in the review modal from it.
    # Shape is heuristic-specific; everyone is responsible for
    # documenting their own evidence keys.
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Three numbers shown in the dashboard card's projection row.
    files_affected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    est_runtime_s: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # nullable when the rule doesn't queue optimizations
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0..1; higher = more data behind the suggestion

    # Dedup. The analyzer composes a stable key per (heuristic, attrs)
    # so re-running the same analysis doesn't insert duplicates and a
    # dismissal stays sticky for the same pattern.
    dedup_key: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True
    )

    # Lifecycle. "pending" → "deployed" or "dismissed".
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )

    # When the operator clicked Deploy → which rule did this become?
    # FK is nullable; on rule delete we SET NULL so suggestion history
    # is preserved even if the deployed rule is later removed.
    deployed_rule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    deployed_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    dismissed_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dismissed_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )
