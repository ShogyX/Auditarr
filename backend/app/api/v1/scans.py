"""Scans router (``/api/v1/scans``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RedisDep, RegistryDep, SessionDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.scan_run import ScanRun
from app.schemas.media import ScanRunRead, ScanTriggerRequest
from app.services.media import Scanner, ScanOptions, get_ffprobe_service
from app.services.repositories import LibraryRepository, ScanRepository
from app.utils.datetime import utcnow

router = APIRouter(prefix="/scans", tags=["scans"])
log = get_logger("auditarr.api.scans", category="api")


@router.get("", response_model=list[ScanRunRead], summary="List recent scans")
async def list_scans(
    _user: CurrentUser,
    session: SessionDep,
    library_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[ScanRunRead]:
    repo = ScanRepository(session)
    runs = (
        await repo.list_for_library(library_id, limit=limit)
        if library_id
        else await repo.list_recent(limit=limit)
    )
    return [ScanRunRead.model_validate(r) for r in runs]


@router.get("/{scan_id}", response_model=ScanRunRead, summary="Get a scan run")
async def get_scan(
    scan_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> ScanRunRead:
    run = await ScanRepository(session).get(scan_id)
    if run is None:
        raise NotFoundError("Scan run not found")
    return ScanRunRead.model_validate(run)


@router.post(
    "/libraries/{library_id}",
    response_model=ScanRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a scan for a library",
)
async def trigger_scan(
    library_id: str,
    body: ScanTriggerRequest,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    redis: RedisDep,
    registry: RegistryDep,
    enqueue: bool = Query(
        default=True,
        description=(
            "When true (default, Stage 8 audit follow-up), enqueue the "
            "scan to the ARQ worker and return immediately with HTTP 202. "
            "When false, run synchronously in-process and return the "
            "completed run — only safe for small libraries or test "
            "fixtures, because the API worker has a hard timeout."
        ),
    ),
) -> ScanRunRead:
    library = await LibraryRepository(session).get(library_id)
    if library is None:
        raise NotFoundError("Library not found")
    if not library.enabled:
        raise ValidationError("Library is disabled")

    # Bug-hunt 2: refuse to start a second scan against a library
    # that already has one running or queued. Without this check,
    # two rapid POSTs (operator double-click, automation tick +
    # manual click, etc.) would each kick off a scanner against
    # the same directory:
    #   - duplicate ``scan.started`` events fire
    #   - ffprobe runs on every file twice — CPU/IO waste
    #   - two ``ScanRun`` rows show as "running" in the UI,
    #     confusing operators about which one to watch
    # The single-flight check is by library, not global. Two
    # libraries can scan concurrently; the same library can't.
    # 409 Conflict is the right status — the request is well-
    # formed but conflicts with current resource state.
    active = await ScanRepository(session).find_active_for_library(library.id)
    if active is not None:
        raise ConflictError(
            f"A scan is already {active.status} for this library "
            f"(run id {active.id}). Wait for it to finish before "
            "starting another.",
            details={"library_id": library.id, "active_run_id": active.id},
        )

    if enqueue:
        # Pre-create a queued ScanRun so the caller has a stable id; the
        # worker will pick it up and update it as it progresses.
        run = ScanRun(
            library_id=library.id,
            mode=body.mode,
            status="queued",
            options={"follow_symlinks": body.follow_symlinks},
        )
        await ScanRepository(session).add(run)
        await session.commit()

        try:
            # v1.8.1: pass an explicit ``_job_id`` that includes
            # the ScanRun id so ARQ's dedupe key is unique per
            # row. Without this, ARQ hashes (function, args) and
            # silently returns None on a duplicate — leaving the
            # row stuck at "queued" forever. The 409 check above
            # SHOULD catch this case at the application level,
            # but a unique job_id is the defense-in-depth that
            # surfaces the issue if the 409 check ever misses
            # (e.g. concurrent requests racing the check).
            job = await redis.enqueue(
                "scan_library",
                library.id,
                mode=body.mode,
                follow_symlinks=body.follow_symlinks,
                _job_id=f"scan_library:{run.id}",
            )
            if job is None:
                # ARQ returned None — a job with this id was
                # already on the queue. Given the unique run.id
                # in the key, this means a stale job from a prior
                # restart is still there. Mark the row failed
                # with a clear message so the operator knows
                # exactly what to do.
                log.error(
                    "scans.enqueue_dedup_collision",
                    library_id=library.id,
                    run_id=run.id,
                )
                run.status = "failed"
                run.error = (
                    "ARQ refused to enqueue this scan because a job "
                    "with the same id is already on the queue. This "
                    "usually means a prior worker crashed without "
                    "clearing its in-flight jobs. Restart the worker "
                    "or wait for the stale-scan reaper to clean up "
                    "the queued row."
                )
                run.finished_at = utcnow()
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("scans.enqueue_failed", error=str(exc), library_id=library.id)
            # Stage 8 (audit follow-up): with async as the default,
            # raising 422 on queue-unavailable would punish operators
            # who didn't opt in to async. Mark the row "failed" and
            # return 202 — the UI surfaces the failure state via the
            # row, and the operator sees a clear "queue unavailable"
            # error attached to the run instead of a 4xx response.
            # Pre-Stage-8 (sync default) flow raised ValidationError;
            # callers that depended on the 422 can either pass
            # ``?enqueue=false`` (legacy sync mode) or read the
            # returned ``run.status == "failed"``.
            run.status = "failed"
            run.error = f"queue unavailable: {exc}"
            run.finished_at = utcnow()
            await session.commit()
        return ScanRunRead.model_validate(run)

    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=get_ffprobe_service(),
        registry=registry,
    )
    report = await scanner.scan(
        library,
        options=ScanOptions(mode=body.mode, follow_symlinks=body.follow_symlinks),
    )
    run = await ScanRepository(session).get(report.run_id)
    if run is None:  # pragma: no cover — defensive
        raise NotFoundError("Scan run not found after creation")
    return ScanRunRead.model_validate(run)


@router.post(
    "/all",
    response_model=list[ScanRunRead],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a scan for every enabled library (Stage 8)",
)
async def trigger_scan_all(
    body: ScanTriggerRequest,
    _admin: AdminUser,
    session: SessionDep,
    redis: RedisDep,
) -> list[ScanRunRead]:
    """Stage 8 (audit follow-up): scan-all affordance.

    Pre-Stage-8, the operator had to walk Settings → Libraries and
    click Run-Scan on each row to refresh the whole index. This
    endpoint enqueues one scan per enabled library and returns the
    list of queued ``ScanRun`` rows.

    Libraries that already have an active scan are silently skipped
    (the per-library endpoint returns 409 in that case; bulk
    skipping is the right behaviour here so a single conflict
    doesn't fail the whole batch). Disabled libraries are skipped
    by design — they're disabled.
    """
    repo = LibraryRepository(session)
    scans = ScanRepository(session)
    libraries = await repo.list_all()
    queued: list[ScanRun] = []

    for library in libraries:
        if not library.enabled:
            continue
        # Single-flight: skip libraries already scanning. Same
        # contract as the per-library endpoint's 409 (Stage 25),
        # but bulk-mode silently skips so one busy library doesn't
        # block scanning everything else.
        active = await scans.find_active_for_library(library.id)
        if active is not None:
            continue

        run = ScanRun(
            library_id=library.id,
            mode=body.mode,
            status="queued",
            options={"follow_symlinks": body.follow_symlinks},
        )
        await scans.add(run)
        queued.append(run)

    # Commit all rows first so they're visible to UI watchers before
    # we attempt to enqueue. If the queue is down, the scans show
    # as "queued" briefly and then get marked "failed" below — the
    # API still returns 202 with the row ids so the UI can render
    # them in the queued state and show the failure when the row
    # transitions.
    await session.commit()

    for run in queued:
        try:
            job = await redis.enqueue(
                "scan_library",
                run.library_id,
                mode=body.mode,
                follow_symlinks=body.follow_symlinks,
                _job_id=f"scan_library:{run.id}",
            )
            if job is None:
                log.error(
                    "scans.scan_all_enqueue_dedup_collision",
                    library_id=run.library_id,
                    run_id=run.id,
                )
                run.status = "failed"
                run.error = (
                    "ARQ refused to enqueue: a job with the same "
                    "id is already on the queue. Likely a stale job "
                    "from a prior worker crash. Restart the worker "
                    "or wait for the reaper."
                )
                run.finished_at = utcnow()
        except Exception as exc:  # noqa: BLE001
            log.error(
                "scans.scan_all_enqueue_failed",
                error=str(exc),
                library_id=run.library_id,
                run_id=run.id,
            )
            run.status = "failed"
            run.error = f"queue unavailable: {exc}"
            run.finished_at = utcnow()

    await session.commit()
    return [ScanRunRead.model_validate(r) for r in queued]


@router.post(
    "/libraries/{library_id}/reset",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Reset stuck scans for a library (v1.8.1)",
)
async def reset_library_scans(
    library_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> dict:
    """v1.8.1: manually mark any stuck ``queued``/``running`` scans
    for a library as ``failed`` so the operator can start a new one.

    Same effect as waiting for the ``reap_stale_scans`` cron to fire
    (every 5 min) plus the 1-hour staleness threshold, but
    available immediately when the operator knows the scan is dead
    (worker restart, OOM, etc.) and doesn't want to wait.

    The frontend offers this as an "Unstick library" button when
    the user hits 409 on a scan trigger.

    Returns ``{"reset_count": N, "run_ids": [...]}``.
    """
    library = await LibraryRepository(session).get(library_id)
    if library is None:
        raise NotFoundError("Library not found")

    from sqlalchemy import select

    from app.models.scan_run import ScanRun

    result = await session.execute(
        select(ScanRun).where(
            ScanRun.library_id == library_id,
            ScanRun.status.in_(("queued", "running")),
        )
    )
    stuck = list(result.scalars().all())

    now = utcnow()
    for run in stuck:
        previous_status = run.status
        run.status = "failed"
        run.finished_at = now
        run.error = (
            f"Manually reset by operator (was '{previous_status}'). "
            "Use this when a scan is stuck because the worker died "
            "mid-run; the library is now free for new scans."
        )

    if stuck:
        library.last_scan_status = "failed"
        await session.commit()
        log.info(
            "scans.manual_reset",
            library_id=library_id,
            run_ids=[r.id for r in stuck],
            count=len(stuck),
        )

    return {
        "reset_count": len(stuck),
        "run_ids": [r.id for r in stuck],
    }
