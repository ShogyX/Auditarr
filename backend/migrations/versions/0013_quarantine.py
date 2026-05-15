"""quarantine state on media_files (Stage 27)

Revision ID: 0013_quarantine
Revises: 0012_runtime_settings
Create Date: 2026-05-12 06:00:00

Adds three columns to ``media_files`` carrying quarantine state:

* ``quarantined`` — boolean, defaults false. Indexed because
  list/filter queries will exclude quarantined files by default.
* ``quarantined_at`` — timestamp the operator quarantined the
  file. NULL when not quarantined.
* ``quarantined_reason`` — optional free-text reason
  (max 512 chars). NULL when not quarantined or when the operator
  didn't supply one.

Quarantine is distinct from:

  - ``is_orphaned`` (scanner couldn't find the file on disk)
  - ``probe_failed`` (technical failure during ffprobe)

…both of which describe automatic state. Quarantine is a
deliberate operator action: "I know about this file, it's
broken/weird/out-of-scope, leave it alone."

Default-exclusion from automation is enforced in the query layer
(``MediaFilter``) rather than at the DB level, so an operator
can still surface quarantined files explicitly when they want to
review or release them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_quarantine"
down_revision: str | None = "0012_runtime_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_files") as batch:
        batch.add_column(
            sa.Column(
                "quarantined",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "quarantined_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "quarantined_reason", sa.String(length=512), nullable=True
            )
        )
    op.create_index(
        "ix_media_files_quarantined", "media_files", ["quarantined"]
    )


def downgrade() -> None:
    op.drop_index("ix_media_files_quarantined", table_name="media_files")
    with op.batch_alter_table("media_files") as batch:
        batch.drop_column("quarantined_reason")
        batch.drop_column("quarantined_at")
        batch.drop_column("quarantined")
