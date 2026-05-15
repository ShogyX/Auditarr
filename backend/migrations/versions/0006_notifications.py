"""notification channels + deliveries

Revision ID: 0006_notifications
Revises: 0005_automation
Create Date: 2026-05-10 22:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_notifications"
down_revision: str | None = "0005_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_ciphertext", sa.Text(), nullable=True),
        sa.Column("min_severity_rank", sa.Integer(), nullable=False),
        sa.Column("last_delivery_status", sa.String(length=16), nullable=True),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notification_channels"),
        sa.UniqueConstraint("name", name="uq_notification_channels_name"),
    )
    op.create_index(
        "ix_notification_channels_name",
        "notification_channels",
        ["name"],
        unique=False,
    )
    op.create_index(
        "ix_notification_channels_kind",
        "notification_channels",
        ["kind"],
        unique=False,
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=True),
        sa.Column("channel_name", sa.String(length=120), nullable=False),
        sa.Column("channel_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name="fk_notif_delivery_channel",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_notif_delivery_channel",
        "notification_deliveries",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        "ix_notif_delivery_status",
        "notification_deliveries",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_notif_delivery_attempted_at",
        "notification_deliveries",
        ["attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notif_delivery_attempted_at", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notif_delivery_status", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notif_delivery_channel", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_index(
        "ix_notification_channels_kind", table_name="notification_channels"
    )
    op.drop_index(
        "ix_notification_channels_name", table_name="notification_channels"
    )
    op.drop_table("notification_channels")
