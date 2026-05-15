"""User repository."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """Persistence for :class:`User`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username.lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_login(self, login: str) -> User | None:
        """Look up by either email or username."""
        login = login.strip().lower()
        if "@" in login:
            return await self.get_by_email(login)
        return await self.get_by_username(login)

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush([user])
        return user

    async def count(self) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(User)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def touch_login(self, user: User) -> None:
        user.last_login_at = _dt.datetime.now(_dt.UTC)
        await self._session.flush([user])

    async def touch(self, user: User) -> None:
        """Flush pending field mutations on ``user`` to the session.

        Used by profile-edit paths (Stage 21) that mutate User
        columns directly and need them written without bumping
        timestamps or other side effects.
        """
        await self._session.flush([user])

    async def bump_token_version(self, user: User) -> None:
        user.token_version += 1
        await self._session.flush([user])
