"""Bazarr integration plugin.

Bazarr is the *arr-stack subtitle manager. We use it primarily as a
**signal source**: every series/movie record Bazarr tracks carries a list
of missing subtitle languages. Auditarr mirrors those as ``TagSync`` rows
so the rules engine can flag "missing English subtitles" without us having
to crawl every video file ourselves.

What ships in this version:
* ``healthcheck`` — ``GET /api/system/status`` returns the Bazarr build,
  which doubles as an API-key validity check.
* ``discover_libraries`` — Bazarr doesn't own libraries; it follows Sonarr
  and Radarr. We return ``[]`` rather than synthesizing fake libraries.
* ``sync_tags`` — combines ``/api/series`` and ``/api/movies`` results;
  every title with ``missing_subtitles`` produces tags of the form
  ``missing-subs:<lang>`` keyed by the title's on-disk path.
"""

from __future__ import annotations

import asyncio
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


class BazarrProvider(IntegrationProvider):
    kind = "bazarr"
    label = "Bazarr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://bazarr.local:6767",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify TLS", "default": True},
            "timeout_seconds": {
                "type": "integer",
                "title": "Timeout (s)",
                "default": 15,
                "minimum": 1,
                "maximum": 120,
            },
            "sync_missing_subs": {
                "type": "boolean",
                "title": "Mirror missing-subtitle tags",
                "default": True,
            },
        },
    }
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log: Any) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Bazarr integration is missing 'base_url'")
        api_key = str(config.secrets.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("Bazarr integration is missing 'api_key'")
        return async_client(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 15)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/api/system/status")
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

        # Bazarr wraps results in a top-level ``data`` object on some routes
        # and not others; the status endpoint returns a flat dict.
        data = payload.get("data") if isinstance(payload, dict) else payload
        info = data or payload or {}
        return HealthReport(
            status="ok",
            detail=info.get("instance_name") or "Bazarr",
            metadata={
                "version": info.get("bazarr_version") or info.get("version"),
                "sonarr_connected": info.get("sonarr_signalr_connected"),
                "radarr_connected": info.get("radarr_signalr_connected"),
            },
        )

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        # Bazarr follows Sonarr/Radarr; it doesn't own libraries.
        return []

    async def sync_tags(self, config: IntegrationConfig) -> list[TagSync]:
        if not bool(config.options.get("sync_missing_subs", True)):
            return []
        async with self._client(config) as client:
            series_response, movies_response = await asyncio.gather(
                client.get("/api/series"),
                client.get("/api/movies"),
            )
            series_response.raise_for_status()
            movies_response.raise_for_status()
            series_data = (series_response.json() or {}).get("data") or []
            movies_data = (movies_response.json() or {}).get("data") or []

        out: list[TagSync] = []
        for item in (*series_data, *movies_data):
            path = item.get("path")
            if not path:
                continue
            missing = item.get("missing_subtitles") or []
            for entry in missing:
                # Bazarr emits either a string like "en" or an object with
                # ``code2``/``code3`` depending on settings. Handle both.
                lang = (
                    entry.get("code2") or entry.get("code3") or entry.get("name")
                    if isinstance(entry, dict)
                    else str(entry)
                )
                if not lang:
                    continue
                out.append(
                    TagSync(
                        media_path=os.fspath(path),
                        tag=f"missing-subs:{str(lang).lower()}",
                        metadata={"upstream_id": item.get("id") or item.get("sonarrSeriesId") or item.get("radarrId")},
                    )
                )
        return out


def register(context: PluginContext) -> Plugin:
    context.register_integration(BazarrProvider(log=context.logger()))
    return Plugin(context)
