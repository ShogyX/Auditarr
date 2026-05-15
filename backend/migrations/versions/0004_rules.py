"""rules + rule evaluations

Revision ID: 0004_rules
Revises: 0003_integrations
Create Date: 2026-05-10 21:15:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_rules"
down_revision: str | None = "0003_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_match_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rules"),
        sa.UniqueConstraint("name", name="uq_rules_name"),
    )
    op.create_index("ix_rules_name", "rules", ["name"], unique=False)

    op.create_table(
        "rule_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_file_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("severity_rank", sa.Integer(), nullable=False),
        sa.Column("actions_summary", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rule_evaluations"),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_files.id"],
            name="fk_rule_eval_media_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rules.id"],
            name="fk_rule_eval_rule",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "media_file_id", "rule_id", name="uq_rule_eval_file_rule"
        ),
    )
    op.create_index("ix_rule_eval_rule", "rule_evaluations", ["rule_id"], unique=False)
    op.create_index(
        "ix_rule_eval_severity", "rule_evaluations", ["severity_rank"], unique=False
    )
    op.create_index(
        "ix_rule_eval_file", "rule_evaluations", ["media_file_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_rule_eval_file", table_name="rule_evaluations")
    op.drop_index("ix_rule_eval_severity", table_name="rule_evaluations")
    op.drop_index("ix_rule_eval_rule", table_name="rule_evaluations")
    op.drop_table("rule_evaluations")
    op.drop_index("ix_rules_name", table_name="rules")
    op.drop_table("rules")
