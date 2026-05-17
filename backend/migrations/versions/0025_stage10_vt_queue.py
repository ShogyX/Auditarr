"""stage 10 v1.7 — vt_queue table

Revision ID: 0025_stage10_vt_queue
Revises: 0024_stage06_rule_engine
Create Date: 2026-05-16 18:00:00

One schema addition: ``vt_queue`` table.

Per plan §515 — when the VT integration is enabled, the scanner
enqueues files for VT lookup. The queue exists so operators can
SEE how many files are pending lookup (the
``GET /api/v1/integrations/virustotal/status`` endpoint reads
``COUNT(*)`` here).

Schema:
    * ``media_file_id``   — TEXT PK, FK→media_files.id ON DELETE
                           CASCADE. PK because each file is in
                           the queue at most once; the ON DELETE
                           CASCADE keeps the queue consistent
                           with media_files when scans remove
                           rows.
    * ``enqueued_at``     — DATETIME NOT NULL, defaults to UTC
                           now at insert time.
    * ``last_attempted_at`` — DATETIME, nullable; populated only
                           when the (future) drain worker has
                           tried a lookup.
    * ``attempt_count``   — INTEGER NOT NULL DEFAULT 0.

Plus index ``ix_vt_queue_enqueued_at`` so the drain worker (a
later stage) can SELECT in FIFO order without a full scan.

The downgrade drops the table + index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_stage10_vt_queue"
down_revision: str | None = "0024_stage06_rule_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vt_queue",
        sa.Column(
            "media_file_id",
            sa.String(length=36),
            sa.ForeignKey("media_files.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_attempted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_vt_queue_enqueued_at",
        "vt_queue",
        ["enqueued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vt_queue_enqueued_at", table_name="vt_queue")
    op.drop_table("vt_queue")
