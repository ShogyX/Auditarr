"""stage 17 v1.8.0 — playback_sessions for SSE-recorded session lifecycle

Revision ID: 0027_stage17_playback_sessions
Revises: 0026_stage12_must_change_pw
Create Date: 2026-05-18 10:00:00

Per the v1.8.0 SSE rework:

  * New ``playback_sessions`` table. One row per live session per
    integration (uniqueness via ``integration_id + session_key``).
    Mutable — the SSE listener updates the same row as the session
    transitions through playing → paused → stopped.
  * Distinct from ``playback_events`` (which is immutable
    history-scrape data) so the analyzer can aggregate the two
    sources without confusion.

Purely additive. No data backfill needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic identifiers.
revision = "0027_stage17_playback_sessions"
down_revision = "0026_stage12_must_change_pw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playback_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "integration_id",
            sa.String(36),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_file_id",
            sa.String(36),
            sa.ForeignKey("media_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("source_path", sa.String(2048), nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("grandparent_title", sa.String(512), nullable=True),
        sa.Column("user", sa.String(256), nullable=True),
        sa.Column("device_kind", sa.String(64), nullable=True),
        sa.Column("device_name", sa.String(256), nullable=True),
        sa.Column("source_codec", sa.String(32), nullable=True),
        sa.Column("source_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=True),
        sa.Column("source_height", sa.Integer(), nullable=True),
        sa.Column("source_container", sa.String(32), nullable=True),
        sa.Column("target_codec", sa.String(32), nullable=True),
        sa.Column("target_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("view_offset_ms", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_event_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "stopped_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "reconciled_with_history",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "integration_id",
            "session_key",
            name="uq_playback_sessions_integration_session",
        ),
    )
    op.create_index(
        "ix_playback_sessions_int_state",
        "playback_sessions",
        ["integration_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_playback_sessions_int_state", "playback_sessions")
    op.drop_table("playback_sessions")
