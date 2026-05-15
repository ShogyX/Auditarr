"""Audit log repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLogEntry


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogEntry) -> AuditLogEntry:
        self._session.add(entry)
        await self._session.flush([entry])
        return entry

    async def list_recent(self, limit: int = 100) -> list[AuditLogEntry]:
        stmt = (
            select(AuditLogEntry)
            .order_by(AuditLogEntry.occurred_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def filter(  # noqa: A003 — domain term
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        since: "Any | None" = None,
        until: "Any | None" = None,
        before_id: int | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        """Filter audit rows with cursor-style pagination.

        Stage 14 (audit follow-up): adds ``since`` / ``until`` for
        the new viewer's date-range filter, and ``before_id`` for
        the "Load more" affordance. The audit table has no stable
        offset cursor (rows can be inserted under heavy load and the
        offset would shift); using the auto-increment primary key as
        a cursor is stable because IDs only grow.
        """
        stmt = select(AuditLogEntry).order_by(AuditLogEntry.id.desc())
        clauses: list[Any] = []
        if actor_id:
            clauses.append(AuditLogEntry.actor_id == actor_id)
        if action:
            clauses.append(AuditLogEntry.action == action)
        if since is not None:
            clauses.append(AuditLogEntry.occurred_at >= since)
        if until is not None:
            clauses.append(AuditLogEntry.occurred_at <= until)
        if before_id is not None:
            clauses.append(AuditLogEntry.id < before_id)
        if clauses:
            stmt = stmt.where(*clauses)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars())
