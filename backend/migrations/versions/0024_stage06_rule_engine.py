"""stage 06 v1.7 — rule engine extensions

Revision ID: 0024_stage06_rule_engine
Revises: 0023_drop_quarantine
Create Date: 2026-05-16 14:00:00

Three schema additions to support Stage 06 (Rule engine
extensions — plan §343-384 + addendum):

1. ``media_files.vt_status`` column. String(16), nullable. Per
   addendum B.4: the VT plugin (Stage 10) populates this column
   with one of the five canonical values defined in
   ``app.rules.schema.VT_STATUS_VALUES``. Stage 06 adds the
   column so the built-in "VirusTotal non-clean" rule has
   somewhere to look. The column is indexed because the rule
   engine filters on it every evaluation pass.

2. ``ix_media_files_probe_failed`` index on the existing
   ``probe_failed`` boolean column. The Stage 06 built-in
   "Probe failed" rule scans every library row on each
   evaluation pass; an index keeps the predicate cheap as
   libraries scale.

3. ``rule_notification_windows`` table. Per plan §358: a
   per-(rule, window) counter that lets the throttle survive
   restarts. Unique constraint on (rule_id, window_start) so
   the service layer can do INSERT ON CONFLICT DO UPDATE for
   atomic increments. ON DELETE CASCADE from ``rules.id`` so
   deleting a rule cleans up its history.

The downgrade reverses all three.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_stage06_rule_engine"
down_revision: str | None = "0023_drop_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add ``vt_status`` column to ``media_files``.
    # Nullable because most rows have no VT lookup result; the
    # ``not_found`` literal is distinct from NULL ("never
    # looked up").
    with op.batch_alter_table("media_files") as batch:
        batch.add_column(
            sa.Column("vt_status", sa.String(length=16), nullable=True)
        )

    # 2. Index on vt_status (predicate selectivity is high — most
    # rows are NULL, the few non-NULL ones cluster around
    # malicious/suspicious).
    op.create_index(
        "ix_media_files_vt_status", "media_files", ["vt_status"]
    )

    # 3. Index on the existing ``probe_failed`` column. The
    # column was added in an earlier stage; Stage 06 wires it
    # into the rule engine and adds the supporting index.
    op.create_index(
        "ix_media_files_probe_failed", "media_files", ["probe_failed"]
    )

    # 4. ``rule_notification_windows`` table (Stage 06 throttle
    # state). One row per (rule, active window). Composite
    # uniqueness on (rule_id, window_start) drives the
    # increment-or-create pattern in the service layer.
    op.create_table(
        "rule_notification_windows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "rule_id",
            sa.String(length=36),
            sa.ForeignKey("rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "window_end",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # ``TimestampMixin`` columns (created_at, updated_at).
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
        sa.UniqueConstraint(
            "rule_id",
            "window_start",
            name="uq_rule_notification_window_rule_start",
        ),
    )
    op.create_index(
        "ix_rule_notification_windows_rule_id_window_start",
        "rule_notification_windows",
        ["rule_id", "window_start"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_notification_windows_rule_id_window_start",
        table_name="rule_notification_windows",
    )
    op.drop_table("rule_notification_windows")
    op.drop_index(
        "ix_media_files_probe_failed", table_name="media_files"
    )
    op.drop_index("ix_media_files_vt_status", table_name="media_files")
    with op.batch_alter_table("media_files") as batch:
        batch.drop_column("vt_status")
