"""media extension rules table (Stage 9 audit follow-up)

Revision ID: 0019_media_extension_rules
Revises: 0018_profile_integration_routing
Create Date: 2026-05-14 00:00:00

Adds a first-class table for per-extension scanner + rule-engine
overrides. See :mod:`app.models.extension_rule` for the four
dispositions (ignore / stats_only / malicious / accepted). NULL
disposition is invalid by NOT NULL; new rows must pick one.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_media_extension_rules"
down_revision: str | None = "0018_profile_integration_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_extension_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
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
        sa.UniqueConstraint("extension", name="uq_media_extension_rules_extension"),
    )


def downgrade() -> None:
    op.drop_table("media_extension_rules")
