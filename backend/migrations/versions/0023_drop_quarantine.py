"""drop quarantine state + rewrite quarantine rule actions (Stage 05 v1.7)

Revision ID: 0023_drop_quarantine
Revises: 0022_webhook_secret_hash_vt
Create Date: 2026-05-16 12:00:00

Stage 05 retired the quarantine workflow entirely (Section A.0 of
the v1.7 addendum — "delete means delete"). This migration handles
the data side of that retirement:

1. **Rewrite persisted rule definitions** that reference
   ``type: "quarantine"`` actions to ``type: "delete"``. The
   pre-Stage-05 Quarantine action class is gone from the DSL;
   without rewriting, the next ``RuleDefinition.model_validate``
   on those rules would fail validation and the row would be
   unloadable. The original ``reason`` (if any) is preserved on
   the new Delete action.

2. **Drop the three quarantine columns** from ``media_files``:
   ``quarantined``, ``quarantined_at``, ``quarantined_reason``.
   The associated index ``ix_media_files_quarantined`` is also
   dropped.

3. **The pre-Stage-05 ``Delete`` action also carried a ``confirm``
   flag** which Stage 05 retired (delete is now always
   unconditional). Persisted Delete actions with ``confirm: True``
   are passed through cleanly (the flag is stripped); persisted
   Delete actions with ``confirm: False`` are rewritten to
   match the new "always hard delete" semantics — that's a
   deliberate behaviour change called out in the addendum.
   Operators with a Delete(confirm=False) action effectively had
   a soft-delete-via-quarantine; that path is gone. The rule's
   delete action now hard-deletes when matched.

The downgrade restores the columns + index but does NOT restore
the original rule body shape. A downgrade after operator usage
would lose the quarantined state of any files; this is a
forward-only migration in practice. The downgrade exists for
local-dev iteration only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_drop_quarantine"
down_revision: str | None = "0022_webhook_secret_hash_vt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rewrite_actions(actions: list[dict]) -> tuple[list[dict], bool]:
    """Rewrite a list of action dicts to the Stage 05 schema.

    Returns ``(new_actions, changed)`` so the caller can skip
    rows that don't need updating.

    * ``type: "quarantine"`` → ``type: "delete"`` with ``reason``
      preserved.
    * ``type: "delete"`` with a ``confirm`` key → ``confirm`` is
      stripped (Stage 05 delete is unconditional).
    * All other action types pass through untouched.
    """
    out: list[dict] = []
    changed = False
    for action in actions:
        if not isinstance(action, dict):
            # Defensive: a malformed rule body wouldn't have
            # validated in the first place. Leave it alone — the
            # rules loader will fail loudly later, which is the
            # right operator signal.
            out.append(action)
            continue
        t = action.get("type")
        if t == "quarantine":
            new_action: dict = {"type": "delete"}
            if "reason" in action and action["reason"] is not None:
                new_action["reason"] = action["reason"]
            out.append(new_action)
            changed = True
        elif t == "delete":
            new_action = {"type": "delete"}
            if "reason" in action and action["reason"] is not None:
                new_action["reason"] = action["reason"]
            # Drop ``confirm`` if present (Stage 05 removes the flag).
            if "confirm" in action:
                changed = True
            out.append(new_action)
        else:
            out.append(action)
    return out, changed


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Rewrite stored rule definitions BEFORE dropping any
    # schema. The ``rules.definition`` column is a JSON blob; we
    # rewrite per-row to preserve the rest of the body.
    rules_table = sa.table(
        "rules",
        sa.column("id", sa.String),
        sa.column("definition", sa.JSON),
    )
    rows = bind.execute(sa.select(rules_table.c.id, rules_table.c.definition))
    for row in rows.fetchall():
        rule_id, raw_definition = row
        # SQLAlchemy's JSON column returns a parsed dict in most
        # backends; SQLite may return a string. Normalise.
        if isinstance(raw_definition, str):
            try:
                definition = json.loads(raw_definition)
            except json.JSONDecodeError:
                # Corrupt body — leave it for the rule loader to
                # surface. Skip rewriting.
                continue
        else:
            definition = raw_definition
        if not isinstance(definition, dict):
            continue
        actions = definition.get("actions")
        if not isinstance(actions, list):
            continue
        new_actions, changed = _rewrite_actions(actions)
        if changed:
            definition["actions"] = new_actions
            bind.execute(
                sa.update(rules_table)
                .where(rules_table.c.id == rule_id)
                .values(definition=definition)
            )

    # 2. Drop the index + quarantine columns.
    op.drop_index("ix_media_files_quarantined", table_name="media_files")
    with op.batch_alter_table("media_files") as batch:
        batch.drop_column("quarantined_reason")
        batch.drop_column("quarantined_at")
        batch.drop_column("quarantined")


def downgrade() -> None:
    # Restore the columns + index. Stored rule bodies are NOT
    # restored — the quarantine action class is gone from the
    # codebase, so re-adding ``type: "quarantine"`` rules would
    # immediately fail to load. The downgrade exists for local-
    # dev iteration; in production, this is a forward-only
    # migration.
    with op.batch_alter_table("media_files") as batch:
        batch.add_column(
            sa.Column(
                "quarantined",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "quarantined_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "quarantined_reason", sa.String(length=512), nullable=True
            )
        )
    op.create_index(
        "ix_media_files_quarantined", "media_files", ["quarantined"]
    )
