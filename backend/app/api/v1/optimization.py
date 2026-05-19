"""Optimization router (``/api/v1/optimization``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import update

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.manager import IntegrationManager
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.optimization import OptimizationWorker
from app.optimization.profile_schema import ProfileDefinition
from app.schemas.optimization import (
    OptimizationBulkEnqueueRequest,
    OptimizationBulkEnqueueResponse,
    OptimizationEnqueueRequest,
    OptimizationItemDetailRead,
    OptimizationProfileCreate,
    OptimizationProfileRead,
    OptimizationProfileUpdate,
    WorkerReportRead,
)
from app.security.secrets import get_secret_box
from app.services.repositories import (
    MediaRepository,
    OptimizationProfileRepository,
    OptimizationRepository,
)
from app.utils.datetime import utcnow

router = APIRouter(prefix="/optimization", tags=["optimization"])


def _validate_profile_settings(settings: dict) -> ProfileDefinition:
    """Validate a profile body, returning the parsed definition.

    Mirrors :func:`rules._validate_definition` — we stringify any
    underlying exceptions Pydantic embedded in ``ctx`` so the JSON error
    envelope is serializable.
    """
    try:
        return ProfileDefinition.model_validate(settings)
    except PydanticValidationError as exc:
        errors = []
        for err in exc.errors(include_url=False):
            entry = dict(err)
            if "ctx" in entry and isinstance(entry["ctx"], dict):
                entry["ctx"] = {
                    k: str(v) if isinstance(v, BaseException) else v
                    for k, v in entry["ctx"].items()
                }
            errors.append(entry)
        raise ValidationError(
            "Profile settings are invalid",
            details={"errors": errors},
        ) from exc


# ── Profiles ────────────────────────────────────────────────────
@router.get(
    "/profiles",
    response_model=list[OptimizationProfileRead],
    summary="List optimization profiles",
)
async def list_profiles(
    _user: CurrentUser, session: SessionDep
) -> list[OptimizationProfileRead]:
    rows = await OptimizationProfileRepository(session).list_all()
    return [OptimizationProfileRead.model_validate(r) for r in rows]


@router.post(
    "/profiles",
    response_model=OptimizationProfileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an optimization profile",
)
async def create_profile(
    body: OptimizationProfileCreate,
    _admin: AdminUser,
    session: SessionDep,
) -> OptimizationProfileRead:
    _validate_profile_settings(body.settings)
    repo = OptimizationProfileRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("A profile with that name already exists")
    profile = OptimizationProfile(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        settings=body.settings,
        max_input_bytes=body.max_input_bytes,
        # Stage 7 (audit follow-up): routing column. NULL ⇒ in-process
        # ffmpeg runner; non-NULL identifies an integration that will
        # take the job once the dispatch wiring lands.
        optimization_integration_id=body.optimization_integration_id,
    )
    await repo.add(profile)
    return OptimizationProfileRead.model_validate(profile)


@router.get(
    "/profiles/{profile_id}",
    response_model=OptimizationProfileRead,
    summary="Get a profile",
)
async def get_profile(
    profile_id: str, _user: CurrentUser, session: SessionDep
) -> OptimizationProfileRead:
    profile = await OptimizationProfileRepository(session).get(profile_id)
    if profile is None:
        raise NotFoundError("Profile not found")
    return OptimizationProfileRead.model_validate(profile)


@router.patch(
    "/profiles/{profile_id}",
    response_model=OptimizationProfileRead,
    summary="Update a profile",
)
async def update_profile(
    profile_id: str,
    body: OptimizationProfileUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> OptimizationProfileRead:
    repo = OptimizationProfileRepository(session)
    profile = await repo.get(profile_id)
    if profile is None:
        raise NotFoundError("Profile not found")
    if body.settings is not None:
        _validate_profile_settings(body.settings)
        profile.settings = body.settings
    if body.name is not None:
        profile.name = body.name
    if body.description is not None:
        profile.description = body.description
    if body.enabled is not None:
        profile.enabled = body.enabled
    if body.max_input_bytes is not None:
        profile.max_input_bytes = body.max_input_bytes
    # Stage 7 (audit follow-up): for the routing column we honour
    # explicit-null. ``body.optimization_integration_id is None`` is
    # ambiguous (could be "don't touch" OR "clear"); using Pydantic's
    # model_fields_set lets us distinguish the two.
    if "optimization_integration_id" in body.model_fields_set:
        profile.optimization_integration_id = (
            body.optimization_integration_id
        )
    await session.flush()
    return OptimizationProfileRead.model_validate(profile)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a profile (refuses if active queue items reference it)",
)
async def delete_profile(
    profile_id: str,
    _admin: AdminUser,
    session: SessionDep,
    force: bool = Query(
        default=False,
        description=(
            "Delete the profile even when active queue items still "
            "reference it. The items will keep failing with "
            "\"profile X not found\" until they're cancelled or "
            "re-queued against a surviving profile. Use only when "
            "the operator has confirmed they'll deal with the "
            "orphans."
        ),
    ),
) -> None:
    repo = OptimizationProfileRepository(session)
    profile = await repo.get(profile_id)
    if profile is None:
        raise NotFoundError("Profile not found")
    if not force:
        active = await OptimizationRepository(session).count_active_for_profile(
            profile.name
        )
        if active > 0:
            raise ConflictError(
                f"Cannot delete profile {profile.name!r}: "
                f"{active} active queue item(s) still reference it. "
                "Cancel or finish those items first, or pass "
                "?force=true to delete anyway (orphaned items will "
                "fail forever).",
                details={
                    "profile_id": profile.id,
                    "profile_name": profile.name,
                    "active_item_count": active,
                },
            )
    await repo.delete(profile)


# ── Queue ───────────────────────────────────────────────────────
# IMPORTANT: declare the literal-path routes (/run-next, /enqueue,
# /queue) *before* /{item_id}, or FastAPI's path-param match will eat
# the literal strings.
@router.get(
    "/queue",
    response_model=list[OptimizationItemDetailRead],
    summary="List queue items with full Stage 10 detail",
)
async def list_queue(
    _user: CurrentUser,
    session: SessionDep,
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OptimizationItemDetailRead]:
    rows = await OptimizationRepository(session).list_all(
        status=status_, limit=limit
    )
    return [OptimizationItemDetailRead.model_validate(r) for r in rows]


@router.post(
    "/enqueue",
    response_model=OptimizationItemDetailRead,
    status_code=status.HTTP_201_CREATED,
    summary="Manually enqueue a (file, profile) pair",
)
async def enqueue_item(
    body: OptimizationEnqueueRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> OptimizationItemDetailRead:
    # Validate the file and profile exist before queueing — the worker
    # would catch this later, but failing fast is friendlier.
    if await MediaRepository(session).get(body.media_file_id) is None:
        raise NotFoundError("Media file not found")
    if (
        await OptimizationProfileRepository(session).get_by_name(body.profile)
        is None
    ):
        raise NotFoundError(f"Profile {body.profile!r} not found")
    item = await OptimizationRepository(session).upsert_queued(
        media_file_id=body.media_file_id,
        profile=body.profile,
        rule_id=None,
        queued_at=utcnow(),
    )
    await session.commit()
    return OptimizationItemDetailRead.model_validate(item)


# ── Stage 28: bulk enqueue ──────────────────────────────────────
# Closes the last Stage 23 ledger item (bulk-optimize from the
# Files page selection bar). Sits between ``/enqueue`` and
# ``/run-next`` in the route table so the literal-path-first
# ordering rule is preserved — see the IMPORTANT note above
# ``GET /queue``.
@router.post(
    "/bulk-enqueue",
    response_model=OptimizationBulkEnqueueResponse,
    summary="Enqueue many files against one profile (Stage 28)",
)
async def bulk_enqueue(
    body: OptimizationBulkEnqueueRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> OptimizationBulkEnqueueResponse:
    """Queue a list of files against a single optimization profile.

    Per-bucket outcomes:

    - ``queued``: pair didn't exist or existed in a terminal state
      and is being re-queued (matches the
      ``OptimizationRepository.upsert_queued`` contract — it only
      mutates rows in ``queued`` state, so re-queueing a completed
      pair won't clobber the prior run history; the pair becomes
      "queued" again via a separate path).
    - ``already_queued``: pair was already pending; ``queued_at``
      is refreshed but the row count doesn't grow.
    - ``skipped_active``: pair was in ``running``/``completed``/
      ``failed``/``cancelled``/``skipped`` state; we leave it
      alone. The operator can still re-queue via the Retry button
      on the Optimization page.
    - ``files_not_found``: ids that didn't resolve to a media row.

    Profile lookup fails the WHOLE request (404) — we don't want
    to surprise the operator by silently picking another profile
    or "succeeding" with nothing queued. Duplicates in
    ``media_ids`` are rejected (400) consistent with the other
    bulk endpoints in the project.
    """
    if len(set(body.media_ids)) != len(body.media_ids):
        raise ValidationError("media_ids must not contain duplicates")

    profile_repo = OptimizationProfileRepository(session)
    profile = await profile_repo.get_by_name(body.profile)
    if profile is None:
        raise NotFoundError(f"Profile {body.profile!r} not found")
    if not profile.enabled:
        raise ValidationError(
            f"Profile {body.profile!r} is disabled — enable it before bulk enqueueing"
        )

    media_repo = MediaRepository(session)
    opt_repo = OptimizationRepository(session)
    now = utcnow()
    queued = already_queued = skipped_active = 0
    not_found: list[str] = []

    for mid in body.media_ids:
        record = await media_repo.get(mid)
        if record is None:
            not_found.append(mid)
            continue

        # Probe the current state for this (file, profile) pair so
        # we can attribute outcomes correctly. The repository's
        # upsert_queued returns the row but doesn't tell us whether
        # it added or refreshed — for the per-bucket count we need
        # the pre-state. A bare select keeps it simple.
        from sqlalchemy import select

        from app.models.optimization import OptimizationItem

        result = await session.execute(
            select(OptimizationItem).where(
                OptimizationItem.media_file_id == mid,
                OptimizationItem.profile == body.profile,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            await opt_repo.upsert_queued(
                media_file_id=mid,
                profile=body.profile,
                rule_id=None,
                queued_at=now,
            )
            queued += 1
        elif existing.status == "queued":
            await opt_repo.upsert_queued(
                media_file_id=mid,
                profile=body.profile,
                rule_id=None,
                queued_at=now,
            )
            already_queued += 1
        else:
            # running / completed / failed / cancelled / skipped — leave alone.
            skipped_active += 1

    await session.commit()
    return OptimizationBulkEnqueueResponse(
        queued=queued,
        already_queued=already_queued,
        skipped_active=skipped_active,
        files_not_found=not_found,
    )


@router.post(
    "/run-next",
    response_model=WorkerReportRead,
    summary="Run the oldest queued item synchronously",
)
async def run_next_now(
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> WorkerReportRead:
    # Stage 08 (v1.7) — pass IntegrationManager so a routed item
    # picked up by the "Run now" button actually dispatches to
    # its integration provider instead of just stamping ``routed``
    # and stopping.
    manager = IntegrationManager(
        session=session,
        registry=registry,
        secret_box=get_secret_box(),
        event_bus=bus,
    )
    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_one()
    return WorkerReportRead(
        item_id=report.item_id, status=report.status, detail=report.detail
    )


@router.get(
    "/{item_id}",
    response_model=OptimizationItemDetailRead,
    summary="Get one queue item",
)
async def get_item(
    item_id: str, _user: CurrentUser, session: SessionDep
) -> OptimizationItemDetailRead:
    item = await OptimizationRepository(session).get(item_id)
    if item is None:
        raise NotFoundError("Optimization item not found")
    return OptimizationItemDetailRead.model_validate(item)


@router.post(
    "/{item_id}/run",
    response_model=WorkerReportRead,
    summary="Run a specific queue item synchronously",
)
async def run_item(
    item_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> WorkerReportRead:
    manager = IntegrationManager(
        session=session,
        registry=registry,
        secret_box=get_secret_box(),
        event_bus=bus,
    )
    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_item(item_id)
    if report.status == "failed" and report.detail == "not found":
        raise NotFoundError("Optimization item not found")
    return WorkerReportRead(
        item_id=report.item_id, status=report.status, detail=report.detail
    )


@router.post(
    "/{item_id}/cancel",
    response_model=OptimizationItemDetailRead,
    summary="Cancel a queued (or running, best-effort) item",
)
async def cancel_item(
    item_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
) -> OptimizationItemDetailRead:
    repo = OptimizationRepository(session)
    item = await repo.get(item_id)
    if item is None:
        raise NotFoundError("Optimization item not found")
    if item.status in ("completed", "failed", "cancelled", "skipped"):
        raise ValidationError(
            f"Cannot cancel item in status {item.status!r}",
            details={"status": item.status},
        )
    # Bug-hunt 2: atomic state transition. A naive SET-then-COMMIT
    # like the original would let two concurrent cancel clicks both
    # pass the status check, both write status=cancelled, both
    # commit, both emit ``optimization.failed``. The duplicate
    # event lights up notifications twice and confuses dashboards
    # that subscribe to the bus. The conditional UPDATE here
    # succeeds only if the item is still in a cancellable state
    # at the moment the UPDATE runs; ``rowcount`` tells us whether
    # we won the race, and we skip the event emission if we
    # didn't.
    now = utcnow()
    cancellable_states = ("queued", "running")
    update_result = await session.execute(
        update(OptimizationItem)
        .where(
            OptimizationItem.id == item_id,
            OptimizationItem.status.in_(cancellable_states),
        )
        .values(status="cancelled", finished_at=now)
        .execution_options(synchronize_session="fetch")
    )
    await session.commit()
    if update_result.rowcount == 0:
        # Another caller cancelled (or completed/failed) this item
        # between our SELECT and our UPDATE. Reload and return the
        # current state without re-emitting the event.
        item = await repo.get(item_id)
        if item is None:
            raise NotFoundError("Optimization item not found")
        return OptimizationItemDetailRead.model_validate(item)

    # We won the race. Reload + emit once.
    item = await repo.get(item_id)
    assert item is not None  # we just updated it
    await bus.emit(
        "optimization.failed",
        {"item_id": item.id, "status": "cancelled", "reason": "user cancelled"},
        source="optimization",
    )
    return OptimizationItemDetailRead.model_validate(item)


@router.post(
    "/{item_id}/retry",
    response_model=OptimizationItemDetailRead,
    summary="Re-queue a failed/cancelled item",
)
async def retry_item(
    item_id: str, _admin: AdminUser, session: SessionDep
) -> OptimizationItemDetailRead:
    repo = OptimizationRepository(session)
    item = await repo.get(item_id)
    if item is None:
        raise NotFoundError("Optimization item not found")
    if item.status == "queued":
        return OptimizationItemDetailRead.model_validate(item)
    if item.status == "running":
        raise ValidationError(
            "Item is currently running; cancel it first to retry"
        )
    # Bug-hunt 2: atomic re-queue. Without the conditional UPDATE,
    # two retry clicks on the same failed/cancelled item would both
    # pass the status check above, both write status=queued, both
    # commit. The second clobbers ``queued_at`` (affecting FIFO
    # order) and the response returns one of the two race-winning
    # rows. Conditional UPDATE here narrows the win to one caller;
    # losers see the already-queued state.
    now = utcnow()
    retriable_states = ("failed", "cancelled", "skipped", "completed")
    update_result = await session.execute(
        update(OptimizationItem)
        .where(
            OptimizationItem.id == item_id,
            OptimizationItem.status.in_(retriable_states),
        )
        .values(
            status="queued",
            started_at=None,
            finished_at=None,
            progress_pct=0,
            error=None,
            queued_at=now,
        )
        .execution_options(synchronize_session="fetch")
    )
    await session.commit()
    if update_result.rowcount == 0:
        # Another caller raced us to the retry; return the row
        # they produced. This is idempotent semantics: both
        # callers see "yes, it's queued."
        item = await repo.get(item_id)
        if item is None:
            raise NotFoundError("Optimization item not found")
        return OptimizationItemDetailRead.model_validate(item)
    item = await repo.get(item_id)
    assert item is not None
    return OptimizationItemDetailRead.model_validate(item)
