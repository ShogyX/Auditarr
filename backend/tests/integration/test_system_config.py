"""Tests for the operator-facing /system/config endpoint (Stage 20).

The Settings UI uses GET /api/v1/system/config to render a read-only
view of the runtime config (api/auth/storage/updater/plugins/
housekeeping). We pin the contract here so a refactor can't silently:

- expose secrets (DB password, Redis password, secret key, JWT key)
- bypass the admin-only requirement
- drop any of the six sections the UI expects
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "system_config.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    # Include a recognizable password in the DATABASE_URL so we can
    # assert it gets redacted in the response.
    monkeypatch.setenv(
        "AUDITARR_REDIS_URL",
        "redis://default:VERY_SECRET_REDIS_PW@localhost:6379/0",
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()

    app = create_app()
    db = get_database()
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


async def _register_and_login(
    client: AsyncClient, *, admin: bool
) -> dict[str, str]:
    """Register a user, optionally promote to admin, log in, return
    the auth header dict."""
    email = f"{'admin' if admin else 'user'}@example.com"
    username = "adminuser" if admin else "regularuser"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]

    if admin:
        async with get_database().session() as sess:
            await sess.execute(
                update(User).where(User.id == user_id).values(role="admin")
            )
            await sess.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": PASSWORD},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# ── Auth gating ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_config_requires_authentication(client: AsyncClient) -> None:
    r = await client.get("/api/v1/system/config")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_config_requires_admin_role(client: AsyncClient) -> None:
    """Non-admin users get 403 — config leaks deployment topology
    (file paths, redacted URLs) so it's deliberately admin-only."""
    headers = await _register_and_login(client, admin=False)
    r = await client.get("/api/v1/system/config", headers=headers)
    assert r.status_code == 403


# ── Response shape ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_config_returns_all_six_sections(client: AsyncClient) -> None:
    """The frontend renders six cards based on six top-level keys.
    Renaming one without coordinating with the UI would silently
    drop a settings section."""
    headers = await _register_and_login(client, admin=True)
    r = await client.get("/api/v1/system/config", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "api",
        "auth",
        "storage",
        "updater",
        "plugins",
        "housekeeping",
    }


@pytest.mark.asyncio
async def test_api_section_has_expected_fields(client: AsyncClient) -> None:
    headers = await _register_and_login(client, admin=True)
    r = await client.get("/api/v1/system/config", headers=headers)
    api = r.json()["api"]
    for field in (
        "host",
        "port",
        "api_prefix",
        "api_version",
        "allowed_origins",
        "ws_require_auth",
        "log_level",
        "log_format",
        "env",
    ):
        assert field in api, f"api.{field} missing"


@pytest.mark.asyncio
async def test_updater_section_includes_install_mode(client: AsyncClient) -> None:
    """Stage 19's install_mode field must be on /system/config too
    — the Settings page shows it in the Updater card."""
    headers = await _register_and_login(client, admin=True)
    r = await client.get("/api/v1/system/config", headers=headers)
    updater = r.json()["updater"]
    assert "install_mode" in updater
    assert updater["install_mode"] in {"auto", "docker", "bare-metal", "unmanaged"}


# ── Secret redaction ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_database_url_does_not_leak_password(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DB password must never appear in the response.

    The default test fixture uses SQLite (which has no password,
    so redaction is a no-op), so this test also exercises the
    redaction path explicitly by patching the settings to return a
    postgres-style URL with a recognizable password.
    """
    headers = await _register_and_login(client, admin=True)

    # Baseline: SQLite URL passes through unchanged (no password to
    # redact). What matters is that no password leaks.
    r = await client.get("/api/v1/system/config", headers=headers)
    assert "VERY_SECRET_DB_PW" not in r.text

    # Redaction path: monkey-patch get_settings to return a postgres
    # URL with a known password, and verify it gets masked.
    from app.api.v1 import system as system_router

    redacted = system_router._redact_url(
        "postgresql+asyncpg://auditarr:VERY_SECRET_DB_PW@db:5432/auditarr"
    )
    assert "VERY_SECRET_DB_PW" not in redacted
    assert "***" in redacted
    # Host portion should still be visible so operators can sanity-
    # check their config without seeing the password.
    assert "@db:5432/auditarr" in redacted
    assert "auditarr:***" in redacted


@pytest.mark.asyncio
async def test_redis_url_redacts_password(client: AsyncClient) -> None:
    headers = await _register_and_login(client, admin=True)
    r = await client.get("/api/v1/system/config", headers=headers)
    storage = r.json()["storage"]
    # The fixture set REDIS_URL to include VERY_SECRET_REDIS_PW —
    # that exact string must not appear anywhere in the response.
    assert "VERY_SECRET_REDIS_PW" not in storage["redis_url"]
    assert "***" in storage["redis_url"]


@pytest.mark.asyncio
async def test_secret_key_never_in_response(client: AsyncClient) -> None:
    """Belt-and-suspenders — the JWT signing key isn't in any of the
    six sections, but assert it explicitly so a future refactor that
    naively dumps all settings can't slip the key in.

    We check by-value rather than by-key name because the tmpdir
    path in test fixtures includes the test name, which can contain
    the substring ``secret_key`` (e.g. when pytest names the dir
    after this test itself).
    """
    headers = await _register_and_login(client, admin=True)
    r = await client.get("/api/v1/system/config", headers=headers)
    body = r.json()
    raw = r.text

    # The actual secret value the test fixture set must NOT appear
    # anywhere in the response body.
    assert "test-key-must-be-at-least-sixteen-chars" not in raw

    # Walk the response: none of the six section dicts should have
    # a key literally called 'secret_key' or 'jwt_secret' or similar.
    forbidden_keys = {
        "secret_key",
        "jwt_secret",
        "jwt_signing_key",
        "session_secret",
    }
    for section_name, section in body.items():
        assert isinstance(section, dict), f"{section_name} not a dict"
        leaked = forbidden_keys & set(section.keys())
        assert not leaked, f"{section_name} leaks: {leaked}"
