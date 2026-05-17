"""Playback telemetry models (Stage 16; SSE rework Stage 17 / v1.8.0).

A :class:`PlaybackEvent` is one observation from an upstream integration
(Plex, Jellyfin, ...): one play session, classified as direct-play /
direct-stream / transcode / failed. The analyzer aggregates these into
rule suggestions.

A :class:`PlaybackSession` is a live-session lifecycle row written by
the v1.8.0 SSE listener: start, pause/resume, stop timestamps. Stage 16
captured these only via the history scrape, which inherits Plex's
"watched threshold" filter (default ~90%) and misses every aborted,
scrubbed, or short session. v1.8.0 records lifecycle events from the
SSE stream so we never miss a session.

:class:`IntegrationPollingCursor` lets the poller resume from the last
seen event timestamp rather than re-fetching history every cycle.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class PlaybackEvent(Base):
    __tablename__ = "playback_events"
    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "upstream_id",
            name="uq_playback_events_integration_upstream",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="CASCADE")
    )
    media_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
    )
    source_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    device_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_bitrate_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_bitrate_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )


class IntegrationPollingCursor(Base):
    """Watermark recording where the poller last left off.

    One row per ``(integration_id, cursor_kind)``. ``cursor_kind`` is
    "playback_events" for the Stage 16 telemetry pipeline; we leave
    the column generic so future polled streams (e.g. "activity_log")
    can reuse the table.
    """

    __tablename__ = "integration_polling_cursors"
    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "cursor_kind",
            name="uq_int_poll_cursor_int_kind",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="CASCADE")
    )
    cursor_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor_value: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
        onupdate=lambda: _dt.datetime.now(_dt.UTC),
    )


class PlaybackSession(Base):
    """One live session lifecycle row (v1.8.0 / Stage 17).

    Unlike :class:`PlaybackEvent` (which only records sessions Plex
    considered "watched"), this table captures every session the SSE
    listener observes — including aborted, scrubbed, or sub-threshold
    plays. One row per ``(integration_id, session_key)``; updated in
    place as the session transitions through playing → paused →
    resumed → stopped.

    Why a separate table from PlaybackEvent? Two reasons:
      1. Different lifecycle semantics. PlaybackEvent is immutable
         (one row per completed history entry, deduped by upstream_id).
         PlaybackSession is mutable (the SSE listener updates the same
         row as state changes). Mixing the two on one table would
         confuse the analyzer's aggregation queries.
      2. Different data freshness guarantees. PlaybackEvent rows
         arrive in batches every 15 min from the history scrape;
         PlaybackSession rows arrive within ~100ms of the wire event.

    The analyzer prefers PlaybackSession when available and falls back
    to PlaybackEvent for sessions that ended before SSE was connected.
    """

    __tablename__ = "playback_sessions"
    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "session_key",
            name="uq_playback_sessions_integration_session",
        ),
        # The hot read pattern is "list active sessions for this
        # integration"; index covers that without a sequential scan.
        Index(
            "ix_playback_sessions_int_state",
            "integration_id",
            "state",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="CASCADE")
    )
    media_file_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("media_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Plex's ``sessionKey``; Jellyfin's session id. Unique within
    # an integration for the session's lifetime.
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # Lifecycle state. One of:
    #   "playing"   — actively streaming.
    #   "paused"    — user hit pause; we haven't observed a stop yet.
    #   "buffering" — Plex's intermediate state during seek/transcode warmup.
    #   "stopped"   — final state; the row stays for history.
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    # Decision when the session started: direct_play / direct_stream /
    # transcode. Captured at start and refreshed on transcode-decision
    # change (Plex sends a new event when it switches mid-stream).
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Path-mapped to Auditarr's filesystem view. May be NULL if the
    # SSE event arrived before we ran path mapping (recovered on the
    # next state update).
    source_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # User-visible metadata. ``title`` is the episode/movie title for
    # display; ``grandparent_title`` is the show name for episodes.
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grandparent_title: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    user: Mapped[str | None] = mapped_column(String(256), nullable=True)
    device_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Source stream details — capture once, don't update per
    # progress event.
    source_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_bitrate_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_bitrate_kbps: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Progress fields — updated on every progress event.
    view_offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps for lifecycle accounting.
    started_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )
    last_event_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
        onupdate=lambda: _dt.datetime.now(_dt.UTC),
    )
    stopped_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Stage 17 reconciliation hook: True once the history scrape has
    # observed this session in /status/sessions/history/all. Lets us
    # avoid double-counting in the analyzer.
    reconciled_with_history: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )
