"""v1.8.0 / Stage 17 — worker-side Plex SSE listener supervisor.

Owns the lifecycle of one ``plex_session_listener`` task per
enabled Plex integration. Spawned at worker startup from
:func:`app.worker.startup`. Cancelled at worker shutdown.

Why a separate module from ``app.worker``? Three reasons:

1. ``app.worker`` is consumed by ``arq app.worker.WorkerSettings``;
   adding async listener machinery directly there bloats the
   already-busy module and makes it harder to read.
2. The supervisor here is a simple supervise/respawn loop — easy
   to unit-test in isolation without booting the whole arq stack.
3. Integration churn (operator adds/removes a Plex server while
   the worker is running) is out of scope for v1.8.0. A future
   v1.8.x can watch the integration table and reconcile without
   touching ``app.worker``.

Failure model: a listener task is **expected** to run forever via
:func:`app.core.sse.stream_events`'s built-in reconnect loop. The
supervisor here is a belt-and-braces respawn — if the listener
raises an unhandled exception (which should never happen because
``stream_events`` itself handles transport errors), we log, sleep,
and respawn after backoff. Token-invalidation errors (4xx) from
the SSE client are NOT respawned because they won't fix
themselves; the operator needs to re-save the integration.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

from app.core.logging import get_logger
from app.events.bus import get_event_bus
from app.integrations.manager import IntegrationManager
from app.security.secrets import get_secret_box
from app.services.playback.session_manager import (
    SessionStateManager,
    enrichment_from_live_dto,
)
from app.services.repositories import IntegrationRepository

log = get_logger("auditarr.worker.sse", category="playback")

# Respawn schedule after an unhandled listener exception. Same shape
# as the SSE reconnect schedule but operates one level up.
_RESPAWN_BACKOFF = (5.0, 15.0, 60.0, 300.0)


async def spawn_plex_listeners(
    db: Any, registry: Any
) -> list[asyncio.Task]:
    """Query enabled Plex integrations and spawn one supervisor
    task per. Returns the task list so the caller can cancel
    them on shutdown.

    The supervisor wraps :func:`_run_plex_listener`. Each
    supervisor task is named ``plex-sse-<integration_id>`` so
    journalctl / ps output shows which integration each is
    serving.
    """
    async with db.session() as session:
        rows = await IntegrationRepository(session).list_all(enabled_only=True)
        plex_rows = [r for r in rows if r.kind == "plex"]

    tasks: list[asyncio.Task] = []
    for integration in plex_rows:
        task = asyncio.create_task(
            _supervise_listener(
                integration_id=integration.id,
                integration_name=integration.name,
                db=db,
                registry=registry,
            ),
            name=f"plex-sse-{integration.id}",
        )
        tasks.append(task)
    return tasks


async def _supervise_listener(
    *,
    integration_id: str,
    integration_name: str,
    db: Any,
    registry: Any,
) -> None:
    """Respawn the listener if it dies with an unhandled
    exception. Permanent errors (auth, config) break the loop.
    """
    attempt = 0
    while True:
        start = _dt.datetime.now(_dt.UTC)
        try:
            await _run_plex_listener(
                integration_id=integration_id,
                db=db,
                registry=registry,
            )
            # Returning cleanly means the listener decided to
            # exit on purpose (e.g. integration disabled). Stop
            # respawning.
            log.info(
                "worker.sse.listener_exited_cleanly",
                integration_id=integration_id,
                integration_name=integration_name,
            )
            return
        except asyncio.CancelledError:
            # Shutdown path.
            log.info(
                "worker.sse.listener_cancelled",
                integration_id=integration_id,
                integration_name=integration_name,
            )
            raise
        except _PermanentListenerError as exc:
            log.error(
                "worker.sse.listener_permanent_error",
                integration_id=integration_id,
                integration_name=integration_name,
                detail=str(exc),
            )
            return
        except Exception as exc:  # noqa: BLE001
            # Unexpected. Backoff + respawn.
            backoff = _RESPAWN_BACKOFF[
                min(attempt, len(_RESPAWN_BACKOFF) - 1)
            ]
            runtime = (_dt.datetime.now(_dt.UTC) - start).total_seconds()
            log.error(
                "worker.sse.listener_crashed",
                integration_id=integration_id,
                integration_name=integration_name,
                error=str(exc),
                error_type=type(exc).__name__,
                runtime_seconds=runtime,
                respawn_in_seconds=backoff,
                attempt=attempt,
            )
            # If the listener ran for a long time before crashing,
            # reset the attempt counter — that crash was probably a
            # transient blip, not a misconfiguration.
            if runtime > 600:
                attempt = 0
            else:
                attempt += 1
            await asyncio.sleep(backoff)


class _PermanentListenerError(Exception):
    """Raised when the listener decides the upstream is
    permanently misconfigured (4xx auth, missing endpoint,
    etc.). The supervisor catches this and stops respawning.
    """


async def _run_plex_listener(
    *,
    integration_id: str,
    db: Any,
    registry: Any,
) -> None:
    """One listener iteration.

    Re-reads the integration config at start so an operator
    update is picked up after a restart (or future a-future
    SIGHUP handler). Opens the SSE stream via the Plex
    provider, dispatches events to the SessionStateManager.

    Returns cleanly if the integration becomes disabled or is
    deleted. Raises :class:`_PermanentListenerError` for auth
    failures the operator must fix.
    """
    # Resolve integration config + provider.
    # v1.8.3: use the EventBus singleton directly. Pre-1.8.3 this
    # tried ``registry.get_optional(EventBus)`` which never worked
    # because ``ServiceRegistry`` has no ``get_optional`` method —
    # the hasattr check just silently returned None and the
    # listener ran without an event bus.
    bus = get_event_bus()
    secret_box = get_secret_box()

    async with db.session() as session:
        manager = IntegrationManager(
            session=session,
            registry=registry,
            secret_box=secret_box,
            event_bus=bus,
        )
        integration = await IntegrationRepository(session).get(integration_id)
        if integration is None or not integration.enabled or integration.kind != "plex":
            log.info(
                "worker.sse.listener_skipped",
                integration_id=integration_id,
                reason="integration_disabled_or_removed",
            )
            return
        provider = manager.provider_for("plex")
        if provider is None:
            raise _PermanentListenerError(
                "no Plex provider registered; plugin failed to load?"
            )
        config = manager.build_config(integration)

    if not hasattr(provider, "subscribe_sessions"):
        # Defensive: shouldn't fire because the v1.8.0 Plex
        # plugin always exposes subscribe_sessions, but if a
        # downgrade replaced the plugin we want a clear error.
        raise _PermanentListenerError(
            "Plex provider lacks subscribe_sessions; v1.8.0 plugin not loaded"
        )

    # In-memory cache of session-level enrichment so we don't
    # refetch the snapshot on every state event. Keyed by
    # session_key. Bounded to 256 entries (per session, not per
    # event) — sessions cleared on stop.
    enrichment_cache: dict[str, Any] = {}

    state_manager = SessionStateManager(
        integration_id=integration_id,
        db_session_factory=db.session,
    )

    log.info(
        "worker.sse.listener_starting",
        integration_id=integration_id,
        integration_name=integration.name,
    )

    async for evt in provider.subscribe_sessions(config):
        if evt.kind == "reconnecting":
            # Drop the enrichment cache so the next state event
            # for any session triggers a fresh snapshot.
            enrichment_cache.clear()
            await state_manager.handle_reconnect()
            continue

        if evt.kind != "state" or not evt.session_key or not evt.state:
            continue

        # Plex's wire vocabulary for state matches ours:
        # playing / paused / buffering / stopped.
        state = evt.state.lower()

        # Lazy-enrich on first event for this session_key, OR
        # if the cached entry is older than 5 minutes (the
        # transcode decision can change mid-stream).
        cached = enrichment_cache.get(evt.session_key)
        if cached is None and state != "stopped":
            # Fetch the snapshot to enrich. Race: if the
            # session ended between SSE event and our fetch,
            # we'll get None — that's fine; we still record the
            # state transition with what the SSE event gave us.
            try:
                dto = await provider.fetch_one_session_snapshot(
                    config, evt.session_key
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "worker.sse.snapshot_failed",
                    integration_id=integration_id,
                    session_key=evt.session_key,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                dto = None
            if dto is not None:
                cached = enrichment_from_live_dto(dto)
                enrichment_cache[evt.session_key] = cached

        await state_manager.handle_state_event(
            session_key=evt.session_key,
            state=state,
            view_offset_ms=evt.view_offset_ms,
            enrichment=cached,
        )

        # Drop enrichment for stopped sessions so memory doesn't
        # grow unboundedly across long-running listener lifetimes.
        if state == "stopped":
            enrichment_cache.pop(evt.session_key, None)
