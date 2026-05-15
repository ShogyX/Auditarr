"""plugin settings

Revision ID: 0009_plugin_settings
Revises: 0008_updater
Create Date: 2026-05-11 06:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_plugin_settings"
down_revision: str | None = "0008_updater"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plugin_id", sa.String(length=64), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plugin_settings"),
        sa.UniqueConstraint(
            "plugin_id", name="uq_plugin_settings_plugin_id"
        ),
    )
    op.create_index(
        "ix_plugin_settings_plugin_id",
        "plugin_settings",
        ["plugin_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plugin_settings_plugin_id", table_name="plugin_settings"
    )
    op.drop_table("plugin_settings")
