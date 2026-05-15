"""Audit logging service.

Spec §4.1 mandates audit logging for: authentication, rule changes, automation
changes, plugin changes, update actions, integration failures, notification
failures, and severity modifications. All flow through this service.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLogEntry
from app.services.repositories.audit import AuditRepository


class AuditService:
    """Persist audit-log entries on behalf of other services."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def record(
        self,
        action: str,
        *,
        actor_id: str | None = None,
        actor_label: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        ip_address: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            action=action,
            actor_id=actor_id,
            actor_label=actor_label,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            request_id=request_id,
            metadata_=metadata,
        )
        return await self._repo.add(entry)
