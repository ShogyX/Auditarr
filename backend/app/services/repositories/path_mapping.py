"""Global path mapping repository (Stage 5 audit follow-up).

CRUD over :class:`app.models.path_mapping.GlobalPathMapping`. Read
ordering is ``priority ASC, created_at ASC`` so the resolver applies
the lower-priority mapping first (matching the per-integration list
which is sorted by length-descending; global mappings expose an
explicit priority knob instead because the same operator may want
different ordering at the global layer).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.path_mapping import GlobalPathMapping


class GlobalPathMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(
        self, *, enabled_only: bool = False
    ) -> Sequence[GlobalPathMapping]:
        stmt = select(GlobalPathMapping)
        if enabled_only:
            stmt = stmt.where(GlobalPathMapping.enabled.is_(True))
        stmt = stmt.order_by(
            GlobalPathMapping.priority.asc(),
            GlobalPathMapping.created_at.asc(),
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get(self, mapping_id: str) -> GlobalPathMapping | None:
        return await self._session.get(GlobalPathMapping, mapping_id)

    async def add(self, mapping: GlobalPathMapping) -> GlobalPathMapping:
        self._session.add(mapping)
        await self._session.flush()
        return mapping

    async def delete(self, mapping: GlobalPathMapping) -> None:
        await self._session.delete(mapping)
        await self._session.flush()
