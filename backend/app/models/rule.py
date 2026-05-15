"""Rule model.

Each rule is a named, optionally-disabled definition. The ``definition``
column holds the structured DSL document the evaluator consumes; the
shape is validated by :mod:`app.rules.schema` whenever the rule is loaded
or saved. Denormalized ``last_evaluated_at``/``last_match_count`` give the
UI a cheap "did this rule do anything recently?" indicator.

Stage 29 adds ``is_builtin``: rules with this flag are seeded by
Auditarr at startup from :mod:`app.rules.builtin` rather than created
by operators. They get protection at the API layer — operators can
toggle ``enabled`` and ``priority`` to fit their installation but
cannot rename, edit the body, or delete them. Operators who want a
divergent variant duplicate a builtin (the copy is a normal custom
rule with ``is_builtin=False``).
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
        doc="Lower runs first. Rules are deterministic regardless of order, "
        "but priority controls the visible evaluation log order in the UI.",
    )

    # Structured rule body: see app.rules.schema.
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Stage 29: True for rules seeded from app.rules.builtin at startup.
    # API layer enforces: cannot rename / re-body / delete. The flag is
    # indexed because the Rules page UI filters by it.
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    last_evaluated_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_match_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
