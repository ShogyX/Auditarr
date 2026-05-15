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

from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
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
        return httpx.AsyncClient(
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
