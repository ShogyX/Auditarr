"""Async database engine + session lifecycle.

Dependencies should obtain sessions through :func:`get_session_dependency`,
which is wired into the FastAPI dependency tree in ``app.api.dependencies``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

log = get_logger("auditarr.database", category="database")


class Database:
    """Holds the engine + sessionmaker pair for the app's lifetime."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    # ── Lifecycle ─────────────────────────────────────────────
    async def connect(self) -> None:
        if self._engine is not None:
            return

        kwargs: dict[str, object] = {
            "echo": self._settings.database_echo,
            "future": True,
            "pool_pre_ping": True,
        }
        if self._settings.is_sqlite:
            # ``:memory:`` SQLite needs a single shared connection so all
            # sessions see the same schema; file-based SQLite uses NullPool to
            # stay safe across asyncio tasks.
            if ":memory:" in self._settings.database_url:
                kwargs["poolclass"] = StaticPool
                kwargs["connect_args"] = {"check_same_thread": False}
            else:
                kwargs["poolclass"] = NullPool
        else:
            kwargs["pool_size"] = self._settings.database_pool_size
            kwargs["max_overflow"] = self._settings.database_max_overflow
            # (Stage 1 / L3) Recycle pooled connections to prevent
            # stale Postgres sockets after the app idles past server-
            # side idle timeouts or NAT keepalive ceilings. Skipped
            # for SQLite (which uses NullPool / StaticPool above and
            # has no socket-lifetime concern). A non-positive value
            # disables the recycle.
            if self._settings.database_pool_recycle > 0:
                kwargs["pool_recycle"] = self._settings.database_pool_recycle

        self._engine = create_async_engine(self._settings.database_url, **kwargs)
        self._sessionmaker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        log.info("database.connected", url=_redact(self._settings.database_url))

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            log.info("database.disconnected")
        self._engine = None
        self._sessionmaker = None

    # ── Access ────────────────────────────────────────────────
    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise ServiceUnavailableError("Database is not connected")
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise ServiceUnavailableError("Database is not connected")
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Context-managed session with automatic rollback on error."""
        async with self.sessionmaker() as sess:
            try:
                yield sess
            except SQLAlchemyError:
                await sess.rollback()
                raise

    async def healthcheck(self) -> bool:
        from sqlalchemy import text

        try:
            async with self.session() as sess:
                await sess.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as exc:
            log.warning("database.healthcheck_failed", error=str(exc))
            return False


_db: Database | None = None


def get_database() -> Database:
    """Return the process-wide database singleton."""
    global _db
    if _db is None:
        _db = Database(get_settings())
    return _db


def _redact(url: str) -> str:
    """Strip credentials from a DSN for logging."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"
