"""Integration provider protocol and DTOs.

The integration manager talks to plugins exclusively through the
:class:`IntegrationProvider` protocol. Each connector plugin (Plex,
Sonarr, …) registers an instance via ``ctx.register_integration(...)``.

The DTOs here are intentionally minimal and stable — they're frozen as
public SDK after Stage 5.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class HealthReport:
    """Result of a healthcheck against an upstream service."""

    status: str  # "ok" | "degraded" | "error"
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveredLibrary:
    """A library/section discovered on an upstream service.

    Operators can promote these to managed :class:`Library` rows. Auto-import
    is opt-in per integration to avoid surprising operators with mass-imports
    on first connect.
    """

    upstream_id: str
    name: str
    kind: str  # movies | tv | music | mixed
    root_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TagSync:
    """One tag entry mirrored from an upstream service.

    The integration manager reconciles these against ``MediaTag`` rows
    keyed by ``(media_file_id, name, source)``.
    """

    media_path: str  # absolute path that this tag applies to
    tag: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Stage 16: playback telemetry ────────────────────────────────
@dataclass(slots=True)
class IntegrationConfig:
    """Validated configuration handed to a provider.

    ``options`` holds the public config (URL, timeouts, etc.); ``secrets``
    holds the decrypted credential dict. Providers should treat both as
    read-only.
    """

    integration_id: str
    name: str
    kind: str
    options: dict[str, Any]
    secrets: dict[str, Any]


@dataclass(slots=True)
class PlaybackEventDTO:
    """One playback observation from an upstream service.

    Providers map their native event shapes (Plex's
    ``/status/sessions/history``, Jellyfin's ``/Sessions`` snapshots)
    onto this normalized DTO. The poller persists it as a
    :class:`app.models.playback.PlaybackEvent` row.

    ``source_path`` is the path as the integration sees it — the
    poller applies any configured path mappings before saving so the
    stored row already matches Auditarr's filesystem view.
    """

    # Integration's own ID for this event. Used by the poller to dedupe
    # across overlapping polls. Must be stable for the same logical
    # session — Plex's history entry's ratingKey+viewedAt works, as
    # does Jellyfin's session GUID.
    upstream_id: str

    source_path: str  # path as the integration reports it (pre-remap)

    # Classification. Use "transcode" when *either* the video or audio
    # was transcoded; "direct_stream" when remuxed without re-encode;
    # "direct_play" when played verbatim; "failed" when playback could
    # not start.
    decision: str  # "direct_play" | "direct_stream" | "transcode" | "failed"

    started_at: _dt.datetime

    device_kind: str | None = None  # e.g. "Roku", "AppleTV", "Browser"
    device_name: str | None = None  # the operator's per-device label
    reason_code: str | None = None  # machine code, e.g. "video.codec.unsupported"

    source_codec: str | None = None
    source_bitrate_kbps: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_container: str | None = None
    target_codec: str | None = None
    target_bitrate_kbps: int | None = None

    completed_at: _dt.datetime | None = None
    duration_s: int | None = None


@runtime_checkable
class IntegrationProvider(Protocol):
    """Implemented by connector plugins.

    All methods are coroutines so the manager can fan out concurrently and
    so HTTP I/O is non-blocking.
    """

    #: Plugin id — must match the manifest id and the integration ``kind``.
    kind: str

    #: Human label shown in the UI integration directory.
    label: str

    #: JSON-Schema-ish dict describing the public config fields. The frontend
    #: renders this as a form. Keep it simple — no nested schemas.
    config_schema: dict[str, Any]

    #: Names of the secret fields. Filled in by the operator on connect; the
    #: manager encrypts them at rest.
    secret_fields: tuple[str, ...]

    async def healthcheck(self, config: IntegrationConfig) -> HealthReport: ...

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        """Return libraries/sections this service exposes.

        Optional — providers that don't expose libraries can return ``[]``.
        """
        ...

    async def sync_tags(self, config: IntegrationConfig) -> list[TagSync]:
        """Return tag mirrors. Optional — return ``[]`` if not supported."""
        ...

    async def fetch_playback_events(
        self, config: IntegrationConfig, since: _dt.datetime | None
    ) -> list[PlaybackEventDTO]:
        """Return playback events newer than ``since`` (or all available
        if ``since`` is None).

        Optional — providers that don't expose playback telemetry
        (Sonarr, Radarr, Bazarr) return ``[]``. The manager treats this
        as a no-op rather than an error so the contract stays additive
        for existing connector plugins.
        """
        return []
