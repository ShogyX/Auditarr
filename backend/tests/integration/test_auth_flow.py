"""End-to-end auth tests.

These run against an in-memory SQLite database that is initialized once per
test using ``Base.metadata.create_all`` (no Alembic in tests — Alembic is
verified separately by the migration test).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.main import create_app
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


@pytest_asyncio.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    """A fully-bootstrapped client with database tables created."""
    # Reset the global singletons so tests don't share state.
    get_settings.cache_clear()
    db = get_database()
    redis = get_redis()
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await redis.disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()


PASSWORD = "supersecret-password-1!"


async def _register_admin(client: AsyncClient) -> dict:
    """Register a user and promote them to admin via direct DB write."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
            "full_name": "The Admin",
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()

    # Promote to admin out-of-band.
    from sqlalchemy import update

    from app.models.user import User
    from app.storage.database import get_database

    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    return user


@pytest.mark.asyncio
async def test_register_login_me_logout(auth_client: AsyncClient) -> None:
    # Register
    response = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "username": "alice",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    assert response.json()["username"] == "alice"

    # Login
    response = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "alice", "password": PASSWORD},
    )
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] > 0

    # Authenticated /me
    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"

    # Logout invalidates the refresh
    response = await auth_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert response.status_code == 204

    # Refresh after logout fails
    response = await auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation(auth_client: AsyncClient) -> None:
    await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "username": "bob",
            "password": PASSWORD,
        },
    )
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "bob", "password": PASSWORD},
    )
    tokens = login.json()

    # First refresh succeeds and yields a new pair
    rotated = await auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Old refresh is now revoked
    replay = await auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_change_password_invalidates_tokens(
    auth_client: AsyncClient,
) -> None:
    await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "carol@example.com",
            "username": "carol",
            "password": PASSWORD,
        },
    )
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "carol", "password": PASSWORD},
    )
    tokens = login.json()

    NEW = "even-more-secret-password-2!"
    change = await auth_client.post(
        "/api/v1/auth/password/change",
        headers={"authorization": f"Bearer {tokens['access_token']}"},
        json={"current_password": PASSWORD, "new_password": NEW},
    )
    assert change.status_code == 204

    # Old access token is now invalid
    me_after = await auth_client.get(
        "/api/v1/auth/me",
        headers={"authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_after.status_code == 401

    # New password works
    relogin = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "carol", "password": NEW},
    )
    assert relogin.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_flow(auth_client: AsyncClient) -> None:
    await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "dave@example.com",
            "username": "dave",
            "password": PASSWORD,
        },
    )

    # Request reset always returns 202, even for unknown emails
    response = await auth_client.post(
        "/api/v1/auth/password/reset/request",
        json={"email": "dave@example.com"},
    )
    assert response.status_code == 202
    response = await auth_client.post(
        "/api/v1/auth/password/reset/request",
        json={"email": "ghost@example.com"},
    )
    assert response.status_code == 202

    # Pull the reset token straight from the DB (the email backend in tests is
    # the console provider, which doesn't expose links).
    from sqlalchemy import select

    from app.models.password_reset import PasswordResetToken
    from app.models.user import User
    from app.storage.database import get_database

    async with get_database().session() as sess:
        user = (
            await sess.execute(select(User).where(User.username == "dave"))
        ).scalar_one()
        rec = (
            await sess.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id
                )
            )
        ).scalar_one()
        # Confirm the reset record exists and is unused.
        assert rec.used_at is None

    # The token itself is unrecoverable from the DB (we hash it). Build a
    # confirmation that fails on a tampered token to prove validation.
    bad = await auth_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": "definitely-wrong-token", "new_password": "thisisalongpassword!"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_admin_only_audit_log(auth_client: AsyncClient) -> None:
    admin = await _register_admin(auth_client)
    assert admin["username"] == "admin"
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    response = await auth_client.get("/api/v1/audit/log", headers=headers)
    assert response.status_code == 200
    actions = {row["action"] for row in response.json()}
    assert "auth.login" in actions
    assert "auth.register" in actions

    # Non-admin
    await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "ed@example.com",
            "username": "ed",
            "password": PASSWORD,
        },
    )
    user_login = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "ed", "password": PASSWORD},
    )
    response = await auth_client.get(
        "/api/v1/audit/log",
        headers={"authorization": f"Bearer {user_login.json()['access_token']}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_invalid_credentials_rejected(auth_client: AsyncClient) -> None:
    await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "frank@example.com",
            "username": "frank",
            "password": PASSWORD,
        },
    )
    response = await auth_client.post(
        "/api/v1/auth/login",
        json={"login": "frank", "password": "wrong-wrong-wrong"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_protected_endpoint_requires_token(
    auth_client: AsyncClient,
) -> None:
    response = await auth_client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_short_password_rejected(auth_client: AsyncClient) -> None:
    response = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "gabe@example.com",
            "username": "gabe",
            "password": "tooshort",
        },
    )
    assert response.status_code == 422
