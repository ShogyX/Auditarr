"""v1.9 Stage 9.1 — playback_devices table.

Adds the device index. One row per (integration, client_key).
Populated lazily by the playback poller on every event ingested
— there's no backfill of historical events on first deploy
(the events lack stable client_keys; a 30-day window of new
plays is enough to rebuild the index after upgrade).

Purely additive: no existing rows are touched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_stage9_1_playback_devices"
down_revision = "0028_stage4_4_rule_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playback_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "integration_id",
            sa.String(36),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("platform", sa.String(64), nullable=True),
        sa.Column("product", sa.String(128), nullable=True),
        sa.Column("device_model", sa.String(128), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "playback_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "transcode_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "direct_play_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "direct_stream_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.UniqueConstraint(
            "integration_id",
            "client_key",
            name="uq_playback_devices_integration_client",
        ),
    )
    op.create_index(
        "ix_playback_devices_last_seen",
        "playback_devices",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playback_devices_last_seen", table_name="playback_devices"
    )
    op.drop_table("playback_devices")
