"""optimization profile routing column (Stage 7 audit follow-up)

Revision ID: 0018_profile_integration_routing
Revises: 0017_global_path_mappings
Create Date: 2026-05-14 00:00:00

Adds a nullable ``optimization_integration_id`` column to
``optimization_profiles``. When set, the worker will dispatch jobs for
this profile to the named integration (Tdarr, future Unmanic plugin,
etc.) instead of the in-process ffmpeg runner. When NULL (the default
and every existing row's value), the in-process runner takes the job
— preserving pre-Stage-7 behaviour.

The column is intentionally NOT a foreign key — see the docstring on
``OptimizationProfile.optimization_integration_id`` for why.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_profile_integration_routing"
down_revision: str | None = "0017_global_path_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "optimization_profiles",
        sa.Column(
            "optimization_integration_id",
            sa.String(length=36),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "optimization_profiles", "optimization_integration_id"
    )
