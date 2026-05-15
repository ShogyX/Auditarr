"""Tests for PATCH /auth/me — profile updates (Stage 21).

Pins:

- Authenticated users can update their own email + full_name.
- Username changes are NOT exposed by this endpoint (audit-log
  attribution is preserved).
- Passwords are NOT exposed by this endpoint (password change has
  its own current-password-confirmation flow).
- Email collisions with another account are rejected.
- Empty full_name clears the field.
- Changing email resets the is_verified flag.
- The audit log records the field names changed, but not the values.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.user import User
from app.storage.base import Base
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "auth_profile.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
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
    client: AsyncClient,
    *,
    username: str = "alice",
    email: str = "alice@example.com",
    full_name: str | None = "Alice Original",
) -> dict[str, str]:
    payload = {
        "email": email, "username": username, "password": PASSWORD,
    }
    if full_name is not None:
        payload["full_name"] = full_name
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 201, r.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": PASSWORD},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# ── Auth ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_patch_me_requires_auth(client: AsyncClient) -> None:
    r = await client.patch("/api/v1/auth/me", json={"full_name": "Anyone"})
    assert r.status_code == 401


# ── Field updates ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_update_full_name(client: AsyncClient) -> None:
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"full_name": "Alice Renamed"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Alice Renamed"


@pytest.mark.asyncio
async def test_update_email(client: AsyncClient) -> None:
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"email": "alice.new@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "alice.new@example.com"
    # Email change resets is_verified — operator must re-verify.
    assert r.json()["is_verified"] is False


@pytest.mark.asyncio
async def test_partial_update_leaves_other_fields_alone(
    client: AsyncClient,
) -> None:
    """Updating full_name MUST NOT clobber email."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"full_name": "Alice Renamed"},
    )
    body = r.json()
    assert body["full_name"] == "Alice Renamed"
    assert body["email"] == "alice@example.com"  # unchanged


@pytest.mark.asyncio
async def test_empty_full_name_clears_field(client: AsyncClient) -> None:
    """Operators who want to clear their display name pass an empty
    string. The service normalizes that to NULL on the column."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"full_name": ""},
    )
    assert r.status_code == 200
    assert r.json()["full_name"] is None


# ── Forbidden fields ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_username_not_editable(client: AsyncClient) -> None:
    """Usernames appear in audit logs — renaming in place would
    break historical attribution. The schema forbids the field
    (extra='forbid'), so unknown fields produce 422."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"username": "renamed"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_password_not_editable_via_this_endpoint(
    client: AsyncClient,
) -> None:
    """Password change has its own current-password-confirmation
    flow; passing 'password' here is rejected as extra-forbid."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"password": "newpassword-1234"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_role_not_editable(client: AsyncClient) -> None:
    """A user shouldn't be able to escalate themselves to admin
    through profile updates."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"role": "admin"},
    )
    assert r.status_code == 422
    # Confirm the role really hasn't changed.
    async with get_database().session() as sess:
        user = (
            await sess.execute(
                select(User).where(User.username == "alice")
            )
        ).scalar_one()
        assert user.role != "admin"


# ── Email collision ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_email_collision_returns_422(client: AsyncClient) -> None:
    """Trying to take another account's email must be rejected
    cleanly rather than crashing on a unique-constraint violation."""
    # Register two users.
    await _register_and_login(
        client, username="bob", email="bob@example.com",
    )
    headers = await _register_and_login(
        client, username="alice", email="alice@example.com",
    )
    # Alice tries to take Bob's email.
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"email": "bob@example.com"},
    )
    assert r.status_code == 422
    assert "email" in r.text.lower()


@pytest.mark.asyncio
async def test_updating_to_own_current_email_is_noop(
    client: AsyncClient,
) -> None:
    """Re-submitting the current email is a no-op (not a collision)."""
    headers = await _register_and_login(client)
    r = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"email": "alice@example.com"},
    )
    assert r.status_code == 200


# ── Audit trail ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_profile_update_recorded_in_audit_log(
    client: AsyncClient,
) -> None:
    """Profile edits leave an audit trail naming the changed fields
    but NOT their values — so an attacker who reads the audit log
    later can't see the operator's email."""
    headers = await _register_and_login(client)
    await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"full_name": "Alice Renamed"},
    )
    async with get_database().session() as sess:
        rows = (
            await sess.execute(
                select(AuditLogEntry).where(
                    AuditLogEntry.action == "auth.profile_updated"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        # The metadata field names the changed columns ...
        meta = row.metadata_ or {}
        assert "fields" in meta
        assert "full_name" in meta["fields"]
        # ... but does NOT carry the new value.
        flattened = repr(meta)
        assert "Alice Renamed" not in flattened
