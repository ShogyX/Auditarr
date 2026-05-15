"""Database engine pool-configuration tests (Stage 1 / L3).

These tests pin the behaviour of :meth:`Database.connect` around
``pool_recycle``. The bug we are guarding against: a long-running
Auditarr process holding Postgres connections past the server's idle
timeout returns ``connection has been closed`` on the next request,
which surfaces to operators as "fails to fetch data after some hours of
uptime". The fix is to thread ``database_pool_recycle`` from settings
through into the engine kwargs for non-SQLite URLs; SQLite uses a
NullPool or StaticPool depending on the URL shape and has no
socket-lifetime concern, so the option must be omitted there.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.settings import Settings
from app.storage.database import Database


def _make_settings(database_url: str, **overrides: Any) -> Settings:
    """Build a Settings instance with a fixed database URL.

    We pass values directly to the Settings constructor (bypassing
    env vars) so the test doesn't depend on the ambient environment.
    """
    base: dict[str, Any] = {
        "database_url": database_url,
        "secret_key": "test-key-must-be-at-least-sixteen-chars",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_pool_recycle_applied_for_postgres_url() -> None:
    """Engines built against a Postgres URL must propagate
    ``pool_recycle``. SQLAlchemy exposes the configured value as
    ``engine.pool._recycle``."""
    settings = _make_settings(
        "postgresql+asyncpg://u:p@localhost:5432/db",
        database_pool_recycle=900,
    )
    db = Database(settings)
    await db.connect()
    try:
        # On a non-SQLite engine SQLAlchemy assembles a QueuePool whose
        # ``_recycle`` attribute reflects the kwarg we set.
        recycle = db.engine.pool._recycle
        assert recycle == 900
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_pool_recycle_omitted_for_sqlite_file_url(tmp_path) -> None:
    """SQLite engines use NullPool and have no socket lifetime concern.
    The recycle kwarg must NOT be applied — that would make SQLAlchemy
    warn about an unused option on NullPool.

    NullPool exposes ``_recycle`` too (inherited from the base Pool),
    but its value comes from the SQLAlchemy default (-1, meaning
    'never recycle'). We assert exactly that: our explicit value of
    900 was NOT pushed through."""
    db_file = tmp_path / "pool_test.db"
    settings = _make_settings(
        f"sqlite+aiosqlite:///{db_file}",
        database_pool_recycle=900,
    )
    db = Database(settings)
    await db.connect()
    try:
        recycle = db.engine.pool._recycle
        # -1 is SQLAlchemy's "never" sentinel. Anything other than the
        # 900 we configured is acceptable proof that the kwarg was
        # filtered out for SQLite.
        assert recycle != 900
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_pool_recycle_omitted_for_sqlite_memory_url() -> None:
    """The ``:memory:`` SQLite path uses StaticPool, which also has no
    socket-lifetime concern. Same expectation as the file-DB case."""
    settings = _make_settings(
        "sqlite+aiosqlite:///:memory:",
        database_pool_recycle=900,
    )
    db = Database(settings)
    await db.connect()
    try:
        recycle = db.engine.pool._recycle
        assert recycle != 900
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_pool_recycle_zero_disables_setting() -> None:
    """``database_pool_recycle=0`` (or any non-positive value) is an
    explicit "do not configure recycling" signal. The engine must come
    up without the kwarg attached."""
    settings = _make_settings(
        "postgresql+asyncpg://u:p@localhost:5432/db",
        database_pool_recycle=0,
    )
    db = Database(settings)
    await db.connect()
    try:
        recycle = db.engine.pool._recycle
        assert recycle != 0
        # Confirm we did NOT set the value — SQLAlchemy's default (-1)
        # is what we expect to see.
        assert recycle < 0
    finally:
        await db.disconnect()


def test_settings_default_pool_recycle_is_thirty_minutes() -> None:
    """Documents the chosen default (1800s == 30min). Catching a future
    accidental change in this constant guards against operators
    silently losing the long-uptime fix."""
    s = Settings(secret_key="test-key-must-be-at-least-sixteen-chars")
    assert s.database_pool_recycle == 1800
