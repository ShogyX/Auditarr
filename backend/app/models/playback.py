"""Playback telemetry models (Stage 16).

A :class:`PlaybackEvent` is one observation from an upstream integration
(Plex, Jellyfin, ...): one play session, classified as direct-play /
direct-stream / transcode / failed. The analyzer aggregates these into
rule suggestions.

:class:`IntegrationPollingCursor` lets the poller resume from the last
seen event timestamp rather than re-fetching history every cycle.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
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
