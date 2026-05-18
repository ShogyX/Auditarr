"""Tracearr integration plugin.

Tracearr (github.com/connorgallopo/Tracearr) is a streaming-access
manager for Plex / Jellyfin / Emby with its own session-history
database. Its read-only public API at ``/api/v1/public/*`` is
documented in ``apps/server/src/routes/public.ts`` upstream and
exposed in-browser at ``/api-docs`` as a Swagger UI.

Auditarr polls Tracearr's ``/api/v1/public/history`` endpoint and
maps each row into a ``PlaybackEventDTO``. The poller then
persists the DTOs to ``playback_events`` exactly as it does for
Plex / Jellyfin — there is no separate ingest path.

Auth: Tracearr issues API tokens of the form ``trr_pub_<token>``
in Settings > General. The operator pastes that token into the
``api_key`` secret field on the Auditarr integration; this plugin
forwards it verbatim in the ``Authorization: Bearer …`` header.

Notes on the data shape:

* Tracearr does NOT expose downstream file paths — its history is
  ``mediaTitle`` + ``serverId`` + user / device metadata. Auditarr's
  ``PlaybackEvent`` row requires a non-NULL ``source_path``, so we
  synthesise one of the form ``tracearr://<serverId>/<title>...``
  that is stable per logical play and surfaces meaningfully in the
  UI. The synthesised path will not join to ``media_files`` (so
  ``media_file_id`` stays NULL), same as Jellyfin rows on hosts
  that haven't run a scan yet.
* Tracearr filters history by ``startDate`` / ``endDate`` (date
  granularity, IANA timezone). The poller hands us a ``since``
  timestamp; we truncate to UTC date and ask Tracearr from that
  day. The unique constraint on ``(integration_id, upstream_id)``
  dedupes the overlap with the previous poll.
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


# Tracearr's public API caps ``pageSize`` at 100 (enforced server-side
# via the Zod schema in ``apps/server/src/routes/public.ts``). Anything
# larger gets rejected with HTTP 400. We default to the cap.
_PAGE_SIZE_MAX = 100
_PAGE_SIZE_DEFAULT = 100

# Safety net: at 100 events per page, 50 iterations is 5,000 events
# per poll. A typical operator with a 15-minute polling cadence ingests
# far fewer; the cap exists so a misbehaving upstream cannot drive an
# unbounded loop. Operators with a larger catch-up window naturally
# converge over several poll cycles.
_PAGE_ITER_CAP = 50

# Auth-token prefix Tracearr issues from Settings > General. Logged
# in error messages so an operator who pasted a Plex / Jellyfin token
# by mistake gets a useful hint.
_TOKEN_PREFIX = "trr_pub_"


class TracearrProvider(IntegrationProvider):
    kind = "tracearr"
    label = "Tracearr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": (
                    "Tracearr's HTTP base URL (e.g. http://tracearr:3000). "
                    "Do not include /api — the plugin appends /api/v1/public "
                    "itself."
                ),
            },
            "page_size": {
                "type": "integer",
                "default": _PAGE_SIZE_DEFAULT,
                "minimum": 1,
                "maximum": _PAGE_SIZE_MAX,
                "description": (
                    f"Page size used when paging Tracearr's /history "
                    f"endpoint. Tracearr caps this at {_PAGE_SIZE_MAX}."
                ),
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

    # ── healthcheck ──────────────────────────────────────────────
    # Tracearr's authoritative health endpoint is the unauthenticated
    # ``/health`` (returns ``{"status":"ok","db":true,...}``). The
    # authenticated ``/api/v1/public/health`` exists too but doubles
    # as a token-validation probe — we try it second so a healthcheck
    # surfaces a misconfigured token as a *degraded* state rather
    # than as ``error``. The remaining paths are legacy fallbacks
    # for non-Tracearr deployments that previously masqueraded as
    # tracearr in operator setups.
    _HEALTH_PATHS: tuple[str, ...] = (
        "/health",
        "/api/v1/public/health",
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
                if response.status_code == 401:
                    return HealthReport(
                        status="degraded",
                        detail=(
                            f"Tracearr {used_path} returned HTTP 401 — "
                            f"the configured API key was rejected. "
                            f"Generate a token (Settings > General) and "
                            f"verify it starts with '{_TOKEN_PREFIX}'."
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
        return []

    async def sync_tags(
        self, _config: IntegrationConfig
    ) -> list[TagSync]:
        return []

    # ── fetch_playback_events ────────────────────────────────────
    async def fetch_playback_events(
        self,
        config: IntegrationConfig,
        since: _dt.datetime | None,
    ) -> list[PlaybackEventDTO]:
        """Fetch playback events after ``since`` from Tracearr.

        Pagination: Tracearr's ``/history`` endpoint pages by
        ``page`` + ``pageSize`` and returns ``{data: [...], meta:
        {total, page, pageSize}}``. We walk pages 1..N until
        ``page * pageSize >= total`` or the per-poll safety cap
        is hit. ``meta.total`` reflects unique plays (Tracearr
        groups sessions by ``reference_id``) so the same logical
        play surfaces once.

        Date filter: Tracearr only filters by *date*, not
        timestamp. We pass ``startDate = since.date()`` (UTC) so
        Tracearr returns everything from that day onward; the
        ``(integration_id, upstream_id)`` unique constraint in
        Auditarr's ``playback_events`` dedupes the overlap with
        the previous poll.
        """
        raw_size = int(
            config.options.get("page_size") or _PAGE_SIZE_DEFAULT
        )
        page_size = max(1, min(_PAGE_SIZE_MAX, raw_size))

        params_base: dict[str, Any] = {
            "pageSize": page_size,
            "timezone": "UTC",
        }
        if since is not None:
            params_base["startDate"] = _date_for_startdate(since)

        results: list[PlaybackEventDTO] = []
        total: int | None = None
        async with self._client(config) as client:
            for page in range(1, _PAGE_ITER_CAP + 1):
                params = {**params_base, "page": page}
                response = await client.get(
                    "/api/v1/public/history", params=params
                )
                response.raise_for_status()
                payload = response.json() or {}
                items = payload.get("data") or []
                for item in items:
                    dto = _map_tracearr_event(item)
                    if dto is not None:
                        results.append(dto)

                meta = payload.get("meta") or {}
                total = meta.get("total") if isinstance(meta, dict) else None
                if not items:
                    break
                if isinstance(total, int) and page * page_size >= total:
                    break

        return results

    async def trigger_search(
        self,
        _config: IntegrationConfig,
        _media_file_path: str,
    ) -> SearchTriggerResult:
        return SearchTriggerResult(
            status="error",
            detail="Tracearr does not accept search commands",
        )


# ── helpers ─────────────────────────────────────────────────────


def _date_for_startdate(since: _dt.datetime) -> str:
    """Tracearr's ``startDate`` is a date in the configured
    timezone (we pass ``timezone=UTC``). Trim ``since`` to its
    UTC date so we get an inclusive "from this day onward"
    filter."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=_dt.UTC)
    return since.astimezone(_dt.UTC).date().isoformat()


def _decision_from(item: dict[str, Any]) -> str:
    """Project Tracearr's video / audio decisions onto Auditarr's
    canonical playback decision.

    Tracearr exposes a per-stream decision per track
    (``directplay`` | ``copy`` | ``transcode``) plus a top-level
    ``isTranscode`` boolean. We collapse to:

    * ``transcode`` when either track was re-encoded.
    * ``direct_stream`` when at least one track was remuxed
      (``copy``) and nothing was transcoded.
    * ``direct_play`` when both tracks were ``directplay`` (or
      when we have no decision info but ``isTranscode`` is
      false).
    * ``failed`` is not exposed in Tracearr history rows (failed
      sessions don't enter the session table); we never emit
      it.
    """
    video = (item.get("videoDecision") or "").lower()
    audio = (item.get("audioDecision") or "").lower()
    if "transcode" in (video, audio) or bool(item.get("isTranscode")):
        return "transcode"
    if "copy" in (video, audio):
        return "direct_stream"
    return "direct_play"


def _synth_source_path(item: dict[str, Any]) -> str:
    """Synthesize a stable, human-meaningful pseudo-path for the
    event.

    Tracearr does not surface downstream file paths, so we
    cannot satisfy Auditarr's ``source_path NOT NULL`` from the
    raw payload. We build a URI-shaped string keyed on
    ``serverId`` + media identity so plays of the same item
    sort together and operators see something legible on the
    Playback Insight detail page.
    """
    server_id = str(item.get("serverId") or "tracearr")
    media_type = str(item.get("mediaType") or "unknown")
    title = str(item.get("mediaTitle") or "untitled").strip() or "untitled"
    year = item.get("year")
    show = item.get("showTitle")
    season = item.get("seasonNumber")
    episode = item.get("episodeNumber")

    if media_type == "episode" and show:
        # "Breaking Bad/S05E16 — Felina"
        suffix = ""
        if season is not None and episode is not None:
            suffix = f"/S{int(season):02d}E{int(episode):02d}"
        leaf = f"{show}{suffix} — {title}"
    elif media_type == "track":
        artist = item.get("artistName") or ""
        album = item.get("albumName") or ""
        leaf = "/".join(p for p in (artist, album, title) if p)
    else:
        leaf = f"{title} ({int(year)})" if isinstance(year, int) else title

    # Strip leading slashes from the leaf so the joined path is clean.
    return f"tracearr://{server_id}/{media_type}/{leaf.lstrip('/')}"


def _parse_iso8601(raw: Any) -> _dt.datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _opt_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_tracearr_event(item: dict[str, Any]) -> PlaybackEventDTO | None:
    """Map one Tracearr ``/api/v1/public/history`` row to a
    :class:`PlaybackEventDTO`.

    Tracearr's row shape (excerpted from
    ``apps/server/src/routes/public.ts``):

    .. code-block:: json

      {
        "id": "<play-uuid>",
        "serverId": "<server-uuid>",
        "serverName": "Main Plex Server",
        "state": "stopped",
        "mediaType": "episode",
        "mediaTitle": "Felina",
        "showTitle": "Breaking Bad",
        "seasonNumber": 5,
        "episodeNumber": 16,
        "year": 2013,
        "startedAt": "2026-05-17T10:00:00.000Z",
        "stoppedAt": "2026-05-17T11:00:00.000Z",
        "durationMs": 3600000,
        "platform": "tvOS",
        "player": "Plex for Apple TV",
        "device": "Apple TV",
        "isTranscode": false,
        "videoDecision": "directplay",
        "audioDecision": "directplay",
        "bitrate": 8200,
        "sourceVideoCodec": "hevc",
        "sourceVideoWidth": 1920,
        "sourceVideoHeight": 1080,
        "sourceVideoDetails": {"bitrate": 8200},
        "streamVideoCodec": "hevc",
        "streamVideoDetails": {"bitrate": 8200},
        "transcodeInfo": {"reasons": ["video.codec.unsupported"]}
      }

    Returns ``None`` if the row is missing one of the required
    fields (``id``, ``startedAt``) — a malformed row should not
    abort the rest of the batch.
    """
    upstream_id = item.get("id")
    started_at = _parse_iso8601(item.get("startedAt"))
    if not upstream_id or started_at is None:
        return None

    stopped_at = _parse_iso8601(item.get("stoppedAt"))
    duration_ms = item.get("durationMs")
    duration_s: int | None = None
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        duration_s = int(duration_ms // 1000)
    elif isinstance(duration_ms, str):
        try:
            duration_s = int(int(duration_ms) // 1000) or None
        except ValueError:
            duration_s = None

    source_video_details = item.get("sourceVideoDetails") or {}
    if not isinstance(source_video_details, dict):
        source_video_details = {}
    stream_video_details = item.get("streamVideoDetails") or {}
    if not isinstance(stream_video_details, dict):
        stream_video_details = {}

    source_bitrate = _opt_int(source_video_details.get("bitrate"))
    if source_bitrate is None:
        # Some Tracearr rows omit sourceVideoDetails entirely.
        # The row-level ``bitrate`` field is the *stream* bitrate
        # (post-transcode); we use it only when nothing better is
        # available.
        source_bitrate = (
            _opt_int(item.get("bitrate"))
            if not _decision_from(item) == "transcode"
            else None
        )
    target_bitrate = _opt_int(stream_video_details.get("bitrate"))
    if target_bitrate is None and _decision_from(item) == "transcode":
        target_bitrate = _opt_int(item.get("bitrate"))

    transcode_info = item.get("transcodeInfo") or {}
    reasons = (
        transcode_info.get("reasons") if isinstance(transcode_info, dict) else None
    )
    reason_code: str | None = None
    if isinstance(reasons, list) and reasons:
        # Tracearr emits machine-style reason codes
        # ("video.codec.unsupported"); join multiple for visibility.
        reason_code = ",".join(str(r) for r in reasons if r)[:128] or None

    return PlaybackEventDTO(
        upstream_id=str(upstream_id),
        source_path=_synth_source_path(item),
        decision=_decision_from(item),
        started_at=started_at,
        completed_at=stopped_at,
        duration_s=duration_s,
        device_kind=(
            str(item.get("platform"))
            if item.get("platform") is not None
            else None
        ),
        device_name=(
            str(
                item.get("player")
                or item.get("product")
                or item.get("device")
                or ""
            )
            or None
        ),
        source_codec=(
            str(item.get("sourceVideoCodec"))
            if item.get("sourceVideoCodec") is not None
            else None
        ),
        source_width=_opt_int(item.get("sourceVideoWidth")),
        source_height=_opt_int(item.get("sourceVideoHeight")),
        source_bitrate_kbps=source_bitrate,
        target_codec=(
            str(item.get("streamVideoCodec"))
            if item.get("streamVideoCodec") is not None
            else None
        ),
        target_bitrate_kbps=target_bitrate,
        reason_code=reason_code,
    )


def register(context: PluginContext) -> Plugin:
    context.register_integration(TracearrProvider(log=context.logger()))
    return Plugin(context)
