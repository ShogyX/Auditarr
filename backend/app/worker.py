"""ARQ worker entrypoint.

Runs Auditarr background jobs (currently: library scans + integration
healthchecks) out of the API process so long scans don't tie up an HTTP
worker. Jobs are enqueued through Redis using ARQ's queue and consumed by
``arq app.worker.WorkerSettings``.

Usage::

    uv run arq app.worker.WorkerSettings
    # or inside the container:
    auditarr worker
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.logging import configure_logging, get_logger
from app.core.registry import get_registry
from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.integrations.manager import IntegrationManager
from app.security.secrets import get_secret_box
from app.services.media import Scanner, ScanOptions, get_ffprobe_service
from app.services.playback import PlaybackPoller
from app.services.repositories import IntegrationRepository, LibraryRepository
from app.storage.database import get_database
from app.utils.datetime import utcnow

log = get_logger("auditarr.worker", category="queue")

# Stage 16: which integration kinds support playback telemetry. The
# poller skips others to avoid spurious noise — Sonarr/Radarr/Bazarr
# don't implement ``fetch_playback_events`` and would just return [].
PLAYBACK_KINDS = {"plex", "jellyfin", "tracearr"}


# ── Job functions ────────────────────────────────────────────
async def scan_library(
    ctx: dict[str, Any],
    library_id: str,
    *,
    mode: str = "full",
    follow_symlinks: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run a scan for a single library and return the report as a dict.

    When ``run_id`` is provided (the API enqueue path), the worker
    reuses the pre-created ScanRun row instead of creating a new one,
    so the row the API returned to the caller advances ``queued`` →
    ``running`` → terminal status. Older enqueues without a ``run_id``
    fall back to the legacy "create a new row" behaviour.
    """
    db = ctx["db"]
    bus = ctx["bus"]
    ffprobe = ctx["ffprobe"]

    async with db.session() as session:
        library = await LibraryRepository(session).get(library_id)
        if library is None:
            log.warning("worker.scan_library_missing", library_id=library_id)
            return {"status": "missing", "library_id": library_id}
        run = None
        if run_id is not None:
            from app.services.repositories import ScanRepository

            run = await ScanRepository(session).get(run_id)
            if run is None:
                log.warning(
                    "worker.scan_library_run_missing",
                    library_id=library_id,
                    run_id=run_id,
                )
            elif run.status != "queued":
                # Duplicate or stale enqueue: another worker already
                # advanced this row (or the reaper failed it). Don't
                # run a second scan against the same id.
                log.warning(
                    "worker.scan_library_run_not_queued",
                    library_id=library_id,
                    run_id=run_id,
                    status=run.status,
                )
                return {"status": run.status, "run_id": run.id}
        scanner = Scanner(session=session, event_bus=bus, ffprobe=ffprobe)
        report = await scanner.scan(
            library,
            options=ScanOptions(mode=mode, follow_symlinks=follow_symlinks),
            run=run,
        )
        await session.commit()

    log.info(
        "worker.scan_library_done",
        library_id=library_id,
        status=report.status,
        files_seen=report.files_seen,
    )
    return {
        "status": report.status,
        "run_id": report.run_id,
        "files_seen": report.files_seen,
        "files_added": report.files_added,
        "files_updated": report.files_updated,
        "files_orphaned": report.files_orphaned,
        "probe_failures": report.probe_failures,
        "error": report.error,
    }


async def healthcheck_integration(
    ctx: dict[str, Any], integration_id: str
) -> dict[str, Any]:
    """Run a healthcheck for one integration and persist the result."""
    db = ctx["db"]
    bus = ctx["bus"]
    registry = ctx["registry"]

    async with db.session() as session:
        integration = await IntegrationRepository(session).get(integration_id)
        if integration is None:
            return {"status": "missing", "integration_id": integration_id}
        manager = IntegrationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )
        report = await manager.healthcheck(integration)
        await session.commit()

    return {
        "integration_id": integration_id,
        "status": report.status,
        "detail": report.detail,
    }


async def poll_integrations(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron tick: enqueue healthchecks for any integrations that are due.

    Runs every minute. An integration is "due" when its
    ``poll_interval_seconds`` has elapsed since the last healthcheck (or
    when it has never been checked).
    """
    db = ctx["db"]
    redis = ctx["redis"]

    enqueued: list[str] = []
    skipped = 0
    async with db.session() as session:
        rows = await IntegrationRepository(session).list_all(enabled_only=True)
        now = utcnow()
        for integration in rows:
            if integration.poll_interval_seconds <= 0:
                skipped += 1
                continue
            last = integration.health_checked_at
            if last is not None and (now - last).total_seconds() < integration.poll_interval_seconds:
                skipped += 1
                continue
            await redis.enqueue_job(
                "healthcheck_integration",
                integration.id,
                _job_id=f"hc:{integration.id}:{int(now.timestamp())}",
            )
            enqueued.append(integration.id)

    log.info(
        "worker.poll_integrations",
        enqueued=len(enqueued),
        skipped=skipped,
    )
    return {"enqueued": enqueued, "skipped": skipped}


# ── Stage 16: playback telemetry tick ────────────────────────
async def poll_playback(ctx: dict[str, Any]) -> dict[str, Any]:
    """Poll Plex/Jellyfin for new playback events.

    Runs every 15 minutes by default. Throttled per-integration via
    each integration's ``poll_interval_seconds`` (separate from
    healthcheck cadence — playback polling tolerates longer gaps).

    Implementation: iterate every enabled Plex/Jellyfin integration,
    run ``PlaybackPoller.poll_one``, accumulate stats. Failures for
    one integration don't propagate to others.
    """
    db = ctx["db"]
    registry = ctx["registry"]
    event_bus = ctx["bus"]
    secret_box = get_secret_box()

    results: list[dict[str, Any]] = []
    # We open a fresh session per integration so a commit/rollback in
    # one doesn't propagate to others. The session-scoped
    # ``IntegrationManager`` matches the existing healthcheck pattern.
    async with db.session() as session:
        rows = await IntegrationRepository(session).list_all(enabled_only=True)
        candidates = [r for r in rows if r.kind in PLAYBACK_KINDS]

    for integration in candidates:
        async with db.session() as session:
            manager = IntegrationManager(
                session=session,
                registry=registry,
                secret_box=secret_box,
                event_bus=event_bus,
            )
            poller = PlaybackPoller(
                session=session, manager=manager, event_bus=event_bus
            )
            try:
                outcome = await poller.poll_one(integration)
                results.append(
                    {
                        "integration_id": outcome.integration_id,
                        "fetched": outcome.fetched,
                        "inserted": outcome.inserted,
                        "drift": outcome.drift_suspected,
                        "error": outcome.error,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "worker.poll_playback.failed",
                    integration_id=integration.id,
                    error=str(exc),
                )
                results.append(
                    {"integration_id": integration.id, "error": str(exc)}
                )

    log.info(
        "worker.poll_playback",
        integrations=len(results),
        total_inserted=sum(int(r.get("inserted") or 0) for r in results),
    )
    return {"results": results}


# ── Stage 16 Turn 2: daily analyzer tick ────────────────────
async def analyze_playback(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron: run the playback analyzer and emit/refresh rule
    suggestions. Runs once per day at 03:00 UTC so analyses are
    deterministic across deployments and don't compete with peak
    polling traffic.

    Errors are isolated to this tick — they shouldn't take the worker
    down. The next day's run gets a fresh shot.
    """
    db = ctx["db"]
    from app.services.playback import PlaybackAnalyzer

    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        try:
            outcome = await analyzer.analyze()
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.analyze_playback.failed", error=str(exc))
            return {"error": str(exc)}

    log.info(
        "worker.analyze_playback",
        examined=outcome.examined_events,
        created=outcome.suggestions_created,
        too_few=outcome.skipped_too_few_events,
    )
    return {
        "examined_events": outcome.examined_events,
        "suggestions_created": outcome.suggestions_created,
        "candidates_generated": outcome.candidates_generated,
        "skipped_too_few_events": outcome.skipped_too_few_events,
    }


# ── Lifecycle ────────────────────────────────────────────────
async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings)

    # ── SSL sanity check (v1.7.2) ──────────────────────────
    # See app/main.py for the why. Same check, same non-fatal
    # behaviour: the worker process is where playback polling
    # and integration healthchecks run, so a missing CA bundle
    # hurts the worker most. Probing here makes the
    # misconfiguration loud at boot rather than letting
    # every poll-tick log the same cryptic FileNotFoundError.
    from app.core.ssl_bundle import startup_sanity_check

    startup_sanity_check(fatal=False)

    db = get_database()
    await db.connect()

    # Load integration plugins so providers are registered. The worker
    # needs the same provider registry the API uses.
    from app.plugins.loader import get_plugin_loader

    loader = get_plugin_loader()
    await loader.discover_and_load(app=None)

    # Stage 21: apply persisted runtime overrides before the worker
    # starts processing jobs — otherwise the first scan would run
    # against env defaults even though the operator already lowered
    # the ffprobe timeout via the UI. Then subscribe to the reload
    # channel so subsequent API-side changes propagate here too.
    from app.services.runtime_settings import (
        load_and_apply_overrides,
        reload_listener,
    )

    async with db.session() as session:
        await load_and_apply_overrides(session, settings)

    ctx["settings"] = settings
    ctx["db"] = db
    ctx["bus"] = get_event_bus()
    ctx["ffprobe"] = get_ffprobe_service()
    ctx["registry"] = get_registry()
    # Stash the reload listener task in the ctx so shutdown can
    # cancel it cleanly. ARQ does not provide a hook for this so we
    # roll our own here.
    ctx["settings_reload_task"] = asyncio.create_task(
        reload_listener(settings),
        name="worker-settings-reload-listener",
    )

    # ── v1.8.0 / Stage 17: Plex SSE listener tasks ─────────────
    # Spawn one long-running listener per enabled Plex integration.
    # Each task is supervised: if it dies (e.g. token rotated, Plex
    # disappeared from the network for an extended period), the
    # supervisor logs and respawns after backoff. New / removed
    # integrations between worker restarts are reconciled on the
    # next worker restart — that's an acceptable tradeoff for v1.8.0
    # since integration churn is rare in practice.
    from app.worker_sse import spawn_plex_listeners

    ctx["plex_listener_tasks"] = await spawn_plex_listeners(db, ctx["registry"])
    log.info(
        "worker.plex_listeners_spawned",
        count=len(ctx["plex_listener_tasks"]),
    )

    # ``ctx["redis"]`` is provided by ARQ itself.
    log.info("worker.started")


async def shutdown(ctx: dict[str, Any]) -> None:
    # Cancel the SSE listeners first — they're the longest-running
    # tasks and their reconnect loop will keep the connection alive
    # until we cancel.
    plex_tasks = ctx.get("plex_listener_tasks") or []
    for task in plex_tasks:
        task.cancel()
    for task in plex_tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # Cancel the settings reload listener before tearing down Redis
    # so it doesn't spam the log with "connection lost" exceptions.
    reload_task = ctx.get("settings_reload_task")
    if reload_task is not None:
        reload_task.cancel()
        try:
            await reload_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    db = ctx.get("db")
    if db is not None:
        await db.disconnect()
    log.info("worker.stopped")


async def automation_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron tick: dispatch any schedules whose ``next_run_at`` has passed."""
    db = ctx["db"]
    async with db.session() as session:
        from app.automation.scheduler import Scheduler

        scheduler = Scheduler(session=session, event_bus=ctx["bus"])
        # Build a runtime ctx for in-process runners. Stage 10 will swap
        # heavy jobs over to ARQ enqueueing.
        runner_ctx = {
            "registry": ctx["registry"],
            "bus": ctx["bus"],
            "ffprobe": ctx.get("ffprobe"),
        }
        report = await scheduler.tick(runner_ctx)
    log.info(
        "worker.automation_tick",
        enqueued=len(report.enqueued),
        rescheduled=len(report.rescheduled),
    )
    return {
        "enqueued": report.enqueued,
        "rescheduled": report.rescheduled,
    }


async def optimization_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron tick: run one queued optimization item per minute.

    The worker takes at most one item per tick. ffmpeg transcodes can
    take minutes to hours; serialising them keeps the box from turning
    into a heater and keeps CPU contention predictable. Stage 13 polish
    may add a configurable parallelism cap if real deployments need it.

    Stage 08 (v1.7) — pass an :class:`IntegrationManager` so the
    worker can call ``submit_transcode_job`` on non-in_process
    routing targets. The manager wraps the session, registry,
    secret box, and event bus.
    """
    db = ctx["db"]
    registry = get_registry()
    bus = ctx["bus"]
    async with db.session() as session:
        from app.optimization import OptimizationWorker

        manager = IntegrationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )
        worker = OptimizationWorker(
            session=session,
            event_bus=bus,
            integration_manager=manager,
        )
        report = await worker.run_one()
    log.info(
        "worker.optimization_tick",
        item_id=report.item_id,
        status=report.status,
        detail=report.detail,
    )
    return {
        "item_id": report.item_id,
        "status": report.status,
        "detail": report.detail,
    }


async def routed_transcode_poll_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 08 (v1.7) — poll non-in_process transcode jobs.

    Plan §444. Every 5 minutes, walk every ``OptimizationItem``
    in ``routed`` status and ask its integration provider
    whether the upstream job finished. Items that complete
    upstream advance to ``completed``; items that fail upstream
    advance to ``failed``; items still running stay routed.

    See :mod:`app.optimization.poller` for the per-item logic.
    """
    db = ctx["db"]
    registry = get_registry()
    bus = ctx["bus"]
    async with db.session() as session:
        from app.optimization.poller import poll_routed_transcodes

        manager = IntegrationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=bus,
        )
        report = await poll_routed_transcodes(
            session=session,
            integration_manager=manager,
            event_bus=bus,
        )
    log.info(
        "worker.routed_transcode_poll",
        checked=report.checked,
        completed=report.completed,
        failed=report.failed,
        still_running=report.still_running,
        errored=report.errored,
    )
    return {
        "checked": report.checked,
        "completed": report.completed,
        "failed": report.failed,
        "still_running": report.still_running,
        "errored": report.errored,
    }


async def update_check_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hourly tick: poll the update feed + reconcile any open applies.

    The tick fires every minute but we throttle in-process: only run the
    feed check when ``update_check_interval_minutes`` has elapsed since
    the last check. ``poll_apply_status`` is cheap (one file stat) so
    we run it every tick to keep the UI responsive after an apply.
    """
    settings = ctx["settings"]
    db = ctx["db"]
    async with db.session() as session:
        from app.updater import UpdaterService
        from app.services.repositories.updater import UpdateCheckRepository

        service = UpdaterService(
            session=session, settings=settings, event_bus=ctx["bus"]
        )

        applied = await service.poll_apply_status()

        # Only re-check if enough time has elapsed since the last attempt.
        from app.utils.datetime import utcnow

        last = await UpdateCheckRepository(session).latest()
        should_check = True
        if last is not None:
            elapsed = (utcnow() - last.checked_at).total_seconds()
            should_check = (
                elapsed >= settings.update_check_interval_minutes * 60
            )
        checked = None
        if should_check:
            checked = await service.check_now()

    log.info(
        "worker.update_check_tick",
        checked=bool(checked),
        latest=(checked.latest_version if checked else None),
        applied=(applied.id if applied else None),
    )
    return {
        "checked": bool(checked),
        "latest": checked.latest_version if checked else None,
        "applied": applied.id if applied else None,
    }


async def housekeeping_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily tick: trim old audit rows per the retention windows.

    The tick fires every minute but throttles in-process to once per
    24 hours so we don't hammer the DB. The first run after a worker
    restart always runs immediately so a fresh deployment cleans up its
    backlog without waiting.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    state = ctx.setdefault("_housekeeping_state", {"last_run_at": None})
    from app.utils.datetime import utcnow

    now = utcnow()
    last_run = state["last_run_at"]
    if last_run is not None and (now - last_run).total_seconds() < 24 * 3600:
        return {"skipped": True}

    async with db.session() as session:
        from app.housekeeping import HousekeepingService

        service = HousekeepingService(session=session, settings=settings)
        report = await service.run()
    state["last_run_at"] = now
    log.info(
        "worker.housekeeping_tick",
        total=report.total,
        deliveries=report.notification_deliveries,
        update_checks=report.update_checks,
        rule_evaluations=report.rule_evaluations,
        job_runs=report.job_runs,
    )
    return {
        "total": report.total,
        "deliveries": report.notification_deliveries,
        "update_checks": report.update_checks,
        "rule_evaluations": report.rule_evaluations,
        "job_runs": report.job_runs,
    }


# ── v1.8.1: stale-scan reaper ────────────────────────────────
async def reap_stale_scans(ctx: dict[str, Any]) -> dict[str, Any]:
    """Mark stuck ``queued``/``running`` ScanRun rows as ``failed``.

    Background: if the worker process is killed mid-scan (OOM,
    SIGKILL from systemd, container restart, host reboot), the
    ScanRun row never gets its ``status=running`` → ``failed``
    transition because no exception handler runs. That row then
    blocks every future ``POST /scans/libraries/{id}`` call —
    the API's single-flight check returns 409 "A scan is already
    running" and the operator can't kick off a new scan without
    manually editing the DB.

    This tick runs every 5 minutes and marks any
    ``queued``/``running`` row whose ``started_at`` (or
    ``created_at`` for queued rows that never started) is older
    than 1 hour as ``failed`` with a clear diagnostic message.

    One hour is the right threshold for the auditarr workload:
    even a 100k-file library completes in well under that on
    typical hardware. Scans that legitimately run longer are
    rare; operators with truly massive libraries can override
    via runtime settings (future). False-positive cost is low —
    a wrongly-reaped scan just means the operator clicks
    "Run scan" again, which they would have done anyway.
    """
    from app.utils.datetime import utcnow

    db = ctx["db"]
    bus = ctx["bus"]

    # ``STALE_THRESHOLD_SECONDS`` — 1 hour. Magic number is fine
    # here; making it a runtime setting buys nothing for the
    # near term and we can lift it later if anyone complains.
    STALE_THRESHOLD_SECONDS = 3600

    now = utcnow()
    cutoff = now - timedelta(seconds=STALE_THRESHOLD_SECONDS)

    reaped: list[str] = []
    async with db.session() as session:
        from sqlalchemy import or_, select

        from app.models.library import Library
        from app.models.scan_run import ScanRun

        # Pick the timestamp we measure staleness against: prefer
        # ``started_at`` (when the worker actually started), fall
        # back to ``created_at`` (when the API enqueued the row).
        # Queued-but-never-started rows have started_at=NULL so
        # we need the OR clause.
        result = await session.execute(
            select(ScanRun).where(
                ScanRun.status.in_(("queued", "running")),
                or_(
                    ScanRun.started_at < cutoff,
                    ScanRun.started_at.is_(None) & (ScanRun.created_at < cutoff),
                ),
            )
        )
        stuck = list(result.scalars().all())

        def _aware(ts):
            """SQLite returns naive datetimes for DateTime(timezone=True)
            columns even though Postgres returns aware. Coerce to UTC-aware
            so timedelta arithmetic against ``now`` works in both.
            """
            import datetime as _dt_local

            if ts is None:
                return None
            if ts.tzinfo is None:
                return ts.replace(tzinfo=_dt_local.timezone.utc)
            return ts

        for run in stuck:
            # Mark the row failed. Use a distinctive error message
            # so the operator (and us, debugging) can tell this was
            # the reaper not an actual scan failure.
            previous_status = run.status
            reference_ts = _aware(run.started_at) or _aware(run.created_at)
            if reference_ts is None:
                # Defensive: both timestamps missing. Use threshold +
                # 1s so the age is at least the threshold.
                age_seconds = STALE_THRESHOLD_SECONDS + 1
            else:
                age_seconds = int((now - reference_ts).total_seconds())
            run.status = "failed"
            run.finished_at = now
            run.error = (
                f"Reaped by stale-scan watchdog: row was stuck at "
                f"'{previous_status}' for {age_seconds}s "
                f"(threshold {STALE_THRESHOLD_SECONDS}s). The worker "
                "likely crashed mid-scan; check journalctl for "
                "OOM-killer or SIGKILL events around that time. The "
                "library is now unblocked for new scans."
            )
            # Also refresh the library's last_scan_status so the
            # files page doesn't show a stale "running" indicator.
            library = (
                await session.execute(
                    select(Library).where(Library.id == run.library_id)
                )
            ).scalar_one_or_none()
            if library is not None:
                library.last_scan_status = "failed"

            reaped.append(run.id)

        if stuck:
            await session.commit()
            # Emit one event per reaped run so any WS subscribers
            # (the dashboard, the files page) refresh.
            for run in stuck:
                emit_ref = _aware(run.started_at) or _aware(run.created_at)
                emit_age = (
                    int((now - emit_ref).total_seconds())
                    if emit_ref is not None
                    else STALE_THRESHOLD_SECONDS + 1
                )
                await bus.emit(
                    "scan.reaped",
                    {
                        "run_id": run.id,
                        "library_id": run.library_id,
                        "previous_status": "queued",  # may be running too
                        "age_seconds": emit_age,
                    },
                    source="worker.reap_stale_scans",
                )

    if reaped:
        log.warning(
            "worker.reap_stale_scans.reaped",
            count=len(reaped),
            run_ids=reaped[:10],  # log up to 10; avoid log spam
        )
    return {"reaped": len(reaped), "run_ids": reaped}


# ── ARQ entrypoint ───────────────────────────────────────────
class WorkerSettings:
    """Configuration consumed by ``arq app.worker.WorkerSettings``."""

    functions = [
        scan_library,
        healthcheck_integration,
        poll_integrations,
        poll_playback,
        analyze_playback,
        automation_tick,
        optimization_tick,
        routed_transcode_poll_tick,
        update_check_tick,
        housekeeping_tick,
        reap_stale_scans,
    ]
    cron_jobs = [
        cron(
            poll_integrations,
            name="poll_integrations",
            minute=set(range(60)),  # every minute
            run_at_startup=True,
        ),
        cron(
            poll_playback,
            name="poll_playback",
            # Every 15 minutes. Plex history pages are cheap; Jellyfin
            # session snapshots are even cheaper. Operators wanting
            # finer resolution can run a separate cron via the API.
            minute={0, 15, 30, 45},
            run_at_startup=True,
        ),
        cron(
            analyze_playback,
            name="analyze_playback",
            # Daily at 03:00 UTC. The analyzer's read query is cheap
            # (one bounded SELECT against the indexed playback_events
            # table), so we don't bother spreading it across the hour.
            hour={3},
            minute={0},
            run_at_startup=False,
        ),
        cron(
            automation_tick,
            name="automation_tick",
            minute=set(range(60)),
            run_at_startup=True,
        ),
        cron(
            optimization_tick,
            name="optimization_tick",
            minute=set(range(60)),
            run_at_startup=True,
        ),
        cron(
            routed_transcode_poll_tick,
            name="routed_transcode_poll_tick",
            # Stage 08 (v1.7) plan §444: every 5 minutes. The poll
            # is light — one provider call per routed item — so
            # the bound is set by "how fast should we notice a
            # completed Tdarr job?" rather than load. Five minutes
            # is the documented cadence.
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
        cron(
            update_check_tick,
            name="update_check_tick",
            minute=set(range(60)),
            run_at_startup=True,
        ),
        cron(
            housekeeping_tick,
            name="housekeeping_tick",
            # Fires every minute but the function itself throttles to
            # 24h. ``run_at_startup`` means a fresh deployment trims its
            # backlog immediately.
            minute=set(range(60)),
            run_at_startup=True,
        ),
        cron(
            reap_stale_scans,
            name="reap_stale_scans",
            # v1.8.1: every 5 minutes. Reaps ScanRun rows that got
            # stuck at queued/running because the worker process
            # died mid-scan (OOM, SIGKILL, container restart).
            # The 1-hour staleness threshold means a genuinely
            # long scan won't get reaped while it's still
            # progressing.
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 8
    job_timeout = int(timedelta(hours=1).total_seconds())
    keep_result = int(timedelta(days=1).total_seconds())

    # arq reads ``WorkerSettings.__dict__["redis_settings"]`` directly
    # (see ``arq.worker.get_kwargs``) and passes the value to
    # ``Worker(redis_settings=...)``. That means this MUST be a
    # ``RedisSettings`` instance — not a method or staticmethod.
    # Previously this was decorated ``@staticmethod`` returning a
    # ``RedisSettings``, which left a ``staticmethod`` object in
    # ``__dict__`` and made arq blow up with
    # ``AttributeError: 'staticmethod' object has no attribute 'host'``.
    # Evaluating at class-body time is safe because the worker is
    # invoked via systemd ``EnvironmentFile=…``, so AUDITARR_REDIS_URL
    # is present before Python starts importing this module.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
