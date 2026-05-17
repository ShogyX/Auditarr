"""Radarr integration plugin.

Targets Radarr v3+. The API surface is the same shape as Sonarr v3 but the
collection endpoint is ``/api/v3/movie`` instead of ``/api/v3/series``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.http import async_client

from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)
from app.plugins import Plugin, PluginContext


class RadarrProvider(IntegrationProvider):
    kind = "radarr"
    label = "Radarr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://radarr.local:7878",
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
            },
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
            raise ValueError("Radarr integration is missing 'base_url'")
        api_key = str(config.secrets.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("Radarr integration is missing 'api_key'")
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
            detail=payload.get("instanceName") or "Radarr",
            metadata={
                "version": payload.get("version"),
                "branch": payload.get("branch"),
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
                name=str(folder.get("path") or "Radarr root").rstrip("/").rsplit("/", 1)[-1]
                or "Radarr root",
                kind="movies",
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
        import asyncio

        async with self._client(config) as client:
            tags_response, movies_response = await asyncio.gather(
                client.get("/api/v3/tag"),
                client.get("/api/v3/movie"),
            )
            tags_response.raise_for_status()
            movies_response.raise_for_status()
            tag_index = {
                t["id"]: str(t.get("label") or "") for t in tags_response.json()
            }
            movies = movies_response.json()

        out: list[TagSync] = []
        for movie in movies:
            title_path = movie.get("path")
            if not title_path:
                continue
            for tag_id in movie.get("tags") or []:
                label = tag_index.get(int(tag_id))
                if not label:
                    continue
                out.append(
                    TagSync(
                        media_path=os.fspath(title_path),
                        tag=label,
                        metadata={"upstream_id": movie.get("id")},
                    )
                )
        return out


def register(context: PluginContext) -> Plugin:
    context.register_integration(RadarrProvider(log=context.logger()))
    return Plugin(context)
