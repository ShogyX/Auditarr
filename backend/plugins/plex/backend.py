"""Plex integration plugin.

Talks to Plex through the official HTTP API only. Authentication is via
``X-Plex-Token``; the operator pastes one in on connect.

What ships in this version:
* ``healthcheck`` — pings ``/identity`` and reports server name + version.
* ``discover_libraries`` — enumerates ``/library/sections`` and reports
  movies / shows / artists sections as :class:`DiscoveredLibrary`. The
  ``Location`` element gives the on-disk root path which is what Auditarr
  needs to scan locally.
* ``sync_tags`` — Plex's tag system is per-item label metadata. We don't
  ship tag mirroring in 0.1.0; the method returns ``[]``. A later release
  can add it without breaking the SDK contract.
* ``fetch_playback_events`` (Stage 16) — pulls
  ``/status/sessions/history/all`` since the last cursor and classifies
  each entry as direct_play / direct_stream / transcode based on the
  ``Media``/``Part`` stream nodes.

Optimization endpoints are intentionally out of scope here — those are
reverse-engineered and live in the optimization plugin (Stage 10).
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

PLEX_KIND_TO_AUDITARR = {
    "movie": "movies",
    "show": "tv",
    "artist": "music",
}


class PlexProvider(IntegrationProvider):
    kind = "plex"
    label = "Plex Media Server"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://plex.local:32400",
            },
            "verify_ssl": {
                "type": "boolean",
                "title": "Verify TLS",
                "default": True,
            },
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
    secret_fields: tuple[str, ...] = ("token",)

    def __init__(self, log: Any) -> None:
        self._log = log

    # ── HTTP helpers ─────────────────────────────────────────────
    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Plex integration is missing 'base_url'")
        token = str(config.secrets.get("token", "")).strip()
        if not token:
            raise ValueError("Plex integration is missing 'token'")
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 15)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers={
                "X-Plex-Token": token,
                "Accept": "application/json",
                "X-Plex-Client-Identifier": "auditarr",
                "X-Plex-Product": "Auditarr",
                "X-Plex-Version": "0.1.0",
            },
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/identity")
                if response.status_code == 401:
                    return HealthReport(
                        status="error", detail="Plex token rejected (401)"
                    )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(status="error", detail=f"HTTP error: {exc}")
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        info = payload.get("MediaContainer", payload)
        return HealthReport(
            status="ok",
            detail=info.get("friendlyName") or info.get("machineIdentifier"),
            metadata={
                "version": info.get("version"),
                "platform": info.get("platform"),
                "machine_identifier": info.get("machineIdentifier"),
            },
        )

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        async with self._client(config) as client:
            response = await client.get("/library/sections")
            response.raise_for_status()
            payload = response.json()

        sections = (payload.get("MediaContainer") or {}).get("Directory") or []
        out: list[DiscoveredLibrary] = []
        for section in sections:
            kind = PLEX_KIND_TO_AUDITARR.get(section.get("type") or "", "mixed")
            # Plex Sections expose 1+ Location entries with a `path` attribute.
            locations = section.get("Location") or []
            root_path = (
                str(locations[0].get("path"))
                if locations and locations[0].get("path")
                else None
            )
            out.append(
                DiscoveredLibrary(
                    upstream_id=str(section.get("key") or ""),
                    name=str(section.get("title") or section.get("key")),
                    kind=kind,
                    root_path=root_path,
                    metadata={
                        "agent": section.get("agent"),
                        "scanner": section.get("scanner"),
                        "language": section.get("language"),
                        "uuid": section.get("uuid"),
                    },
                )
            )
        return out

    async def sync_tags(self, config: IntegrationConfig) -> list[TagSync]:
        # Stage 5 ships read-only Plex; tag mirroring is a later add.
        return []

    async def fetch_playback_events(
        self, config: IntegrationConfig, since: _dt.datetime | None
    ) -> list[PlaybackEventDTO]:
        """Stage 16: pull session history and classify per-entry.

        Plex's ``/status/sessions/history/all`` returns one entry per
        completed play, with the full Media/Part tree showing how the
        server actually streamed it. We diff source vs target codec /
        container to decide direct_play / direct_stream / transcode.
        """
        async with self._client(config) as client:
            # Plex history filter: ``viewedAt>=<unix>`` returns events
            # after that timestamp. If no cursor, default to 24h back so
            # first-poll doesn't drown us in years of history.
            cutoff = since or (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=1))
            params = {
                "sort": "viewedAt:desc",
                # Plex accepts ``viewedAt>=`` as a query op.
                "viewedAt>=": int(cutoff.timestamp()),
                # Cap the page at 200 — enough for a 15-minute poll
                # window on any realistic deployment.
                "X-Plex-Container-Start": 0,
                "X-Plex-Container-Size": 200,
            }
            try:
                response = await client.get(
                    "/status/sessions/history/all", params=params
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._log.warning(
                    "plex.playback.fetch_failed", error=str(exc)
                )
                return []

            payload = response.json().get("MediaContainer", {})
            entries = payload.get("Metadata", []) or []

        events: list[PlaybackEventDTO] = []
        for entry in entries:
            event = _plex_history_to_event(entry)
            if event is not None:
                events.append(event)
        return events


def _plex_history_to_event(entry: dict) -> PlaybackEventDTO | None:
    """Translate a single Plex history Metadata entry → DTO.

    Returns None when the entry lacks a file path (e.g. trailers,
    or items where Plex didn't record the source Part), or when the
    payload shape is malformed in ways we can't safely interpret.

    Plex's history response is forgiving about missing fields, so we
    don't trust any nested .get() chain without nullability checks.
    A single bad entry must not poison the whole batch.
    """
    try:
        # ratingKey + viewedAt uniquely identifies a play.
        rating_key = entry.get("ratingKey")
        viewed_at_raw = entry.get("viewedAt")
        if not rating_key or not viewed_at_raw:
            return None
        # viewedAt is Unix seconds; some Plex builds return it as
        # string. _safe_int handles both, returning None on garbage.
        viewed_at = _safe_int(viewed_at_raw)
        if viewed_at is None:
            return None
        upstream_id = f"plex:{rating_key}:{viewed_at}"

        # File path lives under Media[0].Part[0].file.
        media_arr = entry.get("Media") or []
        if not media_arr or not isinstance(media_arr, list):
            return None
        media0 = media_arr[0] if isinstance(media_arr[0], dict) else None
        if not media0:
            return None
        parts = media0.get("Part") or []
        if not parts or not isinstance(parts, list):
            return None
        part0 = parts[0] if isinstance(parts[0], dict) else None
        if not part0:
            return None
        source_path = part0.get("file")
        if not source_path or not isinstance(source_path, str):
            return None

        # Classify: Plex's history nodes carry ``videoDecision`` /
        # ``audioDecision`` strings on the Part record.
        video_decision = (part0.get("videoDecision") or "").lower()
        audio_decision = (part0.get("audioDecision") or "").lower()
        if "transcode" in (video_decision, audio_decision):
            decision = "transcode"
        elif "copy" in (video_decision, audio_decision):
            decision = "direct_stream"
        else:
            decision = "direct_play"

        reason_code: str | None = None
        if decision == "transcode":
            target_container = media0.get("container")
            part_container = part0.get("container")
            if (
                target_container
                and part_container
                and target_container != part_container
            ):
                reason_code = "video.container.unsupported"
            elif video_decision == "transcode":
                reason_code = "video.codec.unsupported"
            elif audio_decision == "transcode":
                reason_code = "audio.codec.unsupported"

        # ``Player`` may be missing or null in some history records.
        player = entry.get("Player")
        if not isinstance(player, dict):
            player = {}

        # Duration is in ms in Plex's response; we store seconds.
        duration_ms = _safe_int(entry.get("duration"))
        duration_s = duration_ms // 1000 if duration_ms is not None else None

        return PlaybackEventDTO(
            upstream_id=upstream_id,
            source_path=source_path,
            decision=decision,
            started_at=_dt.datetime.fromtimestamp(viewed_at, tz=_dt.UTC),
            device_kind=player.get("platform") if isinstance(player.get("platform"), str) else None,
            device_name=player.get("title") if isinstance(player.get("title"), str) else None,
            reason_code=reason_code,
            source_codec=media0.get("videoCodec") if isinstance(media0.get("videoCodec"), str) else None,
            source_bitrate_kbps=_safe_int(media0.get("bitrate")),
            source_width=_safe_int(media0.get("width")),
            source_height=_safe_int(media0.get("height")),
            source_container=media0.get("container") if isinstance(media0.get("container"), str) else None,
            target_codec=None,
            target_bitrate_kbps=None,
            completed_at=None,
            duration_s=duration_s,
        )
    except (AttributeError, TypeError, ValueError, KeyError):
        # Any unexpected shape crashes silently — drop the entry,
        # carry on with the batch. The poller logs total fetched vs
        # inserted so operators can see if many entries are being
        # dropped.
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def register(context: PluginContext) -> Plugin:
    log = context.logger()
    provider = PlexProvider(log=log)
    context.register_integration(provider)
    return Plugin(context)
