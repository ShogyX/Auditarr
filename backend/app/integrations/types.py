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

    # v1.9 OP-10 — provider's stable media id (Plex's
    # ``ratingKey``). Used by the poller to reconcile a history
    # DTO against an existing SSE-tracked PlaybackSession row.
    # Defaults to None for backward compatibility — Jellyfin /
    # Tracearr providers and any test fixtures built before this
    # field landed continue to construct the DTO without
    # specifying it.
    rating_key: str | None = None


# ── Stage 09 (v1.7): live playback DTO ──────────────────────────


@dataclass(slots=True)
class LivePlaybackDTO:
    """Stage 09 (v1.7) — one currently-in-progress playback session.

    Distinct from :class:`PlaybackEventDTO` (the historical
    record). A live session is ephemeral: the dashboard's "Live
    now" tile reads these via the new ``fetch_live_playbacks``
    Protocol method on a 15-second poll plus a
    ``playback.live_changed`` WebSocket push, and the rows are
    NOT persisted to the playback_events table — they're a
    realtime view, not history.

    ``source_path`` is the path as the integration reports it,
    pre-remap. The aggregating ``/playback/live`` endpoint
    applies the integration's path mappings before returning
    so the frontend sees Auditarr-side paths and can link to
    library files when matched.
    """

    #: Integration's own ID for the live session. Stable for the
    #: duration of the session (Plex's session key, Jellyfin's
    #: session id). When the same session is observed by two
    #: polls back-to-back, the IDs match so the frontend can
    #: animate continuously rather than blinking.
    upstream_id: str

    #: Path as the integration reports it (pre-remap).
    source_path: str

    #: The Auditarr-side normalized decision string. Same values
    #: as :attr:`PlaybackEventDTO.decision`.
    decision: str  # "direct_play" | "direct_stream" | "transcode"

    #: When the session began playing upstream. Surface lets the
    #: tile show "Started 4m ago" without computing per-poll.
    started_at: _dt.datetime

    #: ``"playing"`` | ``"paused"`` | ``"buffering"``. Lets the
    #: tile show a small pause-glyph next to the title without
    #: needing to interpret reason codes.
    state: str = "playing"

    #: Progress through the session in percent (0..100). When
    #: the provider doesn't report it (some Plex client kinds
    #: don't), surface ``None`` so the frontend can fall back to
    #: "elapsed time" rendering.
    progress_pct: float | None = None

    #: Username + device when available.
    user: str | None = None
    device_kind: str | None = None
    device_name: str | None = None

    #: Source-stream details — same shape as PlaybackEventDTO.
    source_codec: str | None = None
    source_bitrate_kbps: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_container: str | None = None
    target_codec: str | None = None
    target_bitrate_kbps: int | None = None

    #: Optional human-readable title from the upstream (Plex
    #: "MovieTitle", Jellyfin "NowPlayingItem.Name"). Spares the
    #: frontend a MediaFile lookup for the common case where the
    #: file is known but the operator just wants to see what's
    #: playing.
    title: str | None = None


# ── Stage 07 (v1.7): third-party transcode hand-off seam ──────


@dataclass(slots=True)
class TranscodeJobSpec:
    """Stage 07 (v1.7) — abstract description of a transcode job
    handed off to an integration provider.

    Stage 07 lays the seam; Stage 08 will implement the actual
    provider sides (Tdarr, future Unmanic, etc.) and translate
    these abstract fields into provider-specific job submissions.

    The fields here are deliberately abstract — codec families
    and quality targets, NOT ffmpeg-specific argv. Each provider
    knows how to translate its own subset (Plex transcoder
    accepts a subset of options; Tdarr accepts another). The
    profile editor surface only exposes options that map cleanly
    across providers for the chosen ``routing_target`` (per
    plan §409 — ``OPTIONS_BY_TARGET`` map on the frontend).
    """

    #: Auditarr's optimization_item.id for forensic correlation.
    item_id: str
    #: The matched media file's absolute path on the Auditarr host.
    input_path: str
    #: ``video_and_audio`` | ``video_only`` | ``audio_only``.
    transcode_scope: str
    #: Video target codec family — ``"libx264"``, ``"libx265"``,
    #: ``"libaom-av1"``, or ``"copy"``. The provider maps this to
    #: its native option. ``copy`` means passthrough.
    video_codec: str
    #: Audio target codec family — same shape.
    audio_codec: str
    #: Target container (``"mkv"``, ``"mp4"``, ``"webm"``).
    container: str
    #: Optional quality target — interpreted as CRF for
    #: x264/x265 family codecs, ignored for ``copy``.
    crf: int | None = None
    #: Optional max bitrate cap, kbps.
    max_bitrate_kbps: int | None = None
    #: Optional scale target (short side in pixels).
    scale_height: int | None = None
    #: Free-form per-provider metadata the profile included.
    #: Providers MAY consult this for non-portable hints; safe
    #: to ignore.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobSubmitResult:
    """Stage 07 (v1.7) — outcome of a ``submit_transcode_job``
    call. The integration provider returns one of these so the
    worker (Stage 08) can either record success-pending-callback
    or fail the item immediately.
    """

    #: ``"accepted"`` — provider acknowledged + queued the job.
    #: ``"rejected"`` — provider refused (capacity, quota, format).
    #: ``"error"``    — transient/transport error; the worker
    #:                  re-enqueues.
    status: str
    #: Provider's own job id, when ``accepted``. Used to
    #: correlate the eventual completion event.
    upstream_job_id: str | None = None
    #: Human-readable detail; surfaced to the operator on
    #: ``rejected``/``error``.
    detail: str | None = None


# ── Stage 08 (v1.7): pre-existing profile listing + job polling ──


@dataclass(slots=True)
class TranscodeProfileSummary:
    """Stage 08 (v1.7) — summary of a provider-side transcode
    profile, returned by ``list_transcode_profiles``.

    Each provider has its own concept of a profile:
      * Tdarr — a "flow" + plugin id (e.g. "Tdarr_Plugin_henk_h265").
      * Plex  — a "target" (Original=1 / Mobile=2 / TV=3) plus
                custom smart-playlist names from /playlists.
      * Jellyfin — TBD; the plan acknowledges Jellyfin's API
                   doesn't currently support this.

    The Auditarr profile editor renders these and persists the
    chosen ``id`` into the optimization profile's ``settings``
    (key: ``provider_profile_id``). The worker passes that id
    through to ``submit_transcode_job`` via the spec's
    ``metadata`` dict.
    """

    #: Provider-native id, opaque to Auditarr.
    id: str
    #: Human label shown in the picker.
    name: str
    #: Optional description / kind hint (e.g. "Mobile", "TV").
    description: str | None = None
    #: Free-form provider metadata; safe to ignore.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscodeJobStatus:
    """Stage 08 (v1.7) — current state of a routed transcode job.

    Returned by ``get_transcode_job_status`` so the worker can
    advance a routed item to ``completed`` / ``failed`` based on
    provider state.

    The status vocabulary is deliberately Auditarr-shaped (not
    provider-shaped): each provider maps its native states into
    this enum.

      * ``"pending"``   — accepted, waiting in the provider's queue.
      * ``"running"``   — actively transcoding upstream.
      * ``"completed"`` — finished successfully.
      * ``"failed"``    — finished with an error.
      * ``"unknown"``   — provider returned a state we can't map.
                          Worker keeps polling.
    """

    status: str
    detail: str | None = None
    progress_pct: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── v1.9 Stage 5.1 — cross-integration search trigger ──────────


@dataclass(slots=True)
class SearchTriggerResult:
    """Outcome of a provider's ``trigger_search`` call.

    Returned by the worker job that fires when a rule's
    ``search_upstream`` action matches. The dataclass is what gets
    audit-logged + WS-emitted.

    ``status``:
      * ``"submitted"`` — upstream accepted the command. The
        ``upstream_id`` field carries whichever id the provider
        resolved (series_id, movie_id, etc.).
      * ``"not_found"`` — the provider couldn't find the file's
        path in its title list. Common cause: the integration's
        root paths don't overlap the file's path.
      * ``"error"`` — the upstream rejected the command, the API
        was unreachable, etc. ``detail`` carries the human-readable
        cause.
    """

    status: str
    upstream_id: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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

    # ── Stage 09 (v1.7): live (in-progress) playback ─────────────
    # Optional, per plan §483-484. The dashboard's "Live now" tile
    # reads aggregated live sessions across all enabled Plex/
    # Jellyfin integrations. The /playback/live aggregating
    # endpoint hasattr-checks this so providers that don't
    # implement it (Sonarr, Radarr, Bazarr, Tdarr) contribute
    # nothing — they just don't have the concept.
    async def fetch_live_playbacks(
        self, config: IntegrationConfig
    ) -> list["LivePlaybackDTO"]:
        """Return the integration's currently-in-progress playback
        sessions. Empty list if no sessions are active.

        Optional. Plex's ``/status/sessions`` and Jellyfin's
        ``/Sessions`` (filtered to entries with ``NowPlayingItem``)
        are the documented endpoints. Providers that don't expose
        a live-session surface return ``[]`` rather than raising,
        so the aggregating endpoint can keep producing a complete
        union across the operator's integrations.
        """
        return []

    # ── Stage 07 (v1.7): optional third-party transcode hand-off ──
    # Defined as optional per plan §403 — existing providers (Plex
    # poller, Sonarr/Radarr connectors) don't need to implement it.
    # The worker checks ``hasattr(provider, "submit_transcode_job")``
    # before calling, and falls back to "routing target unsupported
    # by integration" when missing. Stage 08 will wire the actual
    # provider implementations (Tdarr first, then Plex/Jellyfin).
    async def submit_transcode_job(
        self,
        config: "IntegrationConfig",
        job_spec: "TranscodeJobSpec",
    ) -> "JobSubmitResult":
        """Submit a transcode job to the integration.

        Optional. Providers that own remote transcode execution
        (Tdarr, future Unmanic, future Plex media-server transcode
        queue) implement this to accept jobs the optimization
        worker has routed away from the in-process runner.

        Returning ``status="accepted"`` with an ``upstream_job_id``
        commits the worker to a routed state — the eventual
        ``optimization.routed_completed`` / ``routed_failed``
        events (Stage 08) flip the item to ``completed`` / ``failed``.
        """
        ...

    # ── Stage 08 (v1.7): list pre-existing transcode profiles ────
    # Optional, per plan §438-441. Lets the operator pick a
    # provider-side profile name (Tdarr plugin, Plex transcode
    # target) in the Auditarr profile editor rather than
    # synthesising configuration from scratch.
    async def list_transcode_profiles(
        self,
        config: "IntegrationConfig",
    ) -> list["TranscodeProfileSummary"]:
        """Return the provider's available transcode profiles.

        Optional. Used by the optimization profile editor (Stage 08
        frontend) to populate a picker so operators reference an
        existing provider-side configuration by name rather than
        reinventing settings.
        """
        return []

    # ── Stage 08 (v1.7): poll a previously-submitted job ─────────
    # Optional, per plan §444. The worker's poll_routed_transcodes
    # automation job calls this every 5 minutes to flip routed
    # items to completed/failed based on provider state.
    async def get_transcode_job_status(
        self,
        config: "IntegrationConfig",
        upstream_job_id: str,
    ) -> "TranscodeJobStatus":
        """Return the current state of a previously-submitted job.

        Optional. The poller fans this out for every item still
        in ``routed`` status. Providers that own remote transcode
        execution must implement this; otherwise the worker
        treats routed items as terminal and never polls them.
        """
        ...

    # ── v1.9 Stage 5.1: cross-integration search trigger ─────────
    # Optional, per plan §301-308. Sonarr/Radarr/Bazarr implement
    # this; everything else returns None or raises NotImplemented.
    # The worker hatattr-checks before calling, mirroring the
    # Stage 07 transcode pattern.
    async def trigger_search(
        self,
        config: "IntegrationConfig",
        media_file_path: str,
    ) -> "SearchTriggerResult":
        """Submit a search command to the upstream service for the
        file at ``media_file_path``.

        Implementations resolve ``media_file_path`` to an upstream
        id (Sonarr series id / Radarr movie id / Bazarr series id)
        via the integration's own API (e.g. listing series and
        filtering by path-prefix). They then issue the appropriate
        command:

          * Sonarr: POST /api/v3/command {"name": "SeriesSearch", "seriesId": <id>}
          * Radarr: POST /api/v3/command {"name": "MoviesSearch", "movieIds": [<id>]}
          * Bazarr: POST /api/episodes/subtitles for the series

        Returns a ``SearchTriggerResult`` carrying the outcome
        (``status="submitted" | "not_found" | "error"``) plus
        diagnostics for the audit log entry. Implementations
        SHOULD NOT raise on upstream errors — capture them in
        ``status="error"`` so the audit log records the failure
        rather than the worker job retrying indefinitely.
        """
        ...
