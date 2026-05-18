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

Stage 08 (v1.7) added the third-party transcode hand-off surface,
constrained per addendum B.6 to the documented endpoint set:

* ``submit_transcode_job`` — PUT
  ``/library/metadata/{ratingKey}/optimize`` with the documented body
  params ``targetTagID`` (1=Original, 2=Mobile, 3=TV) and
  ``videoQuality`` / ``videoResolution``. When the job spec doesn't
  carry an explicit ``metadata.ratingKey``, the provider
  auto-resolves it from the file's path: ``path_mappings`` is
  applied in reverse (Auditarr→Plex direction), then Plex's library
  sections are walked to find the matching ``Media.Part.file``.
  This makes the hand-off automatic for the common case where
  Auditarr and Plex see the same file via consistent paths or a
  single path_mappings rule. ``metadata.provider_profile_id`` is
  still required — Plex can't guess between Original / Mobile /
  TV / smart-playlist targets; that's the operator's profile-level
  choice.
* ``list_transcode_profiles`` — GET
  ``/playlists/all?playlistType=video&smart=1`` enumerates the
  smart-playlist targets the operator created in Plex (plus the
  three built-in targets as synthetic entries).
* ``get_transcode_job_status`` — best-effort against
  ``/library/optimize``. Plex doesn't expose a clean per-job
  status; we look for the original ratingKey in the optimize
  queue. Absent = ``"completed"``; present = ``"running"``;
  HTTP error = ``"unknown"``.

The addendum is explicit: *the implementer does not invent or
guess endpoints*. If reality differs from this spec when wiring
against a real Plex, the implementer halts and documents.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.core.http import async_client
from app.core.logging import get_logger
from app.core.sse import is_reconnecting_event, stream_events
from app.integrations.path_mapping import (
    PATH_MAPPINGS_SCHEMA_FRAGMENT,
    parse_mappings,
    remap_path_inverse,
)
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    JobSubmitResult,
    LivePlaybackDTO,
    PlaybackEventDTO,
    TagSync,
    TranscodeJobSpec,
    TranscodeJobStatus,
    TranscodeProfileSummary,
)
from app.plugins import Plugin, PluginContext

# Module-level logger used by the standalone parser helpers
# (``_plex_history_to_event`` / ``_plex_live_to_dto``). The
# ``PlexProvider`` methods themselves use the bound logger
# they receive at construction; helpers fall back to this one.
_module_log = get_logger("auditarr.plex.parser", category="playback")

# Stage 08 (v1.7): Plex's three built-in transcode targets per
# addendum B.6. The IDs are stable across Plex versions and are
# documented in the Plex optimization workflow. Smart playlists
# (custom operator-created transcode targets) are enumerated at
# runtime via ``/playlists?smart=1``.
PLEX_BUILTIN_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("1", "Original Quality", "Plex's 'Optimized for Original Quality' target."),
    ("2", "Mobile", "Plex's 'Optimized for Mobile' target."),
    ("3", "TV", "Plex's 'Optimized for TV' target."),
)

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
            "source_whitelist": {
                "type": "array",
                "title": "Inbound webhook source whitelist",
                "description": (
                    "Stage 11 (v1.7) — optional. One entry per line: "
                    "IP, CIDR range, or hostname. When set, the "
                    "inbound webhook endpoint for this integration "
                    "rejects requests from any source NOT in the "
                    "list (HTTP 403). Leave blank for the default "
                    "behaviour (no source check)."
                ),
                "items": {"type": "string"},
                "default": [],
            },
        },
    }
    secret_fields: tuple[str, ...] = ("token",)

    # v1.9 Stage 6.1 — TTL cache for ratingKey resolution.
    # ``_resolve_rating_key_from_path`` paginates the entire video
    # library for every transcode submission. For an operator with
    # many transcode rules firing per scan, the same path may be
    # resolved twice in a row. A short TTL (default 60s) collapses
    # those duplicates without keeping stale data when the operator
    # renames a file — within 60s the next resolution re-scans Plex
    # and picks up the new path.
    #
    # Per-instance state, NOT class-level: the rule engine builds
    # a single provider instance per process via the plugin
    # registry, but tests construct fresh instances; class-level
    # would leak between tests.
    _RATING_KEY_TTL_SECONDS = 60

    def __init__(self, log: Any) -> None:
        self._log = log
        self._rating_key_cache: dict[
            tuple[str, str], tuple[str, _dt.datetime]
        ] = {}

    # ── HTTP helpers ─────────────────────────────────────────────
    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Plex integration is missing 'base_url'")
        token = str(config.secrets.get("token", "")).strip()
        if not token:
            raise ValueError("Plex integration is missing 'token'")
        # v1.7.2: route through app.core.http.async_client so the
        # CA-bundle resolution is centralised. Without this the
        # plain ``httpx.AsyncClient(verify=True)`` form fails with
        # an opaque FileNotFoundError on hosts whose certifi /
        # OS CA bundle isn't where httpx expects.
        verify_ssl = config.options.get("verify_ssl", True)
        client_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": float(config.options.get("timeout_seconds", 15)),
            "headers": {
                "X-Plex-Token": token,
                "Accept": "application/json",
                "X-Plex-Client-Identifier": "auditarr",
                "X-Plex-Product": "Auditarr",
                "X-Plex-Version": "1.8.3",
            },
        }
        # Only pass verify=False when the operator explicitly
        # disabled it. Otherwise let async_client resolve the
        # bundle (or fall back to its own warning).
        if verify_ssl is False:
            client_kwargs["verify"] = False
        return async_client(**client_kwargs)

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

        Wire-format notes (bugfix 2026-05-17):
          * Pagination is via the ``X-Plex-Container-Start`` and
            ``X-Plex-Container-Size`` HEADERS, not query params.
            Plex Media Server ignores them as query params on some
            builds and returns its default page (which may be
            smaller than we wanted, or omit fields we expected).
          * We filter by ``viewedAt`` cutoff in Python rather than
            via Plex's URL-operator filter (``viewedAt>=<unix>``).
            The operator is unreliable across PMS versions — httpx
            URL-encodes the ``>=`` to ``%3E%3D``, which some PMS
            builds decode-and-match correctly and others silently
            ignore. Filtering in Python costs us one extra
            comparison per entry and gives us a deterministic
            cutoff that works against every PMS version.
          * We sort desc by viewedAt so the newest events are at
            the front of the page; combined with a generous
            container size (500), even a busy server's last hour
            of history fits in one page.
        """
        async with self._client(config) as client:
            cutoff = since or (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=1))
            cutoff_unix = int(cutoff.timestamp())
            try:
                response = await client.get(
                    "/status/sessions/history/all",
                    params={"sort": "viewedAt:desc"},
                    headers={
                        # Pagination as HEADERS per Plex docs.
                        "X-Plex-Container-Start": "0",
                        "X-Plex-Container-Size": "500",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._log.warning(
                    "plex.playback.fetch_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []

            try:
                payload = response.json().get("MediaContainer", {})
            except ValueError as exc:
                # Plex returned non-JSON (XML, an HTML error page, etc.).
                # The default Accept on _client is application/json, but
                # an upstream proxy or a misconfigured reverse proxy can
                # strip it.
                self._log.warning(
                    "plex.playback.fetch_parse_failed",
                    error=str(exc),
                    content_type=response.headers.get("content-type"),
                )
                return []
            entries = payload.get("Metadata", []) or []

        events: list[PlaybackEventDTO] = []
        filtered_out = 0
        for entry in entries:
            event = _plex_history_to_event(entry)
            if event is None:
                continue
            # Python-side cutoff filter. Plex's URL filter operator
            # is unreliable across versions; this is deterministic.
            if event.started_at and event.started_at < cutoff:
                filtered_out += 1
                continue
            events.append(event)

        self._log.info(
            "plex.playback.fetched",
            count=len(events),
            raw_count=len(entries),
            filtered_out_pre_cutoff=filtered_out,
            cutoff_unix=cutoff_unix,
        )
        return events

    # ── Stage 09 (v1.7) — live (in-progress) playback ────────────
    async def fetch_live_playbacks(
        self, config: IntegrationConfig
    ) -> list[LivePlaybackDTO]:
        """Stage 09 (plan §483): return Plex's currently-active
        sessions via the documented ``/status/sessions`` endpoint.

        Plex returns a ``MediaContainer.Metadata`` list of session
        snapshots; each entry carries:
          * ``sessionKey`` — stable for the session's lifetime.
          * ``Player.state`` — ``"playing"`` / ``"paused"`` /
            ``"buffering"``.
          * ``viewOffset`` / ``duration`` (ms) — progress.
          * ``User.title`` — username.
          * ``Media[].Part[].file`` — path on Plex's filesystem
            (the aggregating endpoint applies path mappings).
          * Source + transcode stream details on
            ``Media[].videoCodec``, ``Media[].bitrate``,
            ``TranscodeSession.videoDecision``, etc.

        Errors degrade to an empty list so a transiently
        unreachable Plex doesn't break the dashboard tile, but
        we log every degradation path explicitly so the
        operator (and us, debugging) can see WHY the tile is
        empty.
        """
        async with self._client(config) as client:
            try:
                response = await client.get("/status/sessions")
                response.raise_for_status()
            except httpx.HTTPError as exc:
                self._log.warning(
                    "plex.live.fetch_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []
            try:
                payload = response.json().get("MediaContainer", {})
            except ValueError as exc:
                # Plex returned non-JSON. The default Accept on
                # _client is application/json, but a reverse
                # proxy can strip headers, and some PMS builds
                # return XML when they think the client is a
                # browser. Log the content-type so the operator
                # can see whether the issue is proxying or the
                # PMS itself.
                self._log.warning(
                    "plex.live.fetch_parse_failed",
                    error=str(exc),
                    content_type=response.headers.get("content-type"),
                )
                return []
            entries = payload.get("Metadata", []) or []

        sessions: list[LivePlaybackDTO] = []
        skipped = 0
        for entry in entries:
            dto = _plex_live_to_dto(entry, _log=self._log)
            if dto is not None:
                sessions.append(dto)
            else:
                skipped += 1
        if entries:
            # Always log when Plex returned ANY entries, so we
            # have a paper trail of whether the request actually
            # found sessions on the server.
            self._log.info(
                "plex.live.fetched",
                count=len(sessions),
                raw_count=len(entries),
                skipped=skipped,
            )
        return sessions

    # ── Stage 08 (v1.7) — transcode hand-off (addendum B.6) ──────
    async def _resolve_rating_key_from_path(
        self,
        config: IntegrationConfig,
        auditarr_path: str,
    ) -> tuple[str | None, str | None]:
        """Auto-look-up a Plex ratingKey for an Auditarr file path.

        Stage 08 enhancement (operator request): we don't want
        operators to pre-supply a ratingKey for every transcode
        hand-off. The Auditarr file already has a path, and Plex
        already knows the path to the same file (possibly via a
        path-mapping rewrite); we can correlate.

        Returns ``(rating_key, error_detail)``. On success
        ``rating_key`` is the string and ``error_detail`` is None;
        on failure ``rating_key`` is None and ``error_detail``
        explains why (so the caller can return a clean rejection
        message rather than guessing).

        Algorithm:
          1. Apply the integration's ``path_mappings`` inverse
             to translate the Auditarr path to the path Plex
             would report on its Part records.
          2. List Plex video sections via ``/library/sections``.
          3. For each video section, paginate
             ``/library/sections/{key}/all?type=1`` (movies) and
             ``?type=4`` (episodes); inspect ``Media[].Part[].file``
             on each entry; return the matching ratingKey.

        Cost is one ``/sections`` round-trip plus one paginated
        ``/all`` per video section. For most home setups that's
        2-3 small calls.

        v1.9 Stage 6.1 — TTL-cached. The same ``(integration_id,
        auditarr_path)`` pair returns cached result within
        ``_RATING_KEY_TTL_SECONDS`` (60s). The cache holds only
        successful resolutions — a previous "not found" result
        rechecks every call so the operator who just added the
        file to Plex doesn't have to wait for the TTL. Per-
        instance state; tests use fresh PlexProvider() so cache
        doesn't leak.
        """
        # Cache check — only successful resolutions are cached
        # (errors and None results are not, so a transient outage
        # doesn't pin a bad answer for the TTL window).
        cache_key = (config.integration_id, auditarr_path)
        cached = self._rating_key_cache.get(cache_key)
        if cached is not None:
            rating_key, expires_at = cached
            if _dt.datetime.now(_dt.UTC) < expires_at:
                return rating_key, None
            # Expired — drop it.
            self._rating_key_cache.pop(cache_key, None)

        mappings = parse_mappings(config.options.get("path_mappings"))
        plex_side_path = remap_path_inverse(auditarr_path, mappings)

        try:
            async with self._client(config) as client:
                sections_resp = await client.get(
                    "/library/sections",
                    headers={"Accept": "application/json"},
                )
                sections_resp.raise_for_status()
                sections_body = sections_resp.json()
        except httpx.HTTPError as exc:
            return None, f"Plex section enumeration failed: {exc!s}"
        except ValueError as exc:
            return None, f"Plex returned an unparseable sections payload: {exc!s}"

        sections = (
            sections_body.get("MediaContainer", {}).get("Directory", []) or []
        )
        # Only walk video-bearing sections; ``movie`` and ``show``
        # both have ``Media.Part.file`` we can match against. Music
        # libraries can be transcoded by Plex but Auditarr's
        # optimization model is video-first; revisit when music
        # routing lands.
        video_sections = [
            s
            for s in sections
            if isinstance(s, dict)
            and s.get("type") in ("movie", "show")
            and s.get("key")
        ]
        if not video_sections:
            return None, (
                "No video sections found on Plex; cannot resolve "
                f"ratingKey for {auditarr_path!r}"
            )

        for section in video_sections:
            section_key = str(section.get("key"))
            # ``type=1`` is movies; ``type=4`` is episodes (the
            # actual playable nodes inside a show). We try movies
            # first because movie matches are cheaper to scan.
            for plex_type in ("1", "4"):
                if (
                    section.get("type") == "movie" and plex_type == "4"
                ) or (
                    section.get("type") == "show" and plex_type == "1"
                ):
                    # Skip type 4 for movie sections and type 1 for
                    # show sections — they would always be empty.
                    continue
                rating_key = await self._scan_section_for_file(
                    config, section_key, plex_type, plex_side_path
                )
                if rating_key is not None:
                    # v1.9 Stage 6.1 — cache the successful
                    # resolution. Only successes are cached; a
                    # "not found" result rechecks next call so an
                    # operator who just added the file doesn't
                    # have to wait for the TTL.
                    expires_at = _dt.datetime.now(
                        _dt.UTC
                    ) + _dt.timedelta(seconds=self._RATING_KEY_TTL_SECONDS)
                    self._rating_key_cache[cache_key] = (
                        rating_key,
                        expires_at,
                    )
                    return rating_key, None

        return None, (
            f"No Plex item matched path {plex_side_path!r} "
            f"(Auditarr path {auditarr_path!r}). Check the "
            "integration's path_mappings configuration."
        )

    async def _scan_section_for_file(
        self,
        config: IntegrationConfig,
        section_key: str,
        plex_type: str,
        plex_side_path: str,
    ) -> str | None:
        """Walk one section's items looking for a Part with the
        given file path. Returns the matching ratingKey or None.

        Plex paginates with ``X-Plex-Container-Start`` /
        ``X-Plex-Container-Size`` headers; we use a generous
        page size to minimize round-trips, but bound the total
        page count so a misconfigured server can't make us walk
        forever.
        """
        page_size = 500
        max_pages = 200  # 100k items per section is generous.
        offset = 0
        async with self._client(config) as client:
            for _page in range(max_pages):
                try:
                    response = await client.get(
                        f"/library/sections/{section_key}/all",
                        params={"type": plex_type},
                        headers={
                            "Accept": "application/json",
                            "X-Plex-Container-Start": str(offset),
                            "X-Plex-Container-Size": str(page_size),
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError):
                    return None

                container = body.get("MediaContainer", {})
                items = container.get("Metadata", []) or []
                if not items:
                    return None
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    rk = item.get("ratingKey")
                    if not rk:
                        continue
                    for media in item.get("Media", []) or []:
                        if not isinstance(media, dict):
                            continue
                        for part in media.get("Part", []) or []:
                            if not isinstance(part, dict):
                                continue
                            if part.get("file") == plex_side_path:
                                return str(rk)
                if len(items) < page_size:
                    return None
                offset += page_size
        return None

    async def submit_transcode_job(
        self,
        config: IntegrationConfig,
        job_spec: TranscodeJobSpec,
    ) -> JobSubmitResult:
        """Queue a Plex optimization job.

        Addendum B.6: the supported endpoint is
        ``/library/metadata/{ratingKey}/optimize`` with body
        params ``targetTagID`` (1=Original, 2=Mobile, 3=TV) and
        ``videoQuality`` / ``videoResolution``. Custom Plex
        profiles are smart playlists, referenced by their own
        ratingKey.

        Auto-lookup (Stage 08 operator request): when
        ``metadata.ratingKey`` is not supplied, we resolve the
        ratingKey automatically by inverting the integration's
        path mappings and walking Plex's library sections to
        match the file path. This makes the hand-off automatic
        — operators don't have to pre-pin every item. Operators
        can still pin a specific ratingKey via metadata when
        they want to (e.g. a smart playlist's ratingKey instead
        of the source item's).

        The operator-chosen transcode target id (``"1"``/``"2"``/
        ``"3"`` for built-ins or a smart-playlist ratingKey) is
        still required on ``metadata.provider_profile_id`` —
        Plex can't guess "Mobile" vs "TV" vs "Original"; that's
        the operator's profile-level choice.
        """
        rating_key = job_spec.metadata.get("ratingKey")
        if rating_key is not None and not isinstance(rating_key, str):
            rating_key = None
        if not rating_key:
            # Auto-lookup by path.
            rating_key, lookup_error = await self._resolve_rating_key_from_path(
                config, job_spec.input_path
            )
            if rating_key is None:
                return JobSubmitResult(
                    status="rejected",
                    detail=(
                        f"Plex auto-lookup failed: {lookup_error}. "
                        "Either fix the integration's path_mappings "
                        "configuration or pin a ratingKey explicitly "
                        "on the profile."
                    ),
                )

        target_id = job_spec.metadata.get("provider_profile_id")
        if not target_id or not isinstance(target_id, str):
            return JobSubmitResult(
                status="rejected",
                detail=(
                    "Plex requires a transcode target id (one of "
                    "'1' for Original, '2' for Mobile, '3' for TV, "
                    "or a smart-playlist ratingKey). Edit the "
                    "Auditarr profile and pick one from the list."
                ),
            )

        # Plex's optimize endpoint accepts form-encoded body OR
        # query parameters; we use query params because Plex
        # historically tolerated both and query params are easier
        # to mock cleanly in tests.
        params: dict[str, str] = {"targetTagID": target_id}
        vq = job_spec.metadata.get("video_quality")
        if vq is not None:
            params["videoQuality"] = str(vq)
        vr = job_spec.metadata.get("video_resolution")
        if vr is not None:
            params["videoResolution"] = str(vr)

        endpoint = f"/library/metadata/{rating_key}/optimize"
        try:
            async with self._client(config) as client:
                response = await client.put(endpoint, params=params)
                # Plex returns 200 on accepted; some versions
                # return 201. Anything in the 2xx family means
                # "queued".
                if response.status_code >= 400:
                    # v1.9 audit fix (LOG-AUDIT-2): a 4xx that's
                    # not 401 (auth) or 429 (rate-limit) is a
                    # PERMANENT failure for this item — the media
                    # ID doesn't exist (404), the operator's
                    # plan can't transcode (403), etc. Return
                    # ``rejected`` rather than ``error`` so the
                    # worker doesn't retry the item forever.
                    # 5xx and the two retryable 4xx codes stay
                    # ``error`` (transient).
                    permanent_4xx = (
                        400 <= response.status_code < 500
                        and response.status_code not in (401, 429)
                    )
                    return JobSubmitResult(
                        status="rejected" if permanent_4xx else "error",
                        detail=(
                            f"Plex returned {response.status_code} "
                            f"for {endpoint}"
                        ),
                    )
        except httpx.HTTPError as exc:
            return JobSubmitResult(
                status="error",
                detail=f"Plex HTTP error: {exc!s}",
            )
        except ValueError as exc:
            return JobSubmitResult(status="error", detail=str(exc))

        # Plex's optimize endpoint doesn't return a job id; we
        # synthesize one from the rating key + target so the
        # poller can correlate. Plex tracks the optimization in
        # its internal queue; we'll look it up by ratingKey in
        # ``get_transcode_job_status``.
        synthetic_id = f"plex:{rating_key}:{target_id}"
        return JobSubmitResult(
            status="accepted",
            upstream_job_id=synthetic_id,
            detail=f"queued in Plex for ratingKey={rating_key}",
        )

    # ── v1.9 Stage 6.1 — diagnostics + verify helpers ───────────
    async def diagnostics(
        self, config: IntegrationConfig
    ) -> dict[str, dict[str, object]]:
        """Run four sanity checks against the configured Plex
        server, returning a structured per-check result.

        Probes:
          1. ``/`` — root endpoint reachable + token accepted.
          2. ``/library/sections`` — library listing works (used
             by Auditarr's library discovery).
          3. ``/activities`` — activities endpoint reachable
             (used by some monitoring tools; failing this is a
             soft warning, not an error).
          4. ``/library/optimize`` — the optimize queue endpoint
             that Stage 07's transcode submission writes to. A
             403/404 here is a strong signal that the Plex token
             lacks the "manage" claim.

        Each entry is ``{ok: bool, detail: str, latency_ms: int}``.
        Operators trigger this from the integration row's
        diagnostics button; the dashboard renders the result as
        a compact table so a misconfigured token surfaces in
        seconds rather than after the next scheduled poll.

        Best-effort: a single probe's failure doesn't abort the
        others. Each probe wraps its own HTTP errors so the
        operator sees the full picture from one call.
        """
        results: dict[str, dict[str, object]] = {}
        async with self._client(config) as client:
            for name, path in (
                ("root", "/"),
                ("library_sections", "/library/sections"),
                ("activities", "/activities"),
                ("optimize_queue", "/library/optimize"),
            ):
                results[name] = await _run_diag_probe(client, path)
        return results

    async def verify_optimization_started(
        self, config: IntegrationConfig, upstream_job_id: str
    ) -> bool:
        """After submitting a transcode job, confirm Plex
        actually accepted it by checking the optimize queue.

        Stage 07's ``submit_transcode_job`` synthesizes an
        ``upstream_job_id`` of ``plex:<ratingKey>:<target>``. The
        verification: re-parse the rating key, query
        ``/library/optimize``, return True if the rating key is
        in the queue. Returns False on any error or missing
        rating key — the caller treats False as "submission
        unconfirmed", not "submission failed".
        """
        rating_key = _parse_synthetic_job_id(upstream_job_id)
        if rating_key is None:
            return False
        try:
            async with self._client(config) as client:
                response = await client.get("/library/optimize")
                if response.status_code >= 400:
                    return False
                payload = response.json() or {}
        except httpx.HTTPError:
            return False
        except ValueError:
            return False
        items = (payload.get("MediaContainer") or {}).get("Metadata") or []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            # Plex's optimize queue carries the original
            # ratingKey as ``Item[0].ratingKey``. Iterate items
            # since one optimize job can hold multiple sources.
            sub_items = entry.get("Item") or []
            for sub in sub_items:
                if not isinstance(sub, dict):
                    continue
                if str(sub.get("ratingKey")) == str(rating_key):
                    return True
        return False

    async def verify_optimization_completed(
        self, config: IntegrationConfig, upstream_job_id: str
    ) -> bool:
        """Inverse of ``verify_optimization_started``: True when
        the rating key is NO LONGER in the optimize queue.

        Plex doesn't distinguish "completed" from
        "cancelled / removed" once the entry is gone; the caller
        is responsible for deciding what an absent rating key
        means in its context. For the transcode poller, "gone
        from the queue after we saw it there" is treated as
        completion.

        Returns False when the rating key is still in the queue
        OR when the verification call fails (so a transient
        error doesn't cause the poller to declare premature
        completion)."""
        rating_key = _parse_synthetic_job_id(upstream_job_id)
        if rating_key is None:
            return False
        try:
            async with self._client(config) as client:
                response = await client.get("/library/optimize")
                if response.status_code >= 400:
                    return False
                payload = response.json() or {}
        except httpx.HTTPError:
            return False
        except ValueError:
            return False
        items = (payload.get("MediaContainer") or {}).get("Metadata") or []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            sub_items = entry.get("Item") or []
            for sub in sub_items:
                if not isinstance(sub, dict):
                    continue
                if str(sub.get("ratingKey")) == str(rating_key):
                    return False  # still queued
        return True

    async def list_transcode_profiles(
        self, config: IntegrationConfig
    ) -> list[TranscodeProfileSummary]:
        """List Plex transcode targets.

        Addendum B.6: built-in targets (Original/Mobile/TV) are
        constants; smart playlists are at
        ``/playlists/all?playlistType=video&smart=1``.
        """
        # Built-ins first so they appear at the top of the picker.
        out: list[TranscodeProfileSummary] = [
            TranscodeProfileSummary(
                id=id_, name=name, description=description,
                metadata={"target_kind": "builtin"},
            )
            for id_, name, description in PLEX_BUILTIN_TARGETS
        ]

        # Smart playlists. Plex may return JSON (newer servers) or
        # XML (older servers); we tolerate both because Plex's
        # negotiation can be quirky on this endpoint.
        try:
            async with self._client(config) as client:
                response = await client.get(
                    "/playlists/all",
                    params={"playlistType": "video", "smart": "1"},
                )
                response.raise_for_status()
                # Try JSON first.
                try:
                    body = response.json()
                    metadata = (
                        body.get("MediaContainer", {}).get("Metadata", [])
                        or []
                    )
                    for entry in metadata:
                        if not isinstance(entry, dict):
                            continue
                        rk = entry.get("ratingKey")
                        title = entry.get("title")
                        if rk and title:
                            out.append(
                                TranscodeProfileSummary(
                                    id=str(rk),
                                    name=str(title),
                                    description=(
                                        "Smart playlist transcode target"
                                    ),
                                    metadata={
                                        "target_kind": "smart_playlist",
                                        "summary": entry.get("summary"),
                                    },
                                )
                            )
                except ValueError:
                    # XML fallback. Plex's XML uses
                    # <MediaContainer><Playlist ratingKey="..." title="..."/></MediaContainer>
                    try:
                        root = ET.fromstring(response.text)
                        for playlist in root.findall("Playlist"):
                            rk = playlist.get("ratingKey")
                            title = playlist.get("title")
                            if rk and title:
                                out.append(
                                    TranscodeProfileSummary(
                                        id=rk,
                                        name=title,
                                        description=(
                                            "Smart playlist transcode target"
                                        ),
                                        metadata={
                                            "target_kind": "smart_playlist",
                                        },
                                    )
                                )
                    except ET.ParseError:
                        # Couldn't parse XML either; built-ins
                        # still flow back.
                        pass
        except httpx.HTTPError:
            # Smart-playlist enumeration is best-effort. The
            # built-ins above are always available, so the
            # picker still has options.
            pass

        return out

    async def get_transcode_job_status(
        self,
        config: IntegrationConfig,
        upstream_job_id: str,
    ) -> TranscodeJobStatus:
        """Poll Plex for the status of an optimization job.

        Plex doesn't expose a clean per-job status endpoint. Per
        addendum B.6 we use ``/library/optimize`` (the
        documented endpoint for the optimize queue): the original
        media's ratingKey appears in the queue while the job is
        running and disappears when it completes. Plex's queue
        doesn't distinguish "completed" from "removed", so we
        treat "absent" as ``"completed"`` and surface that
        ambiguity in the detail.

        Synthetic id format ``plex:<ratingKey>:<targetID>``
        (from ``submit_transcode_job``); we parse the ratingKey
        back out to look it up.
        """
        # Parse the synthetic id.
        if not upstream_job_id.startswith("plex:"):
            return TranscodeJobStatus(
                status="unknown",
                detail=(
                    f"unrecognised job id format {upstream_job_id!r}; "
                    "expected 'plex:<ratingKey>:<targetID>'"
                ),
            )
        try:
            _, rating_key, _target = upstream_job_id.split(":", 2)
        except ValueError:
            return TranscodeJobStatus(
                status="unknown",
                detail=(
                    f"malformed Plex job id {upstream_job_id!r}; "
                    "expected 'plex:<ratingKey>:<targetID>'"
                ),
            )

        try:
            async with self._client(config) as client:
                response = await client.get("/library/optimize")
                response.raise_for_status()
                # Optimize queue can be JSON or XML, same shape
                # negotiation as ``list_transcode_profiles``.
                rating_keys_in_queue: set[str] = set()
                try:
                    body = response.json()
                    items = (
                        body.get("MediaContainer", {}).get("Metadata", [])
                        or []
                    )
                    for entry in items:
                        if not isinstance(entry, dict):
                            continue
                        # Plex's optimize queue carries the
                        # source ratingKey on each entry.
                        src = entry.get("sourceRatingKey") or entry.get(
                            "ratingKey"
                        )
                        if src:
                            rating_keys_in_queue.add(str(src))
                except ValueError:
                    try:
                        root = ET.fromstring(response.text)
                        for video in root.iter():
                            src = video.get(
                                "sourceRatingKey"
                            ) or video.get("ratingKey")
                            if src:
                                rating_keys_in_queue.add(src)
                    except ET.ParseError:
                        pass
        except httpx.HTTPError as exc:
            return TranscodeJobStatus(
                status="unknown",
                detail=f"Plex HTTP error: {exc!s}",
            )
        except ValueError as exc:
            return TranscodeJobStatus(status="unknown", detail=str(exc))

        if rating_key in rating_keys_in_queue:
            return TranscodeJobStatus(
                status="running",
                detail="ratingKey still present in Plex optimize queue",
            )
        # Absent — Plex doesn't distinguish "completed" from
        # "removed/cancelled". Surface the ambiguity in the
        # detail; the worker treats it as terminal.
        return TranscodeJobStatus(
            status="completed",
            detail=(
                "ratingKey no longer in Plex optimize queue; Plex "
                "does not distinguish completion from cancellation, "
                "so the job is treated as completed"
            ),
        )

    # ── v1.8.0 / Stage 17: SSE-based session events ─────────────
    async def subscribe_sessions(
        self, config: IntegrationConfig
    ) -> AsyncIterator["PlexSessionEvent"]:
        """Yield session lifecycle events from Plex's notification
        stream.

        Plex exposes ``GET /:/eventsource/notifications`` which holds
        a persistent SSE connection open and pushes JSON-encoded
        events as activity happens. The two interesting channel
        types for us are:

          * ``playing`` — fires when a session changes state
            (start / play / pause / buffering / stopped).
            Payload includes ``sessionKey``, ``state``,
            ``ratingKey``, ``viewOffset``, but not the rich
            session metadata.
          * ``transcodeSession.update`` — fires while a
            transcode is decision-changing. Payload includes
            the transcoder's ``key``, ``videoDecision``,
            ``audioDecision``.

        Because the SSE payload is thin, the *caller* is expected
        to call :meth:`fetch_one_session_snapshot` after each
        playing-state event to enrich the row with codec/path/
        user/device data. We don't enrich inline here because (a)
        snapshot fetches are a separate connection that benefits
        from independent rate limiting, and (b) the session
        manager wants to dedup back-to-back events for the same
        session before paying for the snapshot fetch.

        The iterator never returns under normal conditions —
        it reconnects forever via :func:`stream_events`. The
        worker task wrapping it handles cancellation.

        Yields:
            :class:`PlexSessionEvent` — one per state change,
            with a synthetic kind="reconnecting" emitted after
            each transport reconnect so the session manager can
            re-sync from a snapshot.
        """
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        token = str(config.secrets.get("token", "")).strip()
        if not base_url or not token:
            raise ValueError(
                "Plex SSE requires base_url + token; one is missing"
            )
        url = f"{base_url}/:/eventsource/notifications"
        headers = {
            "X-Plex-Token": token,
            "X-Plex-Client-Identifier": "auditarr",
            "X-Plex-Product": "Auditarr",
            "X-Plex-Version": "1.8.3",
            # NB: Accept defaults to text/event-stream inside
            # stream_events; we don't need to set it here.
        }
        verify_kw: bool | None = (
            False if config.options.get("verify_ssl") is False else None
        )

        async for sse_event in stream_events(
            url, headers=headers, verify=verify_kw
        ):
            if is_reconnecting_event(sse_event):
                yield PlexSessionEvent(
                    kind="reconnecting",
                    session_key=None,
                    state=None,
                    rating_key=None,
                    view_offset_ms=None,
                    raw=None,
                )
                continue

            # Plex's SSE payload is JSON. The outer structure is
            # ``{"NotificationContainer": {"type": "playing",
            # "size": N, "PlaySessionStateNotification": [...]}}``.
            try:
                payload = json.loads(sse_event.data)
            except (ValueError, TypeError) as exc:
                self._log.warning(
                    "plex.sse.parse_failed",
                    error=str(exc),
                    sample=sse_event.data[:200],
                )
                continue

            container = payload.get("NotificationContainer")
            if not isinstance(container, dict):
                # Plex sends keepalive ``{}`` lines sometimes.
                continue

            notification_type = container.get("type")
            if notification_type == "playing":
                notes = container.get("PlaySessionStateNotification") or []
                if not isinstance(notes, list):
                    continue
                for note in notes:
                    if not isinstance(note, dict):
                        continue
                    sk = note.get("sessionKey")
                    if sk is None:
                        continue
                    yield PlexSessionEvent(
                        kind="state",
                        session_key=str(sk),
                        state=(
                            str(note.get("state")) if note.get("state") else None
                        ),
                        rating_key=(
                            str(note.get("ratingKey"))
                            if note.get("ratingKey") is not None
                            else None
                        ),
                        view_offset_ms=_safe_int(note.get("viewOffset")),
                        raw=note,
                    )
            elif notification_type == "transcodeSession.update":
                # Transcoder updated its decision. We don't
                # emit these as state events — the next playing
                # notification will already reflect the new
                # decision in the /status/sessions snapshot.
                pass
            # Other notification types we ignore: ``activity``
            # (library scan progress), ``backgroundProcessing``,
            # ``status``, ``preference``, ``timeline``, etc.

    async def fetch_one_session_snapshot(
        self, config: IntegrationConfig, session_key: str
    ) -> LivePlaybackDTO | None:
        """Fetch a single session's full metadata by session_key.

        Used by the session manager after an SSE event tells us a
        session is in a new state. We fetch ``/status/sessions``
        and find the matching entry. This is the same endpoint
        :meth:`fetch_live_playbacks` uses, just filtered to one.

        Returns None if Plex doesn't have the session (it just
        ended between the SSE event and our fetch — race that
        happens often in practice).
        """
        # ``/status/sessions`` doesn't support filtering by
        # sessionKey on the wire; we fetch the full list and
        # match locally. The list is small (active sessions
        # only) so this is cheap.
        async with self._client(config) as client:
            try:
                response = await client.get("/status/sessions")
            except httpx.HTTPError as exc:
                self._log.warning(
                    "plex.sse.snapshot_fetch_failed",
                    session_key=session_key,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return None
            try:
                payload = response.json().get("MediaContainer") or {}
            except (ValueError, TypeError, AttributeError) as exc:
                self._log.warning(
                    "plex.sse.snapshot_parse_failed",
                    session_key=session_key,
                    error=str(exc),
                )
                return None
            entries = payload.get("Metadata") or []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("sessionKey") or "") == session_key:
                return _plex_live_to_dto(entry, _log=self._log)
        return None


@dataclass(slots=True)
class PlexSessionEvent:
    """One SSE-derived session event.

    ``kind="state"`` is a regular state-change event (start,
    pause, resume, stop). ``kind="reconnecting"`` is the
    synthetic marker emitted after the SSE transport
    reconnects — subscribers should re-sync from a snapshot
    before trusting the next event stream.
    """

    kind: str  # "state" | "reconnecting"
    session_key: str | None
    state: str | None  # "playing" | "paused" | "buffering" | "stopped"
    rating_key: str | None
    view_offset_ms: int | None
    raw: dict | None


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
            # v1.9 OP-10 — surface the rating_key on the DTO so
            # the poller can reconcile this history entry against
            # an existing SSE-tracked PlaybackSession row.
            rating_key=str(rating_key),
        )
    except (AttributeError, TypeError, ValueError, KeyError):
        # Any unexpected shape crashes silently — drop the entry,
        # carry on with the batch. The poller logs total fetched vs
        # inserted so operators can see if many entries are being
        # dropped.
        return None


def _plex_live_to_dto(entry: dict, _log: Any = None) -> LivePlaybackDTO | None:
    """Stage 09 (v1.7) — translate one ``/status/sessions``
    Metadata entry → :class:`LivePlaybackDTO`.

    Returns None when the entry can't be safely interpreted —
    missing path, missing session key, malformed shape. One bad
    entry must not poison the live tile.

    Plex's session shape mirrors the history shape closely but
    adds:
      * ``sessionKey`` — stable session identifier.
      * ``Player.state`` — ``"playing"`` / ``"paused"`` /
        ``"buffering"``.
      * ``viewOffset`` (ms) + ``duration`` (ms) — progress.
      * ``TranscodeSession.videoDecision`` /
        ``audioDecision`` — ``"copy"`` (direct-stream) vs
        ``"transcode"`` (re-encode).

    v1.7.3 (production bug): on a host with a mix of session
    types and decisions, only the first few sessions appeared
    in the live tile. The bare ``except Exception: return None``
    at the function bottom + the four early-return-None paths
    were silently dropping entries with no diagnostic. This
    function now logs every drop with the entry's
    ``sessionKey`` + reason so the next operator hitting a
    similar issue gets a clear signal in the logs. The
    ``_log`` parameter is the provider's bound structlog
    logger; when called without one (e.g. legacy tests) a
    module logger is used.
    """
    log = _log if _log is not None else _module_log
    session_key = entry.get("sessionKey")
    sk = str(session_key) if session_key else "<unknown>"

    if not session_key:
        log.warning(
            "plex.live.session_dropped",
            reason="missing_sessionKey",
            entry_type=entry.get("type"),
            entry_keys=sorted(entry.keys())[:20],
        )
        return None

    try:
        media_list = entry.get("Media") or []
        if not isinstance(media_list, list) or not media_list:
            log.warning(
                "plex.live.session_dropped",
                session_key=sk,
                reason="missing_or_empty_Media",
                entry_type=entry.get("type"),
                title=entry.get("title") or entry.get("grandparentTitle"),
            )
            return None
        first_media = media_list[0]
        if not isinstance(first_media, dict):
            log.warning(
                "plex.live.session_dropped",
                session_key=sk,
                reason="Media[0]_not_a_dict",
                media_0_type=type(first_media).__name__,
                entry_type=entry.get("type"),
            )
            return None

        parts = first_media.get("Part") or []
        if not isinstance(parts, list) or not parts:
            log.warning(
                "plex.live.session_dropped",
                session_key=sk,
                reason="missing_or_empty_Part",
                entry_type=entry.get("type"),
                media_keys=sorted(first_media.keys())[:20],
                title=entry.get("title") or entry.get("grandparentTitle"),
            )
            return None
        first_part = parts[0]
        if not isinstance(first_part, dict):
            log.warning(
                "plex.live.session_dropped",
                session_key=sk,
                reason="Part[0]_not_a_dict",
                part_0_type=type(first_part).__name__,
            )
            return None

        source_path = first_part.get("file")
        if not source_path or not isinstance(source_path, str):
            log.warning(
                "plex.live.session_dropped",
                session_key=sk,
                reason="missing_Part.file",
                entry_type=entry.get("type"),
                part_keys=sorted(first_part.keys())[:20],
                title=entry.get("title") or entry.get("grandparentTitle"),
                detail=(
                    "Plex returned a session without a real file "
                    "path. Live TV / synthetic streams / Plex News "
                    "and similar surfaces don't carry a Part.file "
                    "so they can't be path-mapped to a library "
                    "file. Skip is correct, log is informational."
                ),
            )
            return None

        # Decision: presence of TranscodeSession with
        # ``videoDecision == "transcode"`` means full transcode;
        # ``"copy"`` means direct-stream (remux); absent means
        # direct-play.
        transcode_raw = entry.get("TranscodeSession")
        transcode = transcode_raw if isinstance(transcode_raw, dict) else {}
        video_decision = transcode.get("videoDecision")
        if video_decision == "transcode":
            decision = "transcode"
        elif video_decision == "copy":
            decision = "direct_stream"
        else:
            decision = "direct_play"

        # Started: Plex doesn't surface a session-start
        # timestamp directly on this endpoint; we use
        # ``addedAt`` as a fallback (rounded to second precision),
        # then ``utcnow()`` if even that's missing. The frontend's
        # "Started Nm ago" copy degrades gracefully when the
        # started_at is approximate.
        started_raw = entry.get("addedAt") or entry.get("lastViewedAt")
        if started_raw is not None:
            try:
                started_at = _dt.datetime.fromtimestamp(
                    int(started_raw), tz=_dt.UTC
                )
            except (ValueError, OSError, TypeError):
                started_at = _dt.datetime.now(_dt.UTC)
        else:
            started_at = _dt.datetime.now(_dt.UTC)

        player_raw = entry.get("Player")
        player = player_raw if isinstance(player_raw, dict) else {}
        user_raw = entry.get("User")
        user = user_raw if isinstance(user_raw, dict) else {}

        # Progress = viewOffset / duration. Both are in ms. We
        # clamp to [0, 100] and round to one decimal.
        view_offset = _safe_int(entry.get("viewOffset"))
        duration_ms = _safe_int(first_media.get("duration")) or _safe_int(
            entry.get("duration")
        )
        progress_pct: float | None
        if view_offset is not None and duration_ms and duration_ms > 0:
            progress_pct = max(0.0, min(100.0, round(
                view_offset / duration_ms * 100, 1
            )))
        else:
            progress_pct = None

        # v1.7.3: coerce every potentially-non-str user/device
        # field to str-or-None so pydantic doesn't reject the
        # DTO. Plex's User.title is usually a string but managed-
        # user / home-user records have been seen returning
        # dicts on some PMS builds. The aggregator validates
        # the DTO via pydantic and a single failure there would
        # 500 the entire request.
        def _str_or_none(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                return value or None
            try:
                return str(value)
            except Exception:  # noqa: BLE001
                return None

        dto = LivePlaybackDTO(
            upstream_id=str(session_key),
            source_path=source_path,
            decision=decision,
            started_at=started_at,
            state=_str_or_none(player.get("state")) or "playing",
            progress_pct=progress_pct,
            user=_str_or_none(user.get("title")),
            device_kind=_str_or_none(
                player.get("device") or player.get("product")
            ),
            device_name=_str_or_none(player.get("title")),
            source_codec=_str_or_none(first_media.get("videoCodec")),
            source_bitrate_kbps=_safe_int(first_media.get("bitrate")),
            source_width=_safe_int(first_media.get("width")),
            source_height=_safe_int(first_media.get("height")),
            source_container=_str_or_none(first_media.get("container")),
            target_codec=_str_or_none(transcode.get("videoCodec")),
            target_bitrate_kbps=_safe_int(transcode.get("bitrate")),
            title=_str_or_none(
                entry.get("title") or entry.get("grandparentTitle")
            ),
        )
        return dto
    except Exception as exc:  # noqa: BLE001
        # Final safety net. Should now be unreachable because
        # every known-bad shape is caught explicitly above, but
        # we keep it so a single malformed entry still doesn't
        # blank the whole tile. Log loudly so we know when the
        # net catches something — that signals a shape we
        # haven't handled yet.
        log.error(
            "plex.live.session_dropped",
            session_key=sk,
            reason="unexpected_exception",
            error=str(exc),
            error_type=type(exc).__name__,
            entry_type=entry.get("type"),
            title=entry.get("title") or entry.get("grandparentTitle"),
            detail=(
                "An unexpected exception fell through to the "
                "outer safety net. This is a parser bug — file "
                "an issue with the session entry shape."
            ),
        )
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ── v1.9 Stage 6.1 — diagnostics helpers ─────────────────────────


async def _run_diag_probe(
    client: "httpx.AsyncClient", path: str
) -> dict[str, object]:
    """Run one diagnostic probe. Each probe is GET + timing +
    HTTP-status interpretation. Failures map to
    ``{ok: False, detail: str, latency_ms: int}`` so the
    operator sees the slow probe along with the failing one.

    The probe is "ok" on any 2xx OR 3xx. We don't treat redirects
    as failures because some Plex servers behind reverse proxies
    redirect ``/`` to ``/web/index.html``; Plex still works.
    ``/activities`` returning 401/403 is treated as ``ok`` with
    a detail line — some Plex servers gate it behind permissions
    that don't affect Auditarr's core functions.
    """
    import time

    start = time.perf_counter()
    try:
        response = await client.get(path)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if 200 <= response.status_code < 400:
            return {
                "ok": True,
                "detail": f"HTTP {response.status_code}",
                "latency_ms": latency_ms,
            }
        if path == "/activities" and response.status_code in (401, 403):
            return {
                "ok": True,
                "detail": (
                    f"HTTP {response.status_code} — activities "
                    "endpoint is gated; doesn't affect core "
                    "functions"
                ),
                "latency_ms": latency_ms,
            }
        return {
            "ok": False,
            "detail": f"HTTP {response.status_code}",
            "latency_ms": latency_ms,
        }
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": False,
            "detail": f"HTTP error: {exc}",
            "latency_ms": latency_ms,
        }


def _parse_synthetic_job_id(upstream_job_id: str) -> str | None:
    """Parse Stage 07's synthetic upstream_job_id format
    ``plex:<ratingKey>:<target>``. Returns the rating key or
    None if the string doesn't match. The verify helpers above
    use this; keeping it module-level so tests can exercise it
    without instantiating a provider."""
    if not upstream_job_id.startswith("plex:"):
        return None
    parts = upstream_job_id.split(":", 2)
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1]


def register(context: PluginContext) -> Plugin:
    log = context.logger()
    provider = PlexProvider(log=log)
    context.register_integration(provider)
    return Plugin(context)
