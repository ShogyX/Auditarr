"""Playback device index (v1.9 Stage 9.1).

The playback poller writes one :class:`PlaybackEvent` per
observed session, but those rows are point-in-time. A
``PlaybackDevice`` aggregates lifecycle data per device so the
dashboard can answer questions like "which clients keep
transcoding HEVC" without scanning the full event table.

Each device row is uniquely identified by
``(integration_id, client_key)`` where ``client_key`` is a
stable identifier derived from the upstream provider's session
metadata — typically a hash of ``device_name`` and
``device_kind`` when no explicit client GUID is available.
Plex / Jellyfin both expose a stable per-client identifier on
their session API, so the device row survives across reboots
and library re-scans.

The poller upserts on every event ingested (history + live).
Stats columns (play / transcode / direct-stream / direct-play
counts) increment monotonically; ``last_seen_at`` and
``first_seen_at`` track the device's overall window of
visibility.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class PlaybackDevice(Base):
    """One row per (integration, client) playback device.

    The device is upserted by ``PlaybackPoller`` each time it
    ingests an event whose ``device_kind`` or ``device_name``
    is non-empty. Stats columns are incremented in-place.
    """

    __tablename__ = "playback_devices"
    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "client_key",
            name="uq_playback_devices_integration_client",
        ),
        Index(
            "ix_playback_devices_last_seen",
            "last_seen_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    integration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Stable identifier within an integration's session namespace.
    # Plex / Jellyfin provide explicit client GUIDs on the session
    # API; when only ``device_kind`` and ``device_name`` are
    # available, the poller synthesizes a deterministic hash so
    # the row is stable across polls.
    client_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # Display name. The user-given device label, e.g. "Living Room
    # Apple TV", "Bedroom TV". May be NULL when the upstream
    # doesn't expose it.
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Platform / product / model fields. Plex provides all three;
    # Jellyfin provides a subset; Tracearr currently maps platform
    # only. NULLable so providers without one don't blow up the
    # upsert.
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Time window of visibility.
    first_seen_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )
    last_seen_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: _dt.datetime.now(_dt.UTC),
    )

    # Counters. All start at zero; the poller increments on each
    # event by decision. Total ``playback_count`` equals the sum
    # of the three decision-specific counters — kept as a denorm
    # so the dashboard can sort by total without summing.
    playback_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    transcode_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    direct_play_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    direct_stream_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )


__all__ = ["PlaybackDevice"]
