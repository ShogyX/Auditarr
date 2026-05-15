"""housekeeping run history table (Stage 14 audit follow-up)

Revision ID: 0020_housekeeping_runs
Revises: 0019_media_extension_rules
Create Date: 2026-05-14 00:00:00

Records each housekeeping run (cron-scheduled or admin-triggered)
so the Settings page can surface "Last run" + counts. See
:mod:`app.models.housekeeping_run`. No retention policy on this
table itself — rows are tiny and history matters.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_housekeeping_runs"
down_revision: str | None = "0019_media_extension_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "housekeeping_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "deliveries_deleted",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "update_checks_deleted",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "rule_evaluations_deleted",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "job_runs_deleted",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("housekeeping_runs")
