"""Audit log read endpoints (admin-only)."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from fastapi import APIRouter, Query

from app.api.auth_deps import AdminUser
from app.api.dependencies import SessionDep
from app.services.repositories import AuditRepository

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/log", summary="List recent audit log entries")
async def list_log(
    _admin: AdminUser,
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    actor_id: str | None = None,
    action: str | None = None,
    # Stage 14 (audit follow-up): date-range filter for the new
    # viewer.
    since: _dt.datetime | None = Query(default=None),
    until: _dt.datetime | None = Query(default=None),
    # Stage 14 (audit follow-up): cursor for the "Load more" button.
    # ``id`` is auto-increment + monotonically growing, so it's a
    # stable cursor across pagination even under heavy insert load.
    before_id: int | None = Query(default=None, ge=1),
) -> list[dict[str, Any]]:
    repo = AuditRepository(session)
    rows = await repo.filter(
        actor_id=actor_id,
        action=action,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )
    return [
        {
            "id": r.id,
            "occurred_at": r.occurred_at.isoformat(),
            "actor_id": r.actor_id,
            "actor_label": r.actor_label,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "ip_address": r.ip_address,
            "request_id": r.request_id,
            "metadata": r.metadata_,
        }
        for r in rows
    ]
