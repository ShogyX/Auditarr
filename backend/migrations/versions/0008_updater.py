"""update checks + applies

Revision ID: 0008_updater
Revises: 0007_optimization
Create Date: 2026-05-11 05:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_updater"
down_revision: str | None = "0007_optimization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "update_checks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("latest_version", sa.String(length=64), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("feed_url", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_update_checks"),
    )
    op.create_index(
        "ix_update_checks_checked_at",
        "update_checks",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        "ix_update_checks_ok", "update_checks", ["ok"], unique=False
    )

    op.create_table(
        "update_applies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("from_version", sa.String(length=64), nullable=True),
        sa.Column("to_version", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "triggered_by_user_id", sa.String(length=36), nullable=True
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_update_applies"),
    )
    op.create_index(
        "ix_update_applies_started_at",
        "update_applies",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_update_applies_status",
        "update_applies",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_update_applies_status", table_name="update_applies")
    op.drop_index("ix_update_applies_started_at", table_name="update_applies")
    op.drop_table("update_applies")
    op.drop_index("ix_update_checks_ok", table_name="update_checks")
    op.drop_index("ix_update_checks_checked_at", table_name="update_checks")
    op.drop_table("update_checks")
