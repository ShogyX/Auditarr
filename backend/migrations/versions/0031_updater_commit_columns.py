"""v1.9.x — add commit identity columns to update_checks.

The updater now tracks the latest commit on a configured branch
(default ``main``) instead of the latest release tag. ``UpdateCheck``
rows therefore need to carry the commit SHA + commit date alongside
the existing ``latest_version`` column, which is still populated when
the feed is operating in release-tag mode.

Both columns are nullable: existing rows (release-tag checks before
this migration) keep working unchanged, and feeds that don't expose
commit metadata still produce valid rows.

The migration is column-additive only — no data backfill, no index
changes — so it is safe to run in either direction with no downtime.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031_updater_commit_columns"
down_revision = "0030_playback_session_rating_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "update_checks",
        sa.Column("latest_commit_sha", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "update_checks",
        sa.Column(
            "latest_commit_date",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("update_checks", "latest_commit_date")
    op.drop_column("update_checks", "latest_commit_sha")
