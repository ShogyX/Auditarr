"""Automation router (``/api/v1/automation``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.automation.catalogue import get_catalogue
from app.automation.cron import validate_cron
from app.automation.scheduler import Scheduler
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.schedule import Schedule
from app.schemas.automation import (
    JobKindRead,
    JobRunRead,
    JobRunRequest,
    OptimizationItemRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from app.services.repositories import (
    JobRunRepository,
    OptimizationRepository,
    ScheduleRepository,
)
from app.services.media import get_ffprobe_service

router = APIRouter(prefix="/automation", tags=["automation"])


def _runtime_ctx(registry, bus) -> dict:
    """Build the ctx dict passed to job runners."""
    return {
        "registry": registry,
        "bus": bus,
        "ffprobe": get_ffprobe_service(),
    }


def _validate_definition(body: ScheduleCreate | ScheduleUpdate) -> None:
    catalogue = get_catalogue()
    if isinstance(body, ScheduleCreate):
        if catalogue.get(body.job_kind) is None:
            raise ValidationError(f"Unknown job_kind: {body.job_kind!r}")
        try:
            catalogue.validate_args(body.job_kind, body.job_args)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            validate_cron(body.cron)
        except ValueError as exc:
            raise ValidationError(f"Invalid cron spec: {exc}") from exc
    else:
        if body.cron is not None:
            try:
                validate_cron(body.cron)
            except ValueError as exc:
                raise ValidationError(f"Invalid cron spec: {exc}") from exc


# ── Job catalogue ───────────────────────────────────────────────
@router.get(
    "/jobs",
    response_model=list[JobKindRead],
    summary="List job kinds available to schedules",
)
async def list_job_kinds(_user: CurrentUser) -> list[JobKindRead]:
    return [
        JobKindRead(
            key=spec.key,
            label=spec.label,
            description=spec.description,
            args_schema=spec.args_schema,
            required_args=list(spec.required_args),
            timeout_seconds=spec.timeout_seconds,
        )
        for spec in get_catalogue().list_all()
    ]


# ── Schedules ───────────────────────────────────────────────────
@router.get(
    "/schedules", response_model=list[ScheduleRead], summary="List schedules"
)
async def list_schedules(
    _user: CurrentUser, session: SessionDep
) -> list[ScheduleRead]:
    return [
        ScheduleRead.model_validate(s)
        for s in await ScheduleRepository(session).list_all()
    ]


@router.post(
    "/schedules",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule",
)
async def create_schedule(
    body: ScheduleCreate,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> ScheduleRead:
    _validate_definition(body)
    repo = ScheduleRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("A schedule with that name already exists")
    schedule = Schedule(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        job_kind=body.job_kind,
        job_args=body.job_args,
        cron=body.cron,
        timeout_seconds=body.timeout_seconds,
    )
    scheduler = Scheduler(session=session, event_bus=bus)
    await scheduler.prime_next_run(schedule)
    await repo.add(schedule)
    return ScheduleRead.model_validate(schedule)


@router.get(
    "/schedules/{schedule_id}",
    response_model=ScheduleRead,
    summary="Get a schedule",
)
async def get_schedule(
    schedule_id: str, _user: CurrentUser, session: SessionDep
) -> ScheduleRead:
    schedule = await ScheduleRepository(session).get(schedule_id)
    if schedule is None:
        raise NotFoundError("Schedule not found")
    return ScheduleRead.model_validate(schedule)


@router.patch(
    "/schedules/{schedule_id}",
    response_model=ScheduleRead,
    summary="Update a schedule",
)
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> ScheduleRead:
    repo = ScheduleRepository(session)
    schedule = await repo.get(schedule_id)
    if schedule is None:
        raise NotFoundError("Schedule not found")
    _validate_definition(body)

    if body.name is not None:
        schedule.name = body.name
    if body.description is not None:
        schedule.description = body.description
    if body.enabled is not None:
        schedule.enabled = body.enabled
    if body.job_args is not None:
        get_catalogue().validate_args(schedule.job_kind, body.job_args)
        schedule.job_args = body.job_args
    if body.cron is not None:
        schedule.cron = body.cron
        scheduler = Scheduler(session=session, event_bus=bus)
        await scheduler.prime_next_run(schedule)
    if body.timeout_seconds is not None:
        schedule.timeout_seconds = body.timeout_seconds

    await session.flush()
    return ScheduleRead.model_validate(schedule)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
async def delete_schedule(
    schedule_id: str, _admin: AdminUser, session: SessionDep
) -> None:
    repo = ScheduleRepository(session)
    schedule = await repo.get(schedule_id)
    if schedule is None:
        raise NotFoundError("Schedule not found")
    await repo.delete(schedule)


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=JobRunRead,
    summary="Run a schedule's job immediately, bypassing the cron",
)
async def run_schedule_now(
    schedule_id: str,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> JobRunRead:
    schedule = await ScheduleRepository(session).get(schedule_id)
    if schedule is None:
        raise NotFoundError("Schedule not found")
    scheduler = Scheduler(session=session, event_bus=bus)
    run = await scheduler.run_job_now(
        job_kind=schedule.job_kind,
        args=dict(schedule.job_args),
        ctx=_runtime_ctx(registry, bus),
        trigger="manual",
        schedule=schedule,
    )
    return JobRunRead.model_validate(run)


# ── Ad-hoc runs ─────────────────────────────────────────────────
@router.post(
    "/run",
    response_model=JobRunRead,
    summary="Run a one-off job (no associated schedule)",
)
async def run_job_now(
    body: JobRunRequest,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> JobRunRead:
    catalogue = get_catalogue()
    if catalogue.get(body.job_kind) is None:
        raise ValidationError(f"Unknown job_kind: {body.job_kind!r}")
    try:
        catalogue.validate_args(body.job_kind, body.job_args)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    scheduler = Scheduler(session=session, event_bus=bus)
    run = await scheduler.run_job_now(
        job_kind=body.job_kind,
        args=dict(body.job_args),
        ctx=_runtime_ctx(registry, bus),
        trigger="manual",
    )
    return JobRunRead.model_validate(run)


# ── Job runs ────────────────────────────────────────────────────
@router.get(
    "/runs",
    response_model=list[JobRunRead],
    summary="List recent job runs",
)
async def list_runs(
    _user: CurrentUser,
    session: SessionDep,
    schedule_id: str | None = Query(default=None),
    job_kind: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[JobRunRead]:
    rows = await JobRunRepository(session).list_recent(
        schedule_id=schedule_id,
        job_kind=job_kind,
        status=status_,
        limit=limit,
    )
    return [JobRunRead.model_validate(r) for r in rows]


@router.get(
    "/runs/{run_id}",
    response_model=JobRunRead,
    summary="Get a single job run",
)
async def get_run(
    run_id: str, _user: CurrentUser, session: SessionDep
) -> JobRunRead:
    run = await JobRunRepository(session).get(run_id)
    if run is None:
        raise NotFoundError("Job run not found")
    return JobRunRead.model_validate(run)


# ── Optimization queue (Stage 7 surface; Stage 10 will own consumption) ──
@router.get(
    "/optimization-queue",
    response_model=list[OptimizationItemRead],
    summary="List the optimization queue",
)
async def list_optimization_queue(
    _user: CurrentUser,
    session: SessionDep,
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OptimizationItemRead]:
    rows = await OptimizationRepository(session).list_all(
        status=status_, limit=limit
    )
    return [OptimizationItemRead.model_validate(r) for r in rows]
