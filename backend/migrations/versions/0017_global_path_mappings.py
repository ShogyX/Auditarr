"""global path mappings table (Stage 5 audit follow-up)

Revision ID: 0017_global_path_mappings
Revises: 0016_trim_library_paths
Create Date: 2026-05-14 00:00:00

Adds a first-class table for global path mappings. Per-integration
mappings (stored on ``Integration.config.path_mappings`` as JSON) are
unchanged — the global table is an additional layer applied AFTER the
per-integration ones during path resolution. See
``app/integrations/path_mapping.py`` for the resolution chain.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_global_path_mappings"
down_revision: str | None = "0016_trim_library_paths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "global_path_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("from_path", sa.String(length=1024), nullable=False),
        sa.Column("to_path", sa.String(length=1024), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Common query: "give me every enabled mapping ordered by
    # priority". The index keeps this cheap even with thousands of
    # rows (unlikely but cheap to support).
    op.create_index(
        "ix_global_path_mappings_enabled_priority",
        "global_path_mappings",
        ["enabled", "priority"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_global_path_mappings_enabled_priority",
        table_name="global_path_mappings",
    )
    op.drop_table("global_path_mappings")
