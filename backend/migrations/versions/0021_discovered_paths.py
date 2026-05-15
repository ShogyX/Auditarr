"""integration.discovered_paths column (Stage 17 audit follow-up)

Revision ID: 0021_discovered_paths
Revises: 0020_housekeeping_runs
Create Date: 2026-05-15 00:00:00

Stores a snapshot of libraries discovered from each integration's
upstream so the Path Mappings panel can highlight unmapped paths
without re-hitting the upstream on every page render. See
:class:`app.models.integration.Integration.discovered_paths`.
Nullable on purpose — existing rows stay ``NULL`` until either the
operator triggers a manual rediscover or the integration is
re-created.

NB: this revision ID is intentionally short. Alembic's
``alembic_version.version_num`` column is ``varchar(32)`` by
default; the original name ``0021_integration_discovered_paths``
was 33 chars and got truncated mid-write on Postgres, producing
a ``StringDataRightTruncationError``. SQLite (used by the test
suite) doesn't enforce VARCHAR length, so the issue only
surfaced on real Postgres deploys. See
``tests/unit/test_migration_ids.py`` for the regression guard.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_discovered_paths"
down_revision: str | None = "0020_housekeeping_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("discovered_paths", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integrations", "discovered_paths")
