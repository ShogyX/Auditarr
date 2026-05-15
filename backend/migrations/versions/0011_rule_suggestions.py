"""rule suggestions (Stage 16 Turn 2)

Revision ID: 0011_rule_suggestions
Revises: 0010_playback_telemetry
Create Date: 2026-05-11 12:00:00

Adds the ``rule_suggestions`` table backing the data-driven rule
recommendation feature. One row per suggestion the analyzer emits;
``status`` tracks lifecycle (pending → deployed | dismissed) and
``dedup_key`` keeps re-runs idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_rule_suggestions"
down_revision: str | None = "0010_playback_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_suggestions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("heuristic", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("files_affected", sa.Integer(), nullable=False),
        sa.Column("est_runtime_s", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("dedup_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deployed_rule_id", sa.String(length=36), nullable=True),
        sa.Column(
            "deployed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "dismissed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rule_suggestions"),
        sa.ForeignKeyConstraint(
            ["deployed_rule_id"],
            ["rules.id"],
            name="fk_rule_suggestions_deployed_rule_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "dedup_key", name="uq_rule_suggestions_dedup_key"
        ),
    )
    op.create_index(
        "ix_rule_suggestions_status",
        "rule_suggestions",
        ["status"],
    )
    op.create_index(
        "ix_rule_suggestions_heuristic",
        "rule_suggestions",
        ["heuristic"],
    )
    op.create_index(
        "ix_rule_suggestions_created_at",
        "rule_suggestions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_suggestions_created_at", table_name="rule_suggestions"
    )
    op.drop_index(
        "ix_rule_suggestions_heuristic", table_name="rule_suggestions"
    )
    op.drop_index(
        "ix_rule_suggestions_status", table_name="rule_suggestions"
    )
    op.drop_table("rule_suggestions")
