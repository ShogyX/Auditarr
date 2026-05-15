"""Webhook event dispatcher (Stage 19 audit follow-up).

Maps per-service webhook payloads to repository actions:

  Sonarr  Download / Rename        → reprobe + hash
  Sonarr  EpisodeFileDelete        → remove
  Radarr  Download / Rename        → reprobe + hash
  Radarr  MovieFileDelete          → remove
  Plex    library.new              → reprobe + hash
  Plex    *                        → ignored
  Jellyfin ItemAdded / ItemUpdated → reprobe + hash
  Jellyfin ItemRemoved             → remove

Any unrecognized event is logged at INFO and 200 OK is returned —
upstreams retry on non-2xx, and we don't want to cause a retry
storm because Sonarr fired ``Test`` or some new event type we
haven't mapped yet.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.path_mapping import parse_mappings, remap_path
from app.models.integration import Integration

log = get_logger("auditarr.webhooks.dispatch", category="webhooks")


@dataclass(slots=True)
class WebhookOutcome:
    """What the dispatcher actually did, surfaced for testing and
    operator-visible response bodies."""

    kind: str
    event: str
    action: str  # "reprobe" | "remove" | "ignored"
    paths: list[str]
    detail: str = ""


# ── Event extraction ────────────────────────────────────────────
def _extract_sonarr(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Return ``(event_type, upstream_paths)`` for a Sonarr payload."""
    event_type = str(payload.get("eventType") or "").strip()
    paths: list[str] = []
    # Download / Rename: episodeFile + episodeFiles arrays.
    ef = payload.get("episodeFile")
    if isinstance(ef, dict) and ef.get("path"):
        paths.append(str(ef["path"]))
    for entry in payload.get("episodeFiles") or []:
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(str(entry["path"]))
    return event_type, [p for p in paths if p]


def _extract_radarr(payload: dict[str, Any]) -> tuple[str, list[str]]:
    event_type = str(payload.get("eventType") or "").strip()
    paths: list[str] = []
    mf = payload.get("movieFile")
    if isinstance(mf, dict) and mf.get("path"):
        paths.append(str(mf["path"]))
    return event_type, [p for p in paths if p]


def _extract_plex(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Plex's payload shape is ``{event: "library.new", Metadata: {...}}``.
    For ``library.new`` events the new item lives in ``Metadata`` —
    look for ``Media[].Part[].file`` (Plex's path field)."""
    event_type = str(payload.get("event") or "").strip()
    paths: list[str] = []
    metadata = payload.get("Metadata") or {}
    for media_entry in metadata.get("Media") or []:
        for part in (media_entry or {}).get("Part") or []:
            if isinstance(part, dict) and part.get("file"):
                paths.append(str(part["file"]))
    return event_type, [p for p in paths if p]


def _extract_jellyfin(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Jellyfin's plugin sends ``NotificationType: "ItemAdded"`` /
    ``"ItemRemoved"`` etc., with the item under ``Item``."""
    event_type = str(payload.get("NotificationType") or "").strip()
    paths: list[str] = []
    item = payload.get("Item") or {}
    if isinstance(item, dict) and item.get("Path"):
        paths.append(str(item["Path"]))
    return event_type, [p for p in paths if p]


_EXTRACTORS = {
    "sonarr": _extract_sonarr,
    "radarr": _extract_radarr,
    "plex": _extract_plex,
    "jellyfin": _extract_jellyfin,
}

# Event → action lookup. Anything not here defaults to "ignored".
_ACTIONS: dict[tuple[str, str], str] = {
    ("sonarr", "Download"): "reprobe",
    ("sonarr", "Rename"): "reprobe",
    ("sonarr", "EpisodeFileDelete"): "remove",
    ("radarr", "Download"): "reprobe",
    ("radarr", "Rename"): "reprobe",
    ("radarr", "MovieFileDelete"): "remove",
    ("plex", "library.new"): "reprobe",
    ("jellyfin", "ItemAdded"): "reprobe",
    ("jellyfin", "ItemUpdated"): "reprobe",
    ("jellyfin", "ItemRemoved"): "remove",
}


async def dispatch(
    *,
    kind: str,
    payload: dict[str, Any],
    integration: Integration,
    session: AsyncSession,
    ctx: dict[str, Any],
) -> WebhookOutcome:
    """Decode a webhook payload + dispatch the resulting action.

    ``ctx`` is the per-request context dict; the dispatcher pulls
    the scanner / repository factories from it so unit tests can
    inject stubs.
    """
    extractor = _EXTRACTORS.get(kind)
    if extractor is None:
        log.warning("webhook.unknown_kind", kind=kind)
        return WebhookOutcome(
            kind=kind, event="", action="ignored",
            paths=[], detail=f"unknown kind {kind!r}",
        )

    event_type, upstream_paths = extractor(payload)
    action = _ACTIONS.get((kind, event_type), "ignored")
    if action == "ignored" or not upstream_paths:
        log.info(
            "webhook.ignored",
            kind=kind, event_type=event_type, paths=len(upstream_paths),
        )
        return WebhookOutcome(
            kind=kind, event=event_type, action="ignored",
            paths=upstream_paths,
        )

    # Translate upstream paths through the integration's mappings.
    mappings = parse_mappings(
        (integration.config or {}).get("path_mappings")
    )
    local_paths = [remap_path(p, mappings) for p in upstream_paths]

    # Action handlers run via injectable callables so tests can
    # observe what was called without touching real scanner / repo.
    if action == "reprobe":
        handler = ctx.get("reprobe_path") or _noop_reprobe
        for p in local_paths:
            await handler(session=session, path=p, ctx=ctx)
    elif action == "remove":
        handler = ctx.get("remove_path") or _noop_remove
        for p in local_paths:
            await handler(session=session, path=p, ctx=ctx)

    return WebhookOutcome(
        kind=kind, event=event_type, action=action, paths=local_paths,
    )


# ── Default handlers ───────────────────────────────────────────
async def _noop_reprobe(*, session, path: str, ctx: dict[str, Any]) -> None:
    """Default ``reprobe`` handler.

    Looks up the file by path; if present, schedules a reprobe via
    the existing :class:`Scanner.reprobe_one`. If absent (truly
    new file), no-ops — a full library scan will pick it up. We do
    NOT walk the filesystem from here; the scanner is the right
    place for that and webhook fan-in shouldn't reimplement it.

    Hashing is done in a background task using
    :func:`app.services.file_hash.compute_sha256` so the webhook
    handler returns promptly even for huge files. The result is
    written via a fresh session — the request's session is closed
    by the time the task runs.
    """
    from app.services.media.scanner import Scanner
    from app.services.repositories.media import MediaRepository

    repo = MediaRepository(session)
    media_file = await repo.get_by_path(path)
    if media_file is None:
        log.info("webhook.reprobe_unknown_path", path=path)
        return

    scanner = Scanner(
        session=session,
        event_bus=ctx["bus"],
        ffprobe=ctx.get("ffprobe"),
        registry=ctx.get("registry"),
    )
    await scanner.reprobe_one(media_file)

    # Hashing + VT lookup are deferred — the actual work is plugged
    # in by the route handler so the dispatcher stays storage-clean.
    on_reprobed = ctx.get("on_reprobed")
    if on_reprobed is not None:
        # Fire-and-forget. We deliberately do NOT await — the goal
        # is to keep the webhook response fast.
        asyncio.create_task(on_reprobed(media_file_id=media_file.id))


async def _noop_remove(*, session, path: str, ctx: dict[str, Any]) -> None:
    """Default ``remove`` handler: mark the file orphaned + emit
    ``media.removed``. We don't hard-delete; that's a destructive
    operation reserved for the housekeeping job or explicit operator
    action. ``is_orphaned=True`` is the same shape a scan would
    apply if it noticed the file missing.
    """
    from app.services.repositories.media import MediaRepository

    repo = MediaRepository(session)
    media_file = await repo.get_by_path(path)
    if media_file is None:
        log.info("webhook.remove_unknown_path", path=path)
        return
    media_file.is_orphaned = True
    bus = ctx.get("bus")
    if bus is not None:
        from app.events.types import DomainEvent

        await bus.publish(
            DomainEvent(
                name="media.removed",
                payload={
                    "media_id": media_file.id,
                    "path": path,
                    "source": "webhook",
                },
                source="webhooks",
            )
        )
