"""Jellyfin integration plugin.

Targets Jellyfin 10.8+. The official API is OpenAPI-documented and stable.

What ships in this version:
* ``healthcheck`` — ``GET /System/Info/Public`` (no auth) for liveness, then
  ``GET /System/Info`` with the API key to verify the key works and report
  the full server version.
* ``discover_libraries`` — ``GET /Library/VirtualFolders`` enumerates the
  configured libraries with their on-disk roots (``Locations[]``). The
  ``CollectionType`` field maps to Auditarr kinds.

Authentication uses the ``X-Emby-Token`` header (Jellyfin retained the Emby
name) and the unified ``Authorization: MediaBrowser`` scheme for richer
clients. We use the simpler header-only form here; it's accepted by all
post-10.5 builds.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx

from app.integrations.path_mapping import PATH_MAPPINGS_SCHEMA_FRAGMENT
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    PlaybackEventDTO,
    TagSync,
)
from app.plugins import Plugin, PluginContext

JELLYFIN_KIND_TO_AUDITARR = {
    "movies": "movies",
    "tvshows": "tv",
    "music": "music",
    "musicvideos": "music",
    "homevideos": "mixed",
    "boxsets": "movies",
    "mixed": "mixed",
}


class JellyfinProvider(IntegrationProvider):
    kind = "jellyfin"
    label = "Jellyfin"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://jellyfin.local:8096",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify TLS", "default": True},
            "timeout_seconds": {
                "type": "integer",
                "title": "Timeout (s)",
                "default": 15,
                "minimum": 1,
                "maximum": 120,
            },
            "path_mappings": PATH_MAPPINGS_SCHEMA_FRAGMENT,
        },
    }
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log: Any) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Jellyfin integration is missing 'base_url'")
        api_key = str(config.secrets.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("Jellyfin integration is missing 'api_key'")
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 15)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers={
                "X-Emby-Token": api_key,
                "Accept": "application/json",
            },
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/System/Info")
                if response.status_code == 401:
                    return HealthReport(
                        status="error", detail="API key rejected (401)"
                    )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(status="error", detail=f"HTTP error: {exc}")
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        return HealthReport(
            status="ok",
            detail=payload.get("ServerName") or "Jellyfin",
            metadata={
                "version": payload.get("Version"),
                "operating_system": payload.get("OperatingSystem"),
                "id": payload.get("Id"),
            },
        )

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        async with self._client(config) as client:
            response = await client.get("/Library/VirtualFolders")
            response.raise_for_status()
            folders = response.json() or []

        out: list[DiscoveredLibrary] = []
        for folder in folders:
            collection_type = (folder.get("CollectionType") or "").lower()
            kind = JELLYFIN_KIND_TO_AUDITARR.get(collection_type, "mixed")
            locations = folder.get("Locations") or []
            # Jellyfin libraries can reference multiple physical roots; we
            # emit one DiscoveredLibrary per location so operators can pick
            # exactly which paths Auditarr should scan.
            for index, location in enumerate(locations):
                suffix = f" ({index + 1})" if len(locations) > 1 else ""
                out.append(
                    DiscoveredLibrary(
                        upstream_id=str(folder.get("ItemId") or folder.get("Name") or ""),
                        name=f"{folder.get('Name') or 'Jellyfin'}{suffix}",
                        kind=kind,
                        root_path=str(location) or None,
                        metadata={
                            "collection_type": folder.get("CollectionType"),
                            "library_options": folder.get("LibraryOptions"),
                        },
                    )
                )
            if not locations:
                out.append(
                    DiscoveredLibrary(
                        upstream_id=str(folder.get("ItemId") or folder.get("Name") or ""),
                        name=str(folder.get("Name") or "Jellyfin"),
                        kind=kind,
                        root_path=None,
                        metadata={"collection_type": folder.get("CollectionType")},
                    )
                )
        return out

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        # Jellyfin "tags" are per-item metadata. Mirroring them would require
        # iterating /Items?fields=Tags,Path which is a multi-thousand-row
        # call on real libraries. Defer to a later optimization stage.
        return []

    async def fetch_playback_events(
        self, config: IntegrationConfig, since: _dt.datetime | None
    ) -> list[PlaybackEventDTO]:
        """Stage 16: snapshot active Jellyfin sessions.

        Jellyfin's playback history is weaker than Plex's: there's no
        dedicated history endpoint that returns completed plays with
        decision metadata. The closest is ``/Sessions`` which lists
        currently-playing sessions with their ``TranscodingInfo`` and
        ``NowPlayingItem`` payloads.

        Strategy:
          - Poll ``/Sessions?activeWithinSeconds=900`` every 15 min
          - For each session with ``NowPlayingItem``, capture one event
          - Use ``Session.Id + NowPlayingItem.Id`` as the upstream_id
            so the same session isn't recorded twice across polls
          - Operators get partial coverage but at least the live picture

        Real historical telemetry on Jellyfin requires the Playback
        Reporting plugin to be installed server-side; that's a future
        enhancement we can layer on as an alternative path.
        """
        cutoff = since or (_dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=20))
        async with self._client(config) as client:
            try:
                response = await client.get(
                    "/Sessions",
                    params={"activeWithinSeconds": 900},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._log.warning(
                    "jellyfin.playback.fetch_failed", error=str(exc)
                )
                return []
            sessions = response.json() or []

        events: list[PlaybackEventDTO] = []
        for session in sessions:
            event = _jellyfin_session_to_event(session, cutoff)
            if event is not None:
                events.append(event)
        return events


def _jellyfin_session_to_event(
    session: dict, cutoff: _dt.datetime
) -> PlaybackEventDTO | None:
    """Translate one Jellyfin ``/Sessions`` entry → DTO.

    Returns None for entries we can't safely parse. Jellyfin's
    response shape varies across versions and plugin configurations,
    so every nested field access is null-guarded. A single bad
    session must not crash the whole batch.
    """
    try:
        item = session.get("NowPlayingItem")
        if not isinstance(item, dict):
            return None
        source_path = item.get("Path")
        if not isinstance(source_path, str) or not source_path:
            return None
        session_id = session.get("Id")
        item_id = item.get("Id")
        if not session_id or not item_id:
            return None
        upstream_id = f"jellyfin:{session_id}:{item_id}"

        # PlayMethod: "DirectPlay" | "DirectStream" | "Transcode".
        # Some sessions omit PlayState entirely; some have
        # PlayState=null. Be defensive.
        play_state = session.get("PlayState")
        if not isinstance(play_state, dict):
            play_state = {}
        play_method = (play_state.get("PlayMethod") or "").lower()
        if play_method == "transcode":
            decision = "transcode"
        elif play_method == "directstream":
            decision = "direct_stream"
        elif play_method == "directplay":
            decision = "direct_play"
        else:
            decision = "direct_play"  # conservative default

        # Reason: Jellyfin exposes TranscodingInfo.TranscodeReasons[]
        # when decision == "transcode". Take the first as the code.
        reason_code: str | None = None
        transcoding = session.get("TranscodingInfo")
        if not isinstance(transcoding, dict):
            transcoding = None
        if transcoding:
            reasons = transcoding.get("TranscodeReasons") or []
            if isinstance(reasons, list) and reasons:
                reason_code = _jellyfin_reason_to_code(str(reasons[0]))

        last_check_in = session.get("LastPlaybackCheckIn")
        started_at = _parse_jellyfin_ts(last_check_in) or cutoff

        # MediaStreams may contain non-dict entries on broken servers;
        # filter to dicts before iterating.
        streams_raw = item.get("MediaStreams") or []
        streams = [s for s in streams_raw if isinstance(s, dict)]
        video_stream = next(
            (s for s in streams if s.get("Type") == "Video"), None
        )
        source_codec = (
            video_stream.get("Codec")
            if video_stream and isinstance(video_stream.get("Codec"), str)
            else None
        )
        source_bitrate = (
            _safe_int(video_stream.get("BitRate")) if video_stream else None
        )
        if source_bitrate is not None:
            source_bitrate //= 1000  # bps → kbps
        source_width = (
            _safe_int(video_stream.get("Width")) if video_stream else None
        )
        source_height = (
            _safe_int(video_stream.get("Height")) if video_stream else None
        )

        target_codec = None
        target_bitrate_kbps = None
        if transcoding:
            tc = transcoding.get("VideoCodec")
            target_codec = tc if isinstance(tc, str) else None
            tb = _safe_int(transcoding.get("Bitrate"))
            if tb is not None:
                target_bitrate_kbps = tb // 1000

        runtime_ticks = _safe_int(item.get("RunTimeTicks"))
        duration_s = (
            runtime_ticks // 10_000_000 if runtime_ticks is not None else None
        )

        client = session.get("Client")
        device_name = session.get("DeviceName")

        return PlaybackEventDTO(
            upstream_id=upstream_id,
            source_path=source_path,
            decision=decision,
            started_at=started_at,
            device_kind=client if isinstance(client, str) else None,
            device_name=device_name if isinstance(device_name, str) else None,
            reason_code=reason_code,
            source_codec=source_codec,
            source_bitrate_kbps=source_bitrate,
            source_width=source_width,
            source_height=source_height,
            source_container=(
                item.get("Container")
                if isinstance(item.get("Container"), str)
                else None
            ),
            target_codec=target_codec,
            target_bitrate_kbps=target_bitrate_kbps,
            completed_at=None,
            duration_s=duration_s,
        )
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def _jellyfin_reason_to_code(raw: str) -> str:
    """``VideoCodecNotSupported`` → ``video.codec.unsupported``."""
    # CamelCase → snake_case-ish, then group.
    s = raw
    out: list[str] = []
    for i, ch in enumerate(s):
        if i > 0 and ch.isupper() and not s[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    snake = "".join(out)
    # Group into "video." / "audio." / "container." namespaces.
    if snake.startswith("video_"):
        return "video." + snake[6:].replace("_not_supported", ".unsupported")
    if snake.startswith("audio_"):
        return "audio." + snake[6:].replace("_not_supported", ".unsupported")
    if snake.startswith("container_"):
        return "video.container.unsupported"
    return snake


def _parse_jellyfin_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # Jellyfin returns ``2024-05-11T10:23:45.1234567Z``
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        # Trim sub-second precision past microsecond.
        if "." in value:
            head, tail = value.split(".", 1)
            tz = ""
            if "+" in tail:
                tail, tz = tail.split("+", 1)
                tz = "+" + tz
            elif "-" in tail:
                tail, tz = tail.split("-", 1)
                tz = "-" + tz
            tail = tail[:6]
            value = f"{head}.{tail}{tz}"
        return _dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def register(context: PluginContext) -> Plugin:
    context.register_integration(JellyfinProvider(log=context.logger()))
    return Plugin(context)
