"""Pytest fixtures shared across the backend test suite."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Force a deterministic environment BEFORE app imports.
os.environ.setdefault("AUDITARR_ENV", "test")
os.environ.setdefault("AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars")
os.environ.setdefault(
    "AUDITARR_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)
os.environ.setdefault("AUDITARR_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("AUDITARR_PLUGIN_DIR", str(Path("/tmp/auditarr-test-plugins")))


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client wired into a freshly-created FastAPI app.

    Note: the lifespan context isn't entered for unit-style tests — endpoints
    that depend on database/redis state should use the integration fixtures.
    """
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def settings_reset() -> Iterator[None]:
    """Clear the lru_cache so env mutations between tests are picked up."""
    from app.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """Auto-reset the in-process rate limiter between every test.

    Stage 13 added rate limiting on ``/auth/login`` and ``/auth/register``
    (default: 10 attempts per 5 minutes per client IP). Pytest's test
    client always reports the same client host, so without this reset
    the integration suite would trip the limit after ~10 tests.
    """
    from app.security.rate_limit import get_rate_limiter

    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()


@pytest.fixture(autouse=True)
def _reset_database_singleton() -> Iterator[None]:
    """Drop the cached :class:`Database` singleton between tests.

    Stage 14 audit uncovered that ``get_database()`` captures
    ``Settings`` at first instantiation — ``self._settings = settings``
    is bound once and never re-read. Tests that ``monkeypatch.setenv``
    a different ``AUDITARR_DATABASE_URL`` and then call
    ``get_settings.cache_clear()`` were getting a *stale* engine pointed
    at whatever URL the very first test in the session had configured.

    They appeared to pass because the suite-wide default is
    ``sqlite+aiosqlite:///:memory:`` and each fixture re-runs
    ``Base.metadata.create_all`` in that shared in-memory DB. As soon
    as any fixture switched to a ``tmp_path`` file DB (Stage 14's WS
    auth tests did), the staleness surfaced as "no such table: users".

    Resetting the module-level ``_db`` global between tests forces
    ``get_database()`` to rebuild the Database with the current
    settings on the next call. The fixture clears settings cache too
    so the rebuild reads fresh env vars.
    """
    from app.core.settings import get_settings
    from app.storage import database as db_module

    get_settings.cache_clear()
    db_module._db = None  # noqa: SLF001
    yield
    db_module._db = None  # noqa: SLF001
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_virustotal_quota() -> Iterator[None]:
    """Stage 10 (addendum C.5) — auto-reset the VT quota state
    between every test.

    The VT plugin's ``_quota`` is a module-level singleton (per
    plan §514: "rate-limiting state stays as a process-wide
    singleton"). Without this autouse fixture, a test that
    burns lookups against the daily cap would leak counter
    state into subsequent tests, making them flaky.

    Resets all three windows (per-minute, per-day, per-month)
    + the per-window alert flags + the last-check timestamp.
    """
    # Import locally so this fixture doesn't crash test
    # collection on environments where the plugin module isn't
    # importable (e.g. partial repos during refactors). In
    # practice the plugin is always present in CI.
    try:
        from plugins.virustotal.backend import reset_quota_for_tests
    except ImportError:
        yield
        return
    reset_quota_for_tests()
    yield
    reset_quota_for_tests()
