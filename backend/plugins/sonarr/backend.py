"""Sonarr integration plugin.

Targets Sonarr v3 and v4. Both expose the same paths under ``/api/v3/``:
* ``GET /api/v3/system/status`` — healthcheck
* ``GET /api/v3/rootfolder``    — root folders (Auditarr library candidates)
* ``GET /api/v3/series``        — every series with its on-disk path + tags
* ``GET /api/v3/tag``           — tag id → label lookup

Tag mirroring resolves Sonarr series tags down to per-file tags (one
:class:`TagSync` row per file under each tagged series).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.http import async_client

from app.integrations.path_mapping import (
    TAG_ALLOWLIST_SCHEMA_FRAGMENT,
    TAG_DENYLIST_SCHEMA_FRAGMENT,
)
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    SearchTriggerResult,
    TagSync,
)
from app.plugins import Plugin, PluginContext


class SonarrProvider(IntegrationProvider):
    kind = "sonarr"
    label = "Sonarr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://sonarr.local:8989",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify TLS", "default": True},
            "timeout_seconds": {
                "type": "integer",
                "title": "Timeout (s)",
                "default": 15,
                "minimum": 1,
                "maximum": 120,
            },
            "sync_tags_per_file": {
                "type": "boolean",
                "title": "Mirror tags per file",
                "default": True,
                "description": "When enabled, every file under a tagged series gets a TagSync entry.",
            },
            # v1.9 Stage 7.2 — tag allowlist/denylist. Filters
            # apply BEFORE writes to media_tags; flipping a tag
            # from allowed → denied + running a sync removes the
            # MediaTag rows on the next reconcile pass.
            "tag_allowlist": TAG_ALLOWLIST_SCHEMA_FRAGMENT,
            "tag_denylist": TAG_DENYLIST_SCHEMA_FRAGMENT,
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
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log: Any) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Sonarr integration is missing 'base_url'")
        api_key = str(config.secrets.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("Sonarr integration is missing 'api_key'")
        return async_client(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 15)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/api/v3/system/status")
                if response.status_code == 401:
                    return HealthReport(status="error", detail="API key rejected (401)")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(status="error", detail=f"HTTP error: {exc}")
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        return HealthReport(
            status="ok",
            detail=payload.get("instanceName") or "Sonarr",
            metadata={
                "version": payload.get("version"),
                "branch": payload.get("branch"),
                "app_data_folder": payload.get("appData"),
            },
        )

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        async with self._client(config) as client:
            response = await client.get("/api/v3/rootfolder")
            response.raise_for_status()
            folders = response.json()
        return [
            DiscoveredLibrary(
                upstream_id=str(folder.get("id") or ""),
                name=str(folder.get("path") or "Sonarr root").rstrip("/").rsplit("/", 1)[-1]
                or "Sonarr root",
                kind="tv",
                root_path=str(folder.get("path") or "") or None,
                metadata={
                    "accessible": folder.get("accessible"),
                    "free_space": folder.get("freeSpace"),
                },
            )
            for folder in folders
        ]

    async def sync_tags(self, config: IntegrationConfig) -> list[TagSync]:
        if not bool(config.options.get("sync_tags_per_file", True)):
            return []
        async with self._client(config) as client:
            tags_response, series_response = await _gather_two(
                client.get("/api/v3/tag"),
                client.get("/api/v3/series"),
            )
            tags_response.raise_for_status()
            series_response.raise_for_status()
            tag_index = {t["id"]: str(t.get("label") or "") for t in tags_response.json()}
            series_list = series_response.json()

        return _tags_for_arr_series(series_list, tag_index)

    # ── v1.9 Stage 5.1 — search trigger ─────────────────────────
    async def trigger_search(
        self,
        config: IntegrationConfig,
        media_file_path: str,
    ) -> SearchTriggerResult:
        """Trigger a Sonarr SeriesSearch for the series owning
        ``media_file_path``.

        Resolution: GET /api/v3/series, then pick the entry whose
        ``path`` is a prefix of ``media_file_path``. If multiple
        candidates match (nested libraries), the LONGEST prefix
        wins — that's the most specific path and the right series.
        If nothing matches, return ``status="not_found"``.

        On match: POST /api/v3/command { "name": "SeriesSearch",
        "seriesId": <id> }. The command is fire-and-forget on
        Sonarr's side; we record submission, not completion.
        """
        try:
            async with self._client(config) as client:
                series_response = await client.get("/api/v3/series")
                series_response.raise_for_status()
                series_list = series_response.json()

                series_id = _find_arr_id_by_path_prefix(
                    series_list, media_file_path
                )
                if series_id is None:
                    return SearchTriggerResult(
                        status="not_found",
                        detail=(
                            f"No Sonarr series path is a prefix of "
                            f"{media_file_path!r}"
                        ),
                    )

                cmd_response = await client.post(
                    "/api/v3/command",
                    json={"name": "SeriesSearch", "seriesId": series_id},
                )
                if cmd_response.status_code >= 400:
                    return SearchTriggerResult(
                        status="error",
                        upstream_id=str(series_id),
                        detail=(
                            f"Sonarr rejected SeriesSearch command "
                            f"(HTTP {cmd_response.status_code})"
                        ),
                    )
                cmd_payload = cmd_response.json() if cmd_response.content else {}
                return SearchTriggerResult(
                    status="submitted",
                    upstream_id=str(series_id),
                    detail="SeriesSearch command queued",
                    metadata={
                        "command_id": cmd_payload.get("id"),
                        "command_name": "SeriesSearch",
                    },
                )
        except httpx.HTTPError as exc:
            return SearchTriggerResult(
                status="error", detail=f"HTTP error: {exc}"
            )
        except ValueError as exc:
            return SearchTriggerResult(status="error", detail=str(exc))


def register(context: PluginContext) -> Plugin:
    context.register_integration(SonarrProvider(log=context.logger()))
    return Plugin(context)


# ── Helpers shared with Radarr (kept inline to preserve plugin isolation) ──
async def _gather_two(a, b):
    import asyncio

    return await asyncio.gather(a, b)


def _tags_for_arr_series(items: list[dict], tag_index: dict[int, str]) -> list[TagSync]:
    """Walk an arr ``series``/``movie`` list and emit a TagSync per file.

    The arr APIs return one object per show/movie with a ``tags`` array of
    tag ids and a ``path`` for the on-disk root of that title. ``episodeFile``
    paths aren't exposed in the list endpoints — we mirror the series-level
    tags onto every file under the series path. The scanner's MediaFile.path
    is an absolute filesystem path; we leave the resolution to the manager,
    which currently records tags at the path prefix and lets the rules
    engine join in the next stage.
    """
    out: list[TagSync] = []
    for item in items:
        title_path = item.get("path")
        if not title_path:
            continue
        tag_ids = item.get("tags") or []
        for tag_id in tag_ids:
            label = tag_index.get(int(tag_id))
            if not label:
                continue
            # ``media_path`` here is the *directory* — the manager will
            # later expand this to every contained file. We avoid hardcoding
            # ``os.sep`` in the contract; the actual file walk happens in
            # the manager / rules engine.
            out.append(
                TagSync(
                    media_path=os.fspath(title_path),
                    tag=label,
                    metadata={"upstream_id": item.get("id")},
                )
            )
    return out


# v1.9 Stage 5.1 — path-prefix resolver shared by Sonarr + Bazarr.
def _find_arr_id_by_path_prefix(
    items: list[dict], media_file_path: str
) -> int | None:
    """Find the arr id whose ``path`` is the longest prefix of
    ``media_file_path``.

    Sonarr / Radarr / Bazarr all expose series / movies with a
    ``path`` field on each entry. Given a file path like
    ``/data/tv/Show/Season 01/episode.mkv``, the matching series
    is the entry whose ``path`` is ``/data/tv/Show``. We anchor
    the prefix match on a directory boundary so
    ``/data/tv/Show A`` doesn't accidentally match
    ``/data/tv/Show Anniversary`` (same anchoring rule
    ``tag_sync.py`` uses).

    Returns the upstream integer ``id`` or ``None`` if nothing
    matches. When multiple entries' paths match (nested
    libraries), the longest one wins — the most specific match
    is the right answer.
    """
    target = os.fspath(media_file_path)
    best_id: int | None = None
    best_len = -1
    for entry in items:
        ep = entry.get("path")
        eid = entry.get("id")
        if not ep or eid is None:
            continue
        prefix = os.fspath(ep).rstrip("/") + "/"
        if target.startswith(prefix) or target == os.fspath(ep).rstrip("/"):
            plen = len(prefix)
            if plen > best_len:
                best_len = plen
                best_id = int(eid)
    return best_id
