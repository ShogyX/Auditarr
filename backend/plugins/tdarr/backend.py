"""Tdarr integration plugin.

Tdarr's Node/Server API lives at ``/api/v2/`` and is the same one the
official Tdarr UI uses. It does not require an API key by default; later
builds added optional bearer auth which we honor via the ``token`` secret
field (omit it on installs that don't require it).

What ships in this version:
* ``healthcheck`` — ``GET /api/v2/status`` returns the Tdarr build and a
  list of attached nodes. ``status="ok"`` requires the response to parse;
  if at least one node is offline we downgrade to ``degraded``.
* ``discover_libraries`` — ``GET /api/v2/cruddb`` with ``collection=LibrarySettingsJSONDB``
  enumerates configured Tdarr libraries (the on-disk roots Tdarr watches).
* ``sync_tags`` — Tdarr's file index is large and stream-oriented; mirroring
  per-file status into tags is deferred. Returns ``[]`` for now.
"""

from __future__ import annotations

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


class TdarrProvider(IntegrationProvider):
    kind = "tdarr"
    label = "Tdarr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://tdarr.local:8265",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify TLS", "default": True},
            "timeout_seconds": {
                "type": "integer",
                "title": "Timeout (s)",
                "default": 20,
                "minimum": 1,
                "maximum": 120,
            },
        },
    }
    # Token is optional — empty string is allowed for builds without auth.
    # We declare it so the operator can supply one when needed, but the
    # manager treats empty/missing as "no auth header".
    secret_fields: tuple[str, ...] = ()

    def __init__(self, log: Any) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Tdarr integration is missing 'base_url'")
        headers = {"Accept": "application/json"}
        token = str(config.secrets.get("token", "")).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 20)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers=headers,
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/api/v2/status")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(status="error", detail=f"HTTP error: {exc}")
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        # Tdarr returns either a top-level dict or a list of nodes. Normalize.
        nodes: list[dict[str, Any]] = []
        if isinstance(payload, list):
            nodes = payload
        elif isinstance(payload, dict):
            nodes = payload.get("nodes") or [payload]

        offline = [n for n in nodes if isinstance(n, dict) and n.get("status") == "offline"]
        version = next(
            (n.get("version") for n in nodes if isinstance(n, dict) and n.get("version")),
            None,
        )
        status = "ok" if not offline else "degraded"
        detail = (
            f"{len(nodes) - len(offline)} of {len(nodes)} node(s) online"
            if nodes
            else "Tdarr responded"
        )
        return HealthReport(
            status=status,
            detail=detail,
            metadata={"version": version, "nodes": len(nodes)},
        )

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        async with self._client(config) as client:
            # Tdarr's CRUD endpoint pages collections.
            response = await client.post(
                "/api/v2/cruddb",
                json={
                    "data": {
                        "collection": "LibrarySettingsJSONDB",
                        "mode": "getAll",
                    }
                },
            )
            response.raise_for_status()
            payload = response.json() or []

        out: list[DiscoveredLibrary] = []
        for lib in payload if isinstance(payload, list) else []:
            if not isinstance(lib, dict):
                continue
            root = lib.get("folder")
            if not root:
                continue
            # Tdarr libraries don't carry a kind; treat them as mixed and let
            # the operator pick what to do on promote.
            out.append(
                DiscoveredLibrary(
                    upstream_id=str(lib.get("_id") or lib.get("id") or ""),
                    name=str(lib.get("name") or root.rstrip("/").rsplit("/", 1)[-1]),
                    kind="mixed",
                    root_path=str(root),
                    metadata={
                        "scan_found_count": lib.get("scanFoundCount"),
                        "transcode_queue": lib.get("transcodeQueue"),
                    },
                )
            )
        return out

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []


def register(context: PluginContext) -> Plugin:
    context.register_integration(TdarrProvider(log=context.logger()))
    return Plugin(context)
