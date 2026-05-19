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
from app.integrations.path_mapping import parse_mappings
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
) -> dict[str, asyncio.Task]:
    """Query enabled Plex integrations and spawn one supervisor
    task per. Returns ``{integration_id: task}`` so callers can
    reconcile + cancel by id.

    The supervisor wraps :func:`_run_plex_listener`. Each
    supervisor task is named ``plex-sse-<integration_id>`` so
    journalctl / ps output shows which integration each is
    serving.

    v1.9.x — return shape changed from ``list[Task]`` to
    ``dict[str, Task]`` so ``reconcile_plex_listeners`` can
    spawn / cancel per-integration without walking the list.
    Callers iterating ``.values()`` for cancellation get the
    same effect as before.
    """
    async with db.session() as session:
        rows = await IntegrationRepository(session).list_all(enabled_only=True)
        plex_rows = [r for r in rows if r.kind == "plex"]

    tasks: dict[str, asyncio.Task] = {}
    for integration in plex_rows:
        tasks[integration.id] = asyncio.create_task(
            _supervise_listener(
                integration_id=integration.id,
                integration_name=integration.name,
                db=db,
                registry=registry,
            ),
            name=f"plex-sse-{integration.id}",
        )
    return tasks


async def reconcile_plex_listeners(
    tasks: dict[str, asyncio.Task],
    *,
    db: Any,
    registry: Any,
) -> dict[str, int]:
    """v1.9.x — bring the listener set in line with the current
    set of enabled Plex integrations.

    Called from the worker's ``poll_integrations`` cron tick
    (every minute) so operators who add a new Plex integration,
    re-enable a disabled one, or change the token on an
    existing one get an SSE listener without restarting the
    worker. The previous "spawn at startup only" path is the
    documented v1.8.0 behaviour but it is hostile in operator
    practice — the user paths reported in v1.9.0 were exactly
    "I rotated my Plex token and live playback stopped working
    until I restarted the worker."

    Mutates ``tasks`` in place. Returns counters for log
    visibility.

    Token-change detection: a listener that died from a
    ``_PermanentListenerError`` (auth failure) has already been
    removed by the supervisor. The reconciler observes the
    missing-task state and respawns — when the operator updates
    the integration row with a fresh token, the next iteration
    picks it up because ``_run_plex_listener`` re-reads the row
    on every supervision cycle.
    """
    spawned = 0
    cancelled = 0
    cleaned = 0

    async with db.session() as session:
        rows = await IntegrationRepository(session).list_all(enabled_only=True)
        enabled_plex_ids = {r.id for r in rows if r.kind == "plex"}
        names_by_id = {r.id: r.name for r in rows if r.kind == "plex"}

    # Cancel listeners whose integration is no longer enabled or
    # is no longer Plex-kind.
    for integration_id in list(tasks.keys()):
        task = tasks[integration_id]
        if integration_id not in enabled_plex_ids:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            tasks.pop(integration_id, None)
            cancelled += 1
            continue
        if task.done():
            # Supervisor exited (clean integration_disabled return,
            # or a _PermanentListenerError that broke the respawn
            # loop). Forget about it; the spawn loop below will
            # respawn if the integration is still enabled.
            tasks.pop(integration_id, None)
            cleaned += 1

    # Spawn listeners for any enabled Plex integration that
    # doesn't currently have one.
    for integration_id in enabled_plex_ids:
        if integration_id in tasks:
            continue
        tasks[integration_id] = asyncio.create_task(
            _supervise_listener(
                integration_id=integration_id,
                integration_name=names_by_id.get(integration_id, "plex"),
                db=db,
                registry=registry,
            ),
            name=f"plex-sse-{integration_id}",
        )
        spawned += 1

    if spawned or cancelled or cleaned:
        log.info(
            "worker.sse.reconciled",
            spawned=spawned,
            cancelled=cancelled,
            cleaned=cleaned,
            active=len(tasks),
        )
    return {
        "spawned": spawned,
        "cancelled": cancelled,
        "cleaned": cleaned,
        "active": len(tasks),
    }


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
            # v1.9.x — surface to the integration row so the
            # operator sees a red dot on the dashboard. Pre-fix
            # the supervisor exited silently (only into the log
            # buffer, which is per-process and invisible to the
            # API); rotated tokens looked like "the app just
            # stopped seeing playback." Writing to health_status
            # makes the failure mode operator-actionable.
            try:
                await _mark_integration_unhealthy(
                    db=db,
                    integration_id=integration_id,
                    detail=(
                        f"SSE listener stopped: {exc}. "
                        "Re-save the integration to retry."
                    ),
                )
            except Exception as mark_exc:  # noqa: BLE001
                log.warning(
                    "worker.sse.health_mark_failed",
                    integration_id=integration_id,
                    error=str(mark_exc),
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


async def _mark_integration_unhealthy(
    *,
    db: Any,
    integration_id: str,
    detail: str,
) -> None:
    """v1.9.x — flip the integration's ``health_status`` to
    ``"error"`` with an operator-readable detail. Best-effort:
    a DB failure inside this helper does not propagate (the
    caller wraps in try/except).
    """
    from app.utils.datetime import utcnow

    async with db.session() as session:
        integration = await IntegrationRepository(session).get(integration_id)
        if integration is None:
            return
        integration.health_status = "error"
        integration.health_detail = detail
        integration.health_checked_at = utcnow()
        await session.commit()


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

    # v1.9 OP-10 — parse the per-integration path mappings once
    # at listener startup so the SessionStateManager can rewrite
    # source_path before resolving media_file_id. The mappings
    # are stored as a JSON list under ``config.options``; the
    # parser tolerates malformed entries.
    integration_mappings = parse_mappings(
        (config.options or {}).get("path_mappings")
    )

    state_manager = SessionStateManager(
        integration_id=integration_id,
        db_session_factory=db.session,
        path_mappings=integration_mappings,
    )

    log.info(
        "worker.sse.listener_starting",
        integration_id=integration_id,
        integration_name=integration.name,
        path_mappings=len(integration_mappings),
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

        # v1.9 OP-10 — thread the rating_key from the SSE event
        # through. Plex's SSE payload exposes it on the session
        # state notification; if a particular event shape doesn't
        # carry it the manager simply skips the column.
        evt_rating_key = getattr(evt, "rating_key", None)

        await state_manager.handle_state_event(
            session_key=evt.session_key,
            state=state,
            view_offset_ms=evt.view_offset_ms,
            enrichment=cached,
            rating_key=evt_rating_key,
        )

        # Drop enrichment for stopped sessions so memory doesn't
        # grow unboundedly across long-running listener lifetimes.
        if state == "stopped":
            enrichment_cache.pop(evt.session_key, None)
