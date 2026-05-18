"""v1.9 Stage 7.1 — path-mapping discovery.

Given an integration + the operator's Auditarr libraries, suggest
sensible ``path_mappings`` rows so the operator doesn't have to
hand-type them. The suggestion engine is best-effort: it never
auto-writes anything; it returns ``PathMappingSuggestion`` rows
that the frontend renders as proposed entries the operator
explicitly accepts.

Strategy (per integration kind):

  * Sonarr / Radarr / Bazarr: GET ``/api/v3/rootfolder`` (Bazarr
    uses ``/api/system/settings/general``). The returned
    ``path`` per entry is the upstream-side root the operator
    has configured on their *arr install. We pair each upstream
    root with the Auditarr library whose ``root_path`` has the
    longest common SUFFIX — operators commonly name their
    libraries with the same trailing folder (``/data/movies``
    upstream + ``/mnt/media/Movies`` Auditarr-side both end in
    "Movies"). When no library obviously matches, the upstream
    root is still surfaced as an unmatched suggestion so the
    operator can finish wiring it up.

  * Plex: GET ``/library/sections``. Each Directory entry has
    a list of Locations carrying upstream paths. Same
    longest-suffix matching applies.

  * Jellyfin: GET ``/Library/VirtualFolders``. Each Locations
    list carries upstream paths. Same logic.

  * Other kinds (Tdarr, Tracearr, etc.) return an empty list
    — no library / root-folder concept upstream.

Returned shape:
    [
      {
        "from": "/data/movies",
        "to": "/mnt/media/Movies",
        "source": "auto",
        "confidence": "high" | "medium" | "low" | "none",
        "library_id": "<auditarr lib id or None>",
        "library_name": "<auditarr lib name or None>",
      },
      ...
    ]

The frontend renders this as a "Suggested mappings" preview
above the actual path_mappings editor; operator clicks "Apply"
to merge them in.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.manager import IntegrationManager
from app.models.integration import Integration
from app.models.library import Library

log = get_logger("auditarr.integrations.discovery", category="integrations")


@dataclass(slots=True)
class PathMappingSuggestion:
    from_path: str  # upstream side (what the integration reports)
    to_path: str  # Auditarr side (the matched library root, "" if none)
    confidence: str  # "high" | "medium" | "low" | "none"
    library_id: str | None
    library_name: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Frontend wants ``from`` / ``to`` not ``from_path`` /
        # ``to_path`` to match the editor's column names.
        d["from"] = d.pop("from_path")
        d["to"] = d.pop("to_path")
        return d


async def discover_path_mappings(
    *,
    session: AsyncSession,
    manager: IntegrationManager,
    integration: Integration,
) -> list[PathMappingSuggestion]:
    """Probe the upstream for its root folders + suggest
    mappings to the operator's Auditarr libraries.

    Best-effort: any HTTP / parsing failure returns ``[]``
    rather than raising. The frontend handles an empty list as
    "no suggestions" without surfacing an error — the operator
    can still type entries manually.
    """
    libraries = (
        await session.execute(select(Library).where(Library.enabled.is_(True)))
    ).scalars().all()

    upstream_paths = await _fetch_upstream_paths(manager, integration)
    if not upstream_paths:
        return []

    return [
        _match_to_library(up, libraries) for up in upstream_paths
    ]


async def _fetch_upstream_paths(
    manager: IntegrationManager, integration: Integration
) -> list[str]:
    """Per-kind dispatch. Returns the deduplicated list of paths
    the upstream considers its library roots."""
    kind = integration.kind
    try:
        config = manager.build_config(integration)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "discover_path_mappings.build_config_failed",
            integration_id=integration.id,
            error=str(exc),
        )
        return []

    base_url = str(config.options.get("base_url", "")).rstrip("/")
    if not base_url:
        return []

    try:
        if kind in ("sonarr", "radarr"):
            return await _fetch_arr_root_folders(config, base_url)
        if kind == "bazarr":
            return await _fetch_bazarr_settings(config, base_url)
        if kind == "plex":
            return await _fetch_plex_section_locations(config, base_url)
        if kind == "jellyfin":
            return await _fetch_jellyfin_virtual_folders(config, base_url)
    except httpx.HTTPError as exc:
        log.warning(
            "discover_path_mappings.http_failed",
            integration_id=integration.id,
            kind=kind,
            error=str(exc),
        )
        return []
    # Tracearr / Tdarr / other kinds: no library concept upstream.
    return []


async def _fetch_arr_root_folders(config, base_url: str) -> list[str]:
    """Sonarr + Radarr expose the same endpoint shape:
    ``GET /api/v3/rootfolder`` → ``[{"path": "...", ...}]``."""
    from app.core.http import async_client

    api_key = str(config.secrets.get("api_key", ""))
    headers = {"X-Api-Key": api_key} if api_key else {}
    async with async_client(
        base_url=base_url, headers=headers, timeout=15.0
    ) as client:
        response = await client.get("/api/v3/rootfolder")
        response.raise_for_status()
        payload = response.json() or []
    paths: list[str] = []
    for entry in payload:
        path = entry.get("path")
        if path:
            paths.append(str(path))
    return list(dict.fromkeys(paths))


async def _fetch_bazarr_settings(config, base_url: str) -> list[str]:
    """Bazarr doesn't expose a rootfolder endpoint — but its
    ``/api/series`` + ``/api/movies`` carry per-title paths, and
    we can extract a small set of distinct prefix-roots from
    those. For Bazarr we lean on the existing series/movies
    listing rather than a synthetic one."""
    from app.core.http import async_client

    api_key = str(config.secrets.get("api_key", ""))
    headers = {"X-Api-Key": api_key} if api_key else {}
    paths: set[str] = set()
    async with async_client(
        base_url=base_url, headers=headers, timeout=15.0
    ) as client:
        for endpoint in ("/api/series", "/api/movies"):
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
                payload = (response.json() or {}).get("data") or []
            except httpx.HTTPError:
                continue
            for entry in payload:
                p = entry.get("path")
                if not p:
                    continue
                # Strip one path component to approximate the
                # root that contains a flat list of titles.
                root = os.path.dirname(os.fspath(p).rstrip("/"))
                if root:
                    paths.add(root)
    return sorted(paths)


async def _fetch_plex_section_locations(
    config, base_url: str
) -> list[str]:
    """Plex's library sections endpoint returns
    ``{"MediaContainer": {"Directory": [{..., "Location": [{"path": "..."}]}]}}``.
    """
    from app.core.http import async_client

    token = str(config.secrets.get("token", ""))
    headers = {
        "X-Plex-Token": token,
        "Accept": "application/json",
    } if token else {"Accept": "application/json"}
    async with async_client(
        base_url=base_url, headers=headers, timeout=15.0
    ) as client:
        response = await client.get("/library/sections")
        response.raise_for_status()
        payload = response.json() or {}
    directories = (
        payload.get("MediaContainer", {}).get("Directory", []) or []
    )
    out: list[str] = []
    for d in directories:
        for loc in d.get("Location") or []:
            p = loc.get("path")
            if p:
                out.append(str(p))
    return list(dict.fromkeys(out))


async def _fetch_jellyfin_virtual_folders(
    config, base_url: str
) -> list[str]:
    """Jellyfin's virtual folders endpoint returns
    ``[{..., "Locations": ["/path/a", "/path/b"]}]``."""
    from app.core.http import async_client

    api_key = str(config.secrets.get("api_key", ""))
    headers = {
        "X-Emby-Token": api_key,
        "Accept": "application/json",
    } if api_key else {"Accept": "application/json"}
    async with async_client(
        base_url=base_url, headers=headers, timeout=15.0
    ) as client:
        response = await client.get("/Library/VirtualFolders")
        response.raise_for_status()
        payload = response.json() or []
    out: list[str] = []
    for folder in payload:
        for loc in folder.get("Locations") or []:
            out.append(str(loc))
    return list(dict.fromkeys(out))


def _match_to_library(
    upstream_path: str,
    libraries: list[Library],
) -> PathMappingSuggestion:
    """Pick the best-matching Auditarr library for an upstream
    path.

    Confidence:
      - "high":    exact basename match (last directory token).
      - "medium":  case-insensitive basename match.
      - "low":     fuzzy substring match on basename.
      - "none":    no matching library; returned with to_path=""
                   so the operator can finish wiring it.
    """
    up_basename = os.path.basename(os.fspath(upstream_path).rstrip("/"))
    up_basename_lower = up_basename.lower()

    high: Library | None = None
    medium: Library | None = None
    low: Library | None = None

    for lib in libraries:
        lib_basename = os.path.basename(
            os.fspath(lib.root_path).rstrip("/")
        )
        lib_basename_lower = lib_basename.lower()
        if lib_basename == up_basename:
            high = lib
            break
        if lib_basename_lower == up_basename_lower:
            if medium is None:
                medium = lib
        elif (
            up_basename_lower
            and lib_basename_lower
            and (
                up_basename_lower in lib_basename_lower
                or lib_basename_lower in up_basename_lower
            )
        ):
            if low is None:
                low = lib

    pick = high or medium or low
    if pick is not None:
        confidence = "high" if pick is high else ("medium" if pick is medium else "low")
        return PathMappingSuggestion(
            from_path=str(upstream_path),
            to_path=str(pick.root_path),
            confidence=confidence,
            library_id=pick.id,
            library_name=pick.name,
        )

    return PathMappingSuggestion(
        from_path=str(upstream_path),
        to_path="",
        confidence="none",
        library_id=None,
        library_name=None,
    )


# ── v1.9 Stage 7.1 — webhook whitelist discovery ───────────────


async def discover_webhook_sources(
    *,
    session: AsyncSession,
    integration: Integration,
) -> list[dict[str, Any]]:
    """Surface IP addresses observed in recent webhook deliveries
    for this integration — operators trying to set up a
    whitelist commonly want exactly the IPs that have been
    reaching them lately.

    Reads ``webhook.received`` audit log rows for this
    integration in the last 24 hours, extracts the
    ``source_ip`` field, returns one entry per distinct IP +
    a count.

    Returns ``[]`` when there's no audit log signal.
    """
    import datetime as _dt

    from sqlalchemy import select

    from app.models.audit_log import AuditLogEntry

    cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=24)
    rows = (
        await session.execute(
            select(AuditLogEntry)
            .where(AuditLogEntry.action == "webhook.received")
            .where(AuditLogEntry.occurred_at >= cutoff)
            .where(AuditLogEntry.target_id == integration.id)
        )
    ).scalars().all()

    counter: dict[str, int] = {}
    for row in rows:
        md = row.metadata_ or {}
        ip = md.get("source_ip") or md.get("remote_addr")
        if not ip:
            continue
        ip_str = str(ip)
        counter[ip_str] = counter.get(ip_str, 0) + 1

    return [
        {"ip": ip, "count": count}
        for ip, count in sorted(
            counter.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


__all__ = [
    "PathMappingSuggestion",
    "discover_path_mappings",
    "discover_webhook_sources",
]
