"""``UserPublic`` email permissiveness regression tests (Stage 1 / L1).

Background: Pydantic's ``EmailStr`` rejects hostname-only addresses
such as ``admin@localhost`` because the underlying ``email-validator``
package requires a public-suffix TLD by default. The Auditarr bootstrap
path happily stored such an email on a fresh install when the operator
set ``AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@localhost``, but then every
single ``GET /api/v1/auth/me`` 500-ed on the response side because the
``UserPublic`` response model validated the column on its way out.

Stage 1 replaced ``EmailStr`` on ``UserPublic`` and
``ProfileUpdateRequest`` with a permissive local@host validator while
keeping strict ``EmailStr`` on ``RegisterRequest`` and
``PasswordResetRequest``. These tests pin that contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.main import create_app
from app.schemas.auth import (
    ProfileUpdateRequest,
    RegisterRequest,
    UserPublic,
)
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


# ── Pydantic round-trip ──────────────────────────────────────────
@pytest.mark.parametrize(
    "email",
    [
        "admin@localhost",
        "admin@auditarr.local",
        "foo@example.com",
        "user.name@mail.internal",
        "ops+tag@host",
    ],
)
def test_user_public_accepts_permissive_emails(email: str) -> None:
    """The shapes that previously 500-ed must round-trip cleanly."""
    import datetime as dt

    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": email,
        "username": "admin",
        "full_name": None,
        "role": "admin",
        "is_active": True,
        "is_verified": True,
        "created_at": dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        "last_login_at": None,
    }
    user = UserPublic.model_validate(payload)
    assert user.email == email


@pytest.mark.parametrize(
    "bad_email",
    [
        "",                # empty
        "no-at-sign",      # missing @
        " admin@localhost", # leading whitespace
        "admin@localhost ", # trailing whitespace
        "admin @host",     # embedded whitespace in local part
        "admin@",          # empty host
        "@host",           # empty local part
        "admin@@host",     # double @
    ],
)
def test_user_public_rejects_obviously_bad_emails(bad_email: str) -> None:
    """The permissive validator is permissive about TLDs, not about
    actual syntactic garbage."""
    import datetime as dt

    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": bad_email,
        "username": "admin",
        "full_name": None,
        "role": "admin",
        "is_active": True,
        "is_verified": True,
        "created_at": dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        "last_login_at": None,
    }
    with pytest.raises(ValidationError):
        UserPublic.model_validate(payload)


def test_profile_update_request_accepts_admin_at_localhost() -> None:
    """``PATCH /auth/me`` must accept ``admin@localhost`` so an operator
    can edit other profile fields without being forced to first migrate
    their email to a TLD-bearing address."""
    body = ProfileUpdateRequest.model_validate({"email": "admin@localhost"})
    assert body.email == "admin@localhost"


def test_register_request_still_rejects_admin_at_localhost() -> None:
    """Registration is the one path where we keep strict ``EmailStr``.
    Self-registering operators are using real addresses; the bootstrap
    quirk doesn't apply there."""
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            {
                "email": "admin@localhost",
                "username": "alice",
                "password": "supersecret-password-1!",
            }
        )


# ── Live ``GET /auth/me`` against a hostname-only-email user ─────
@pytest_asyncio.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    """Fresh in-memory SQLite app per test."""
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
        except Exception:
            pass  # best-effort cleanup in test helper
        bus.clear()


PASSWORD = "supersecret-password-1!"


async def _create_admin_with_email(email: str) -> None:
    """Insert an admin user directly with the chosen email value.

    We bypass the register endpoint because that endpoint still uses
    strict ``EmailStr`` (intentionally — see
    ``test_register_request_still_rejects_admin_at_localhost`` above).
    The hostname-only-email state exists because the bootstrap path
    writes directly to the model.
    """
    from app.models.user import User
    from app.security import Role, hash_password

    async with get_database().session() as sess:
        sess.add(
            User(
                email=email,
                username="bootstrapadmin",
                full_name="Bootstrap Admin",
                password_hash=hash_password(PASSWORD),
                role=Role.ADMIN.value,
                is_active=True,
                is_verified=True,
            )
        )
        await sess.commit()


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_auth_me_returns_200_for_admin_at_localhost(
    auth_client: AsyncClient,
) -> None:
    """The smoke test that proves the L1 regression is fixed."""
    await _create_admin_with_email("admin@localhost")
    headers = await _login(auth_client, "bootstrapadmin")
    r = await auth_client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "admin@localhost"


@pytest.mark.asyncio
async def test_auth_me_returns_200_for_auditarr_local(
    auth_client: AsyncClient,
) -> None:
    """``admin@auditarr.local`` was the bootstrap default — also covered."""
    await _create_admin_with_email("admin@auditarr.local")
    headers = await _login(auth_client, "bootstrapadmin")
    r = await auth_client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "admin@auditarr.local"
