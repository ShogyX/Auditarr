"""Stage 13 security tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.main import create_app
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "security.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


# ── Headers ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_security_headers_on_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    h = response.headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    assert h.get("referrer-policy") == "no-referrer"
    csp = h.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    pp = h.get("permissions-policy", "")
    assert "camera=()" in pp
    assert "microphone=()" in pp


@pytest.mark.asyncio
async def test_hsts_absent_in_dev(client: AsyncClient) -> None:
    """HSTS must not fire in dev — would pin localhost to HTTPS forever."""
    response = await client.get("/api/v1/health")
    assert response.headers.get("strict-transport-security") is None


# ── Rate limiting ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_login_rate_limit_kicks_in(client: AsyncClient) -> None:
    """After ``auth_rate_limit_attempts`` failures, /login returns 429."""
    # Make the limit tiny so the test stays fast.
    import os

    os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"] = "3"
    os.environ["AUDITARR_AUTH_RATE_LIMIT_WINDOW_SECONDS"] = "60"
    from app.core.settings import get_settings

    get_settings.cache_clear()

    try:
        # 3 attempts: any combination of right/wrong should count.
        for _ in range(3):
            await client.post(
                "/api/v1/auth/login",
                json={"login": "nobody", "password": "wrong"},
            )
        # The 4th attempt is rate-limited regardless of correctness.
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": "nobody", "password": "wrong"},
        )
        assert response.status_code == 429
        body = response.json()
        # Detail body carries ``retry_after``.
        assert "retry_after" in str(body)
    finally:
        del os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"]
        del os.environ["AUDITARR_AUTH_RATE_LIMIT_WINDOW_SECONDS"]
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_register_rate_limit_kicks_in(client: AsyncClient) -> None:
    import os

    os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"] = "2"
    os.environ["AUDITARR_AUTH_RATE_LIMIT_WINDOW_SECONDS"] = "60"
    from app.core.settings import get_settings

    get_settings.cache_clear()

    try:
        for i in range(2):
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"e{i}@x.com",
                    "username": f"u{i}",
                    "password": "supersecret-password-1!",
                },
            )
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "e2@x.com",
                "username": "u2",
                "password": "supersecret-password-1!",
            },
        )
        assert response.status_code == 429
    finally:
        del os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"]
        del os.environ["AUDITARR_AUTH_RATE_LIMIT_WINDOW_SECONDS"]
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_rate_limit_disabled_when_attempts_is_zero(
    client: AsyncClient,
) -> None:
    """Operators set attempts=0 to opt out of rate limiting entirely."""
    import os

    os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"] = "0"
    from app.core.settings import get_settings

    get_settings.cache_clear()

    try:
        # 50 failed logins should never trip the limit.
        last = None
        for _ in range(50):
            last = await client.post(
                "/api/v1/auth/login",
                json={"login": "nobody", "password": "wrong"},
            )
        assert last is not None
        assert last.status_code != 429
    finally:
        del os.environ["AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS"]
        get_settings.cache_clear()
