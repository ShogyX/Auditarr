"""integrations

Revision ID: 0003_integrations
Revises: 0002_media_core
Create Date: 2026-05-10 20:30:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_integrations"
down_revision: str | None = "0002_media_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_ciphertext", sa.Text(), nullable=True),
        sa.Column("health_status", sa.String(length=16), nullable=False),
        sa.Column("health_detail", sa.String(length=512), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_integrations"),
        sa.UniqueConstraint("name", name="uq_integrations_name"),
    )
    op.create_index("ix_integrations_name", "integrations", ["name"], unique=False)
    op.create_index("ix_integrations_kind", "integrations", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_integrations_kind", table_name="integrations")
    op.drop_index("ix_integrations_name", table_name="integrations")
    op.drop_table("integrations")
