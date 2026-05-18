"""Tracearr integration plugin (v1.9 Stage 6.4).

Tracearr is a third-party playback-history aggregator that
exposes a unified API over multiple downstream sources (Plex,
Jellyfin, etc.) with retention and normalization that this
project doesn't itself implement. For operators who already run
Tracearr, polling its API is the path of least resistance to a
"every play is logged" outcome — much cheaper than running
Auditarr's own Plex / Jellyfin pollers in parallel.

What this plugin does:

  * ``healthcheck``: GET /api/health, normalize response.
  * ``fetch_playback_events(since)``: GET /api/playback/history?
    since=<iso> (paginated; we collapse pages into one batch
    since the poller hands us back-pressure via the cursor).
  * No tag sync, no library discovery, no transcode submission.

The DTO shape matches Plex/Jellyfin so the same downstream
poller code path handles all three. ``upstream_id`` is
Tracearr's own event UUID — stable per logical session, so
dedup via ``(integration_id, upstream_id)`` works the same way.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

import httpx

from app.core.http import async_client
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    PlaybackEventDTO,
    SearchTriggerResult,
    TagSync,
)
from app.plugins import Plugin, PluginContext


class TracearrProvider(IntegrationProvider):
    kind = "tracearr"
    label = "Tracearr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            # Tracearr is API-key authenticated; we send it as a
            # bearer token. The field name matches Sonarr / Radarr
            # / Plex convention for operator familiarity.
            "page_size": {
                "type": "integer",
                "default": 200,
                "minimum": 1,
                "maximum": 1000,
                "description": "Max events per page when paginating.",
            },
        },
        "required": ["base_url"],
    }
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log=None) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base = str(config.options.get("base_url", "")).rstrip("/")
        api_key = str(config.secrets.get("api_key", ""))
        return async_client(
            base_url=base,
            headers={
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "Accept": "application/json",
            },
            timeout=15.0,
        )

    # v1.9 audit fix (OP-11) — Tracearr's health endpoint path
    # is not standardized across versions. Different builds
    # expose ``/health``, ``/api/health``, ``/api/v1/health``, or
    # ``/status``. Try each in order until one returns a
    # non-404 response. This avoids the operator-facing
    # "Tracearr /api/health returned HTTP 404" error when the
    # configured Tracearr build uses a different path.
    _HEALTH_PATHS: tuple[str, ...] = (
        "/health",
        "/api/health",
        "/api/v1/health",
        "/status",
    )

    async def healthcheck(
        self, config: IntegrationConfig
    ) -> HealthReport:
        last_error: str | None = None
        try:
            async with self._client(config) as client:
                response = None
                used_path: str | None = None
                for path in self._HEALTH_PATHS:
                    try:
                        candidate = await client.get(path)
                    except httpx.HTTPError as exc:
                        last_error = f"{path}: {exc}"
                        continue
                    # 404 means "this path doesn't exist on this
                    # Tracearr build" — try the next candidate.
                    if candidate.status_code == 404:
                        last_error = (
                            f"{path}: HTTP 404 (not this build's "
                            f"health endpoint)"
                        )
                        continue
                    response = candidate
                    used_path = path
                    break

                if response is None:
                    return HealthReport(
                        status="error",
                        detail=(
                            "Tracearr healthcheck failed: none of the "
                            "known health paths responded ("
                            + ", ".join(self._HEALTH_PATHS)
                            + f"). Last error: {last_error or 'no detail'}"
                        ),
                    )
                if response.status_code >= 400:
                    return HealthReport(
                        status="error",
                        detail=(
                            f"Tracearr {used_path} returned HTTP "
                            f"{response.status_code}"
                        ),
                    )
                # Tracearr's health endpoint returns
                # {"status": "ok", "version": "..."}. We treat any
                # status string other than "ok" as degraded. Some
                # builds return plain text ("OK") — handle both.
                try:
                    payload = response.json() or {}
                except (ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "").lower()
                    version = payload.get("version")
                else:
                    status = ""
                    version = None
                if status and status not in ("ok", "healthy", "up"):
                    return HealthReport(
                        status="degraded",
                        detail=f"Tracearr health: {status}",
                    )
                return HealthReport(
                    status="ok",
                    detail=(
                        f"Tracearr {version} ({used_path})"
                        if version
                        else f"Tracearr ({used_path})"
                    ),
                )
        except httpx.HTTPError as exc:
            return HealthReport(
                status="error", detail=f"HTTP error: {exc}"
            )

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        # Tracearr doesn't expose libraries — it pulls history from
        # downstream services. Return [] so the libraries page
        # doesn't try to render an empty discovery card.
        return []

    async def sync_tags(
        self, _config: IntegrationConfig
    ) -> list[TagSync]:
        # No tag sync. Tracearr exposes playback events, not
        # library metadata.
        return []

    async def fetch_playback_events(
        self,
        config: IntegrationConfig,
        since: _dt.datetime | None,
    ) -> list[PlaybackEventDTO]:
        """Fetch playback events after ``since`` from Tracearr.

        Pagination: Tracearr's history endpoint is paginated with
        a cursor token; we follow ``next`` cursor links until the
        server stops returning one. We cap the loop at 50
        iterations as a safety net — at the default page_size of
        200 events per page that's 10,000 events per poll, which
        is well beyond what a typical poll cycle should ingest.
        Operators with unusually large catch-up windows just see
        more rapid subsequent polls naturally narrowing the gap.
        """
        page_size = int(config.options.get("page_size") or 200)
        params: dict[str, Any] = {"limit": page_size}
        if since is not None:
            params["since"] = since.isoformat()

        results: list[PlaybackEventDTO] = []
        next_cursor: str | None = None
        for _ in range(50):
            if next_cursor:
                params["cursor"] = next_cursor
            async with self._client(config) as client:
                response = await client.get(
                    "/api/playback/history", params=params
                )
                response.raise_for_status()
                payload = response.json() or {}
            events = payload.get("events") or []
            for ev in events:
                dto = _map_tracearr_event(ev)
                if dto is not None:
                    results.append(dto)
            next_cursor = (payload.get("paging") or {}).get("next")
            if not next_cursor:
                break
        return results

    # ── v1.9 Stage 5.1 — trigger_search not applicable here ─────
    # Tracearr is read-only telemetry — it doesn't accept search
    # commands. Returning an explicit "error" status (rather than
    # leaving the method off the class) keeps the runtime-
    # checkable Protocol satisfied and makes the rule engine's
    # audit log explicit if an operator accidentally points a
    # search_upstream action at a tracearr integration.
    async def trigger_search(
        self,
        _config: IntegrationConfig,
        _media_file_path: str,
    ) -> SearchTriggerResult:
        return SearchTriggerResult(
            status="error",
            detail="Tracearr does not accept search commands",
        )


def _map_tracearr_event(ev: dict[str, Any]) -> PlaybackEventDTO | None:
    """Map one Tracearr ``events[]`` entry to a PlaybackEventDTO.

    Tracearr's event shape:
      {
        "id": "evt-uuid",
        "started_at": "2026-05-17T10:00:00Z",
        "ended_at": "2026-05-17T10:45:00Z",  (optional)
        "source_path": "/data/movies/X.mkv",
        "decision": "transcode" | "direct_play" | "direct_stream" | "failed",
        "user": "alice",                       (optional)
        "client": {                            (optional)
          "name": "Plex Web",
          "platform": "Browser"
        },
        "media": {                             (optional, mostly
          "codec": "hevc",                       diagnostic data
          "width": 1920,                         the analyzer can use
          "height": 1080,                        downstream)
          "bitrate_kbps": 8200
        }
      }

    Returns None if required fields are missing — we don't want
    a malformed event to abort the entire poll batch.
    """
    upstream_id = ev.get("id")
    source_path = ev.get("source_path")
    started_at_raw = ev.get("started_at")
    decision = ev.get("decision") or "direct_play"
    if not (upstream_id and source_path and started_at_raw):
        return None
    try:
        started_at = _dt.datetime.fromisoformat(
            str(started_at_raw).replace("Z", "+00:00")
        )
    except ValueError:
        return None

    media = ev.get("media") or {}
    client = ev.get("client") or {}

    return PlaybackEventDTO(
        upstream_id=str(upstream_id),
        source_path=str(source_path),
        decision=str(decision),
        started_at=started_at,
        device_kind=str(client.get("platform") or "") or None,
        device_name=str(client.get("name") or "") or None,
        source_codec=str(media.get("codec") or "") or None,
        source_width=(
            int(media["width"]) if media.get("width") is not None else None
        ),
        source_height=(
            int(media["height"])
            if media.get("height") is not None
            else None
        ),
        source_bitrate_kbps=(
            int(media["bitrate_kbps"])
            if media.get("bitrate_kbps") is not None
            else None
        ),
    )


def register(context: PluginContext) -> Plugin:
    context.register_integration(TracearrProvider(log=context.logger()))
    return Plugin(context)
