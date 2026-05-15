"""Update check + apply repositories."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.update_apply import UpdateApply
from app.models.update_check import UpdateCheck


class UpdateCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: UpdateCheck) -> UpdateCheck:
        self._session.add(row)
        await self._session.flush()
        return row

    async def latest(self) -> UpdateCheck | None:
        """The most recent check row, regardless of ok/not."""
        result = await self._session.execute(
            select(UpdateCheck)
            .order_by(UpdateCheck.checked_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 20) -> Sequence[UpdateCheck]:
        result = await self._session.execute(
            select(UpdateCheck)
            .order_by(UpdateCheck.checked_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


class UpdateApplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: UpdateApply) -> UpdateApply:
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, apply_id: str) -> UpdateApply | None:
        return await self._session.get(UpdateApply, apply_id)

    async def has_open(self) -> bool:
        """True if there's a requested or running apply."""
        result = await self._session.execute(
            select(UpdateApply.id)
            .where(UpdateApply.status.in_(["requested", "running"]))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_recent(self, limit: int = 20) -> Sequence[UpdateApply]:
        result = await self._session.execute(
            select(UpdateApply)
            .order_by(UpdateApply.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
