"""playback events + polling cursors (Stage 16)

Revision ID: 0010_playback_telemetry
Revises: 0009_plugin_settings
Create Date: 2026-05-11 11:00:00

Adds two tables for the rule-recommendation engine:

* ``playback_events`` — one row per session/playback observed from
  Plex or Jellyfin (and any future provider that implements
  ``fetch_playback_events``). The analyzer aggregates over this table
  to surface rule suggestions.

* ``integration_polling_cursors`` — per-(integration, cursor-kind)
  watermarks so the poller can resume from where it left off without
  re-fetching history every cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_playback_telemetry"
down_revision: str | None = "0009_plugin_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── playback_events ────────────────────────────────────────
    op.create_table(
        "playback_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        # Best-effort link to the MediaFile this event refers to. Null
        # when the source path didn't resolve (drift). The analyzer
        # still uses unresolved events to compute drift health but
        # excludes them from rule-suggestion heuristics.
        sa.Column("media_file_id", sa.String(length=36), nullable=True),
        # The path as reported by the integration, *after* remapping.
        # Stored so debugging is possible even when media_file_id is
        # null. We keep the post-remap value to keep the rest of the
        # system consistent with MediaFile.path.
        sa.Column("source_path", sa.String(length=2048), nullable=False),
        sa.Column("device_kind", sa.String(length=64), nullable=True),
        sa.Column("device_name", sa.String(length=256), nullable=True),
        # "direct_play" | "direct_stream" | "transcode" | "failed"
        sa.Column("decision", sa.String(length=32), nullable=False),
        # Short machine code explaining a transcode/failure, e.g.
        # "video.codec.unsupported", "video.bitrate.exceeded".
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("source_codec", sa.String(length=32), nullable=True),
        sa.Column("source_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=True),
        sa.Column("source_height", sa.Integer(), nullable=True),
        sa.Column("source_container", sa.String(length=32), nullable=True),
        sa.Column("target_codec", sa.String(length=32), nullable=True),
        sa.Column("target_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        # The integration's own ID for the event, used to dedupe across
        # polls. (id, integration_id) is unique.
        sa.Column("upstream_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_playback_events"),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integrations.id"],
            name="fk_playback_events_integration_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_files.id"],
            name="fk_playback_events_media_file_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "integration_id",
            "upstream_id",
            name="uq_playback_events_integration_upstream",
        ),
    )
    op.create_index(
        "ix_playback_events_integration_id",
        "playback_events",
        ["integration_id"],
    )
    op.create_index(
        "ix_playback_events_media_file_id",
        "playback_events",
        ["media_file_id"],
    )
    op.create_index(
        "ix_playback_events_started_at",
        "playback_events",
        ["started_at"],
    )
    op.create_index(
        "ix_playback_events_decision",
        "playback_events",
        ["decision"],
    )

    # ── integration_polling_cursors ────────────────────────────
    op.create_table(
        "integration_polling_cursors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        # e.g. "playback_events" — lets one integration carry several
        # cursors if it has more than one telemetry stream.
        sa.Column("cursor_kind", sa.String(length=64), nullable=False),
        # ISO timestamp watermark. The provider returns events with
        # started_at > this value.
        sa.Column("cursor_value", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_integration_polling_cursors"),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integrations.id"],
            name="fk_int_poll_cursor_integration_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "integration_id",
            "cursor_kind",
            name="uq_int_poll_cursor_int_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("integration_polling_cursors")
    op.drop_index("ix_playback_events_decision", table_name="playback_events")
    op.drop_index("ix_playback_events_started_at", table_name="playback_events")
    op.drop_index("ix_playback_events_media_file_id", table_name="playback_events")
    op.drop_index(
        "ix_playback_events_integration_id", table_name="playback_events"
    )
    op.drop_table("playback_events")
