"""runtime_setting_changes audit log (Stage 2)

Revision ID: 0015_runtime_setting_changes
Revises: 0014_rule_is_builtin
Create Date: 2026-05-12 21:30:00

Creates the ``runtime_setting_changes`` table. Append-only audit log
for every runtime-setting override change. Each row records:

* ``key`` — the setting that changed
* ``prev_value`` — the value before the change (``NULL`` if the
  override was created from the env default)
* ``next_value`` — the value after the change (``NULL`` for a clear)
* ``set_by_user_id`` — the operator (admin) who made the change
* ``set_at`` — when

The table is indexed on ``(key, set_at)`` to support the
``GET /system/runtime-settings/{key}/history`` endpoint, which lists
recent changes for a key in reverse chronological order.

No retention policy in this migration — the table is expected to
stay small because runtime-setting changes are operator-driven and
infrequent. A future housekeeping job can add retention if usage
patterns prove otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_runtime_setting_changes"
down_revision: str | None = "0014_rule_is_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_setting_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("prev_value", sa.JSON(), nullable=True),
        sa.Column("next_value", sa.JSON(), nullable=True),
        sa.Column("set_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_runtime_setting_changes_key",
        "runtime_setting_changes",
        ["key"],
    )
    op.create_index(
        "ix_runtime_setting_changes_set_at",
        "runtime_setting_changes",
        ["set_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_setting_changes_set_at",
        table_name="runtime_setting_changes",
    )
    op.drop_index(
        "ix_runtime_setting_changes_key",
        table_name="runtime_setting_changes",
    )
    op.drop_table("runtime_setting_changes")
