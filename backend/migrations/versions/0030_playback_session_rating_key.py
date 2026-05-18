"""v1.9 OP-10 — add rating_key to playback_sessions + reconciliation index +
reconciled_with_session_id to playback_events.

Adds:
  * ``playback_sessions.rating_key VARCHAR(128) NULL`` — the upstream's
    stable media id (Plex's ``ratingKey``; NULL for providers that don't
    expose one, e.g. Jellyfin).
  * Composite index ``(integration_id, rating_key, started_at)`` on
    ``playback_sessions`` — drives the history poller's reconciliation
    query when matching a history DTO against an existing SSE-tracked
    session.
  * ``playback_events.reconciled_with_session_id VARCHAR(36) NULL`` —
    foreign key (no FK constraint; logical link) to the
    ``playback_sessions.id`` that this event was reconciled against. NULL
    for events that didn't match a session.

Why the composite index shape: the reconciliation query is
``WHERE integration_id = ? AND rating_key = ? AND started_at BETWEEN ?
AND ? AND rating_key IS NOT NULL``. The composite covers all three
equality columns; the trailing started_at range scan is the standard
B-tree pattern.

Why reconciled_with_session_id (and not skip-insert): caveat 4 of the
audit. The original plan called for skipping the PlaybackEvent insert
entirely when a matching session was found, but that destroys
diagnosability — if reconciliation matches wrongly, the operator has no
event row to inspect. We instead INSERT the event AND record which
session it matched, so dedup at the analyzer layer is explicit and the
operator can always reconstruct what happened.

NULL semantics on ``rating_key``: a row written by the Jellyfin SSE
writer (when that ships) or by the legacy path will have
``rating_key = NULL``. The reconciliation query has an explicit
``rating_key IS NOT NULL`` filter so NULLs never match — preventing
accidental cross-provider joins.

Revision ID: 0030_playback_session_rating_key
Revises: 0029_stage9_1_playback_devices
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_playback_session_rating_key"
down_revision = "0029_stage9_1_playback_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playback_sessions",
        sa.Column("rating_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_playback_sessions_recon",
        "playback_sessions",
        ["integration_id", "rating_key", "started_at"],
        unique=False,
    )
    op.add_column(
        "playback_events",
        sa.Column(
            "reconciled_with_session_id",
            sa.String(length=36),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("playback_events", "reconciled_with_session_id")
    op.drop_index(
        "ix_playback_sessions_recon", table_name="playback_sessions"
    )
    op.drop_column("playback_sessions", "rating_key")
