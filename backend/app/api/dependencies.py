"""FastAPI dependency-injection helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.registry import ServiceRegistry, get_registry
from app.core.settings import Settings, get_settings
from app.events.bus import EventBus, get_event_bus
from app.plugins.loader import PluginLoader, get_plugin_loader
from app.storage.cache import RedisClient, get_redis
from app.storage.database import Database, get_database


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """Yield a transactional async session that commits on success.

    The dependency commits when the endpoint returns normally and rolls back
    on any exception (including HTTP errors raised as :class:`AuditarrError`).
    """
    db = get_database()
    async with db.session() as sess:
        try:
            yield sess
        except Exception:
            await sess.rollback()
            raise
        else:
            await sess.commit()


# Annotated aliases so endpoints stay tidy.
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[ServiceRegistry, Depends(get_registry)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]
DatabaseDep = Annotated[Database, Depends(get_database)]
PluginLoaderDep = Annotated[PluginLoader, Depends(get_plugin_loader)]
