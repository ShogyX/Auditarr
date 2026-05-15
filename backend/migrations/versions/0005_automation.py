"""schedules + job runs + optimization queue

Revision ID: 0005_automation
Revises: 0004_rules
Create Date: 2026-05-10 22:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_automation"
down_revision: str | None = "0004_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("job_kind", sa.String(length=64), nullable=False),
        sa.Column("job_args", sa.JSON(), nullable=False),
        sa.Column("cron", sa.JSON(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_schedules"),
        sa.UniqueConstraint("name", name="uq_schedules_name"),
    )
    op.create_index("ix_schedules_name", "schedules", ["name"], unique=False)
    op.create_index("ix_schedules_job_kind", "schedules", ["job_kind"], unique=False)

    op.create_table(
        "job_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=True),
        sa.Column("job_kind", sa.String(length=64), nullable=False),
        sa.Column("job_args", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_runs"),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            name="fk_job_runs_schedule",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_job_runs_job_kind", "job_runs", ["job_kind"], unique=False)
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"], unique=False)
    op.create_index("ix_job_runs_status", "job_runs", ["status"], unique=False)
    op.create_index("ix_job_runs_schedule", "job_runs", ["schedule_id"], unique=False)

    op.create_table(
        "optimization_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_file_id", sa.String(length=36), nullable=False),
        sa.Column("profile", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_by_rule_id", sa.String(length=36), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_metadata", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_items"),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_files.id"],
            name="fk_optimization_media_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["queued_by_rule_id"],
            ["rules.id"],
            name="fk_optimization_rule",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "media_file_id", "profile", name="uq_optimization_file_profile"
        ),
    )
    op.create_index(
        "ix_optimization_status", "optimization_items", ["status"], unique=False
    )
    op.create_index(
        "ix_optimization_profile", "optimization_items", ["profile"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_optimization_profile", table_name="optimization_items")
    op.drop_index("ix_optimization_status", table_name="optimization_items")
    op.drop_table("optimization_items")
    op.drop_index("ix_job_runs_schedule", table_name="job_runs")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_started_at", table_name="job_runs")
    op.drop_index("ix_job_runs_job_kind", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_index("ix_schedules_job_kind", table_name="schedules")
    op.drop_index("ix_schedules_name", table_name="schedules")
    op.drop_table("schedules")
