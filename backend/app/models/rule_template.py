"""Rule template model (v1.9 Stage 4.4).

A ``RuleTemplate`` is a reference-quality rule body that operators
can copy ("use template") to create their own normal ``Rule`` row.
Templates do NOT evaluate against media on their own — they live
exclusively in this table as starting points.

# Why a separate table?

Pre-1.9 the codebase shipped ``BuiltinRule`` definitions that were
seeded into the ``rules`` table with ``is_builtin=True``. Operators
could toggle them but not edit their bodies. That model had two
recurring complaints:

  1. Operators with bespoke libraries wanted to TWEAK the
     built-in's logic — change a threshold, narrow the match,
     add a tag — not just toggle it. The pre-1.9 workflow
     (Duplicate-as-custom → edit copy) worked but was clunky and
     left the operator with two near-identical-name rows in
     their list.
  2. Operators turning off a built-in lost the ability to find
     it later. "Restore deleted built-ins" needed a re-seed
     pathway that wouldn't clobber operator-tuned ``enabled``
     state on the other 15 builtins.

The template model fixes both: every shipped Auditarr rule lives
in this table; operators see them all in a new "Templates" tab on
the Rules page; clicking "Use template" inserts a normal ``Rule``
row that they own and can edit freely.

# Coexistence with the pre-1.9 builtin model

Stage 4.4 ships templates as a NEW surface. The existing
``Rule.is_builtin=True`` rows continue to seed and continue to
evaluate. Operators who never visit the Templates tab see no
behavior change. A future stage will migrate the existing builtin
rows into operator-owned copies + deprecate ``Rule.is_builtin``;
that migration carries operator-visible risk and is gated on more
testing.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class RuleTemplate(Base, TimestampMixin):
    """A shipped rule template. Re-seeded on every startup.

    Keying:
      * ``name`` is unique — the seed merge uses it as the
        upsert key. Operators don't author templates; the
        codebase is the source of truth.
      * ``definition`` holds the same DSL document as
        ``Rule.definition`` (validated by app.rules.schema).
      * ``priority`` is the SUGGESTED priority — when an
        operator clicks "Use template", the created ``Rule``
        copies this verbatim. They can tune it after.
    """

    __tablename__ = "rule_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Suggested priority for the created Rule. Mirrors the
    # built-in's priority so a template-created copy behaves
    # the same as the legacy built-in would.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    # The DSL body — same shape as Rule.definition, validated by
    # app.rules.schema on load.
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # When the template was last re-seeded — useful for
    # "restore" + debugging which startup wrote the current row.
    seeded_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


__all__ = ["RuleTemplate"]
