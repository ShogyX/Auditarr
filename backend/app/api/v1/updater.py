"""Updater router (``/api/v1/updater``).

Five endpoints. The router is admin-gated for the write paths because
applying an update reconfigures the running container and rolling back
the wrong version can take the box down. Read paths are open to any
authenticated user so the dashboard sidebar can show the "update
available" badge without escalating privileges.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, SessionDep, SettingsDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.schemas.updater import (
    UpdateApplyRead,
    UpdateApplyRequest,
    UpdateCheckRead,
    UpdaterStatusRead,
)
from app.services.repositories import (
    UpdateApplyRepository,
    UpdateCheckRepository,
)
from app.updater import UpdaterService

router = APIRouter(prefix="/updater", tags=["updater"])


# ── Status ─────────────────────────────────────────────────────
# IMPORTANT: ``/status``, ``/check``, ``/apply``, ``/applies``, and
# ``/checks`` are all declared before ``/applies/{apply_id}/rollback``.
# We learned the hard way at Stage 9 + 10 that FastAPI path params can
# silently swallow literal segments — the explicit ordering here matches
# that lesson.
@router.get(
    "/status",
    response_model=UpdaterStatusRead,
    summary="Combined updater state (installed, latest, recent activity)",
)
async def get_status(
    _user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> UpdaterStatusRead:
    service = UpdaterService(session=session, settings=settings, event_bus=bus)
    status_obj = await service.get_status()
    return UpdaterStatusRead(
        installed_version=status_obj.installed_version,
        latest_version=status_obj.latest_version,
        has_update=status_obj.has_update,
        last_checked_at=status_obj.last_checked_at,
        last_check_ok=status_obj.last_check_ok,
        last_check_detail=status_obj.last_check_detail,
        feed_url=status_obj.feed_url,
        apply_in_progress=status_obj.apply_in_progress,
        install_mode=status_obj.install_mode,
        apply_enabled=status_obj.apply_enabled,
    )


# ── Recent checks ──────────────────────────────────────────────
@router.get(
    "/checks",
    response_model=list[UpdateCheckRead],
    summary="Recent update-feed checks",
)
async def list_checks(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[UpdateCheckRead]:
    rows = await UpdateCheckRepository(session).list_recent(limit=limit)
    return [UpdateCheckRead.model_validate(r) for r in rows]


# ── Recent applies ─────────────────────────────────────────────
@router.get(
    "/applies",
    response_model=list[UpdateApplyRead],
    summary="Recent update-apply attempts",
)
async def list_applies(
    _user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[UpdateApplyRead]:
    rows = await UpdateApplyRepository(session).list_recent(limit=limit)
    return [UpdateApplyRead.model_validate(r) for r in rows]


# ── Force a check ──────────────────────────────────────────────
@router.post(
    "/check",
    response_model=UpdateCheckRead,
    summary="Force an immediate feed check (admin)",
)
async def trigger_check(
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> UpdateCheckRead:
    service = UpdaterService(session=session, settings=settings, event_bus=bus)
    row = await service.check_now()
    return UpdateCheckRead.model_validate(row)


# ── Request an apply ───────────────────────────────────────────
@router.post(
    "/apply",
    response_model=UpdateApplyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Request an apply of a target version (admin)",
)
async def request_apply(
    body: UpdateApplyRequest,
    user: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> UpdateApplyRead:
    service = UpdaterService(session=session, settings=settings, event_bus=bus)
    try:
        row = await service.request_apply(
            to_version=body.to_version, triggered_by_user_id=user.id
        )
    except ValueError as exc:
        # The service uses ValueError for "another apply is in progress" —
        # surface it as a 409 so the UI can show "already running".
        raise ConflictError(str(exc)) from exc
    return UpdateApplyRead.model_validate(row)


# ── Force-clear (v1.9 Stage 1.2) ───────────────────────────────
@router.post(
    "/applies/{apply_id}/force-clear",
    response_model=UpdateApplyRead,
    summary="Force-clear a stuck apply (admin)",
)
async def force_clear_apply(
    apply_id: str,
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> UpdateApplyRead:
    """Operator escape hatch when a host helper never reported back.

    The status endpoint's authoritative reaper picks up stale rows
    automatically after ``update_apply_timeout_seconds`` (default
    30 min), but if an operator doesn't want to wait, this endpoint
    flips the row to ``failed`` immediately.
    """
    service = UpdaterService(session=session, settings=settings, event_bus=bus)
    try:
        row = await service.force_clear(apply_id)
    except ValueError as exc:
        message = str(exc)
        if "Unknown apply" in message:
            raise NotFoundError(message) from exc
        raise ValidationError(message) from exc
    return UpdateApplyRead.model_validate(row)


# ── Rollback ───────────────────────────────────────────────────
@router.post(
    "/applies/{apply_id}/rollback",
    response_model=UpdateApplyRead,
    summary="Roll back a completed apply by re-requesting the previous version",
)
async def rollback(
    apply_id: str,
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
) -> UpdateApplyRead:
    service = UpdaterService(session=session, settings=settings, event_bus=bus)
    try:
        row = await service.rollback(apply_id)
    except ValueError as exc:
        message = str(exc)
        if "Unknown apply" in message:
            raise NotFoundError(message) from exc
        # "Cannot roll back" + "already in progress" both come through
        # this path; either is a 422-class operator error.
        raise ValidationError(message) from exc
    return UpdateApplyRead.model_validate(row)
