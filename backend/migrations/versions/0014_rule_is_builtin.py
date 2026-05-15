"""is_builtin on rules (Stage 29)

Revision ID: 0014_rule_is_builtin
Revises: 0013_quarantine
Create Date: 2026-05-12 11:00:00

Adds an ``is_builtin`` column to ``rules``. ``True`` marks rules
seeded by Auditarr at startup from :mod:`app.rules.builtin`. The
API layer prevents operators from renaming, editing the body, or
deleting built-in rules — they can toggle ``enabled`` and adjust
``priority`` to fit their installation, but the definition itself
is owned by the codebase.

The column is indexed because the Rules page filter ("show only
built-in" / "show only custom") drives a list query keyed on it.

Existing rows are ``is_builtin=False`` (the column default at
the DB level handles legacy rows during the upgrade).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_rule_is_builtin"
down_revision: str | None = "0013_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.add_column(
            sa.Column(
                "is_builtin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.create_index("ix_rules_is_builtin", "rules", ["is_builtin"])


def downgrade() -> None:
    op.drop_index("ix_rules_is_builtin", table_name="rules")
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("is_builtin")
