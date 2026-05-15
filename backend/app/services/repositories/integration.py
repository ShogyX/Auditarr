"""Integration repository."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration


class IntegrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, integration: Integration) -> Integration:
        self._session.add(integration)
        await self._session.flush()
        return integration

    async def get(self, integration_id: str) -> Integration | None:
        return await self._session.get(Integration, integration_id)

    async def get_by_name(self, name: str) -> Integration | None:
        result = await self._session.execute(
            select(Integration).where(Integration.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self, *, kind: str | None = None, enabled_only: bool = False
    ) -> Sequence[Integration]:
        stmt = select(Integration).order_by(Integration.name)
        if kind:
            stmt = stmt.where(Integration.kind == kind)
        if enabled_only:
            stmt = stmt.where(Integration.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, integration: Integration) -> None:
        await self._session.delete(integration)
        await self._session.flush()
