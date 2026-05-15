"""Library repository."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library import Library


class LibraryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, library: Library) -> Library:
        self._session.add(library)
        await self._session.flush()
        return library

    async def get(self, library_id: str) -> Library | None:
        return await self._session.get(Library, library_id)

    async def get_by_name(self, name: str) -> Library | None:
        result = await self._session.execute(
            select(Library).where(Library.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, enabled_only: bool = False) -> Sequence[Library]:
        stmt = select(Library).order_by(Library.name)
        if enabled_only:
            stmt = stmt.where(Library.enabled.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, library: Library) -> None:
        await self._session.delete(library)
        await self._session.flush()
