"""optimization profiles + worker fields

Revision ID: 0007_optimization
Revises: 0006_notifications
Create Date: 2026-05-10 23:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_optimization"
down_revision: str | None = "0006_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "optimization_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("max_input_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_profiles"),
        sa.UniqueConstraint(
            "name", name="uq_optimization_profiles_name"
        ),
    )
    op.create_index(
        "ix_optimization_profiles_name",
        "optimization_profiles",
        ["name"],
        unique=False,
    )

    # Extra columns on the existing optimization_items table. Each is
    # nullable or defaulted so the migration adds no breakage for the
    # rows Stage 7 has already written.
    with op.batch_alter_table("optimization_items") as batch:
        batch.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "progress_pct",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("original_size_bytes", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("optimized_size_bytes", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("backup_path", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("optimization_items") as batch:
        batch.drop_column("backup_path")
        batch.drop_column("optimized_size_bytes")
        batch.drop_column("original_size_bytes")
        batch.drop_column("progress_pct")
        batch.drop_column("finished_at")
        batch.drop_column("started_at")

    op.drop_index(
        "ix_optimization_profiles_name", table_name="optimization_profiles"
    )
    op.drop_table("optimization_profiles")
