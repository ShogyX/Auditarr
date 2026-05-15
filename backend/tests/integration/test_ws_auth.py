"""WebSocket auth tests (Stage 14)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.events.bus import get_event_bus
from app.main import create_app
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def app_and_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple]:
    """Boot the app and return ``(app, access_token)``.

    Stage 14: we seed schema + admin from the running pytest_asyncio
    event loop, then hand a Starlette ``TestClient`` to the test body.
    Mixing ``asyncio.run`` with pytest_asyncio's running loop breaks in
    the full suite — see the audit notes in the Stage 14 changelog.
    """
    db_path = tmp_path / "ws_auth.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_WS_REQUIRE_AUTH", "true")

    from app.core.settings import get_settings

    get_settings.cache_clear()

    # Conftest's autouse ``_reset_database_singleton`` already nulled
    # the module-level singleton, so this ``get_database()`` returns
    # a fresh instance bound to the env we just set.
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await db.disconnect()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    app = create_app()
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "ws@example.com",
                    "username": "wsuser",
                    "password": PASSWORD,
                },
            )
            assert r.status_code == 201, r.text
            login = client.post(
                "/api/v1/auth/login",
                json={"login": "wsuser", "password": PASSWORD},
            )
            access_token = login.json()["access_token"]
            yield app, access_token
    finally:
        db._engine = None  # noqa: SLF001
        db._sessionmaker = None  # noqa: SLF001
        await db.connect()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ws_rejects_missing_token(app_and_token) -> None:
    """A connection without ``?token=...`` is closed with 1008."""
    app, _ = app_and_token

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/api/v1/ws"):
                pass
        assert excinfo.value.code == 1008


@pytest.mark.asyncio
async def test_ws_rejects_invalid_token(app_and_token) -> None:
    app, _ = app_and_token

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/api/v1/ws?token=garbage"):
                pass
        assert excinfo.value.code == 1008


@pytest.mark.asyncio
async def test_ws_accepts_valid_token(app_and_token) -> None:
    """A connection with a valid access token is accepted."""
    app, token = app_and_token

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
            ws.close()


@pytest.mark.asyncio
async def test_ws_no_auth_allowed_when_setting_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting ``AUDITARR_WS_REQUIRE_AUTH=false`` skips the check."""
    db_path = tmp_path / "ws_noauth.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_WS_REQUIRE_AUTH", "false")

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
    await db.disconnect()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    app = create_app()
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/ws") as ws:
                ws.close()
    finally:
        db._engine = None  # noqa: SLF001
        db._sessionmaker = None  # noqa: SLF001
        await db.connect()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()
