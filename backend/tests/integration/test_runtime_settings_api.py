"""Tests for the runtime-settings CRUD endpoints (Stage 21).

Pin the contract:

- Admin-only on every endpoint (describe, list, set, clear).
- Whitelist-only writes: a 422 on any key not in RUNTIME_EDITABLE.
- Per-key validation: out-of-range / wrong-type values rejected.
- Persistence: writes survive a fresh service instance.
- Override delta: the list endpoint distinguishes overrides from
  env defaults via the ``is_override`` flag.
- Clear: removes the override and reverts to env default.
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
    db_path = tmp_path / "runtime_settings_api.db"
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


async def _login(client: AsyncClient, *, admin: bool) -> dict[str, str]:
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
async def test_describe_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/system/runtime-settings/describe")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_describe_requires_admin(client: AsyncClient) -> None:
    headers = await _login(client, admin=False)
    r = await client.get(
        "/api/v1/system/runtime-settings/describe", headers=headers
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_requires_admin(client: AsyncClient) -> None:
    headers = await _login(client, admin=False)
    r = await client.get("/api/v1/system/runtime-settings", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_requires_admin(client: AsyncClient) -> None:
    headers = await _login(client, admin=False)
    r = await client.put(
        "/api/v1/system/runtime-settings/log_level",
        headers=headers,
        json={"value": "debug"},
    )
    assert r.status_code == 403


# ── Describe contract ───────────────────────────────────────
@pytest.mark.asyncio
async def test_describe_returns_field_metadata(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.get(
        "/api/v1/system/runtime-settings/describe", headers=headers
    )
    assert r.status_code == 200
    fields = r.json()["fields"]
    by_key = {f["key"]: f for f in fields}
    # Spot-check a representative field carries every property the UI
    # needs to render the editor without hardcoding it.
    assert "log_level" in by_key
    log_level = by_key["log_level"]
    for k in ("label", "description", "category", "type", "default",
              "constraints", "impact"):
        assert k in log_level
    assert log_level["category"] == "logging"
    assert log_level["impact"] == "immediate"


@pytest.mark.asyncio
async def test_describe_includes_every_category(client: AsyncClient) -> None:
    """The UI groups by category; make sure every category we expect
    has at least one entry. Adding a new category to the schema
    without adding a field would surface as an empty tab in the UI,
    which is bad UX — this test makes that hard to do silently."""
    headers = await _login(client, admin=True)
    r = await client.get(
        "/api/v1/system/runtime-settings/describe", headers=headers
    )
    categories = {f["category"] for f in r.json()["fields"]}
    assert categories == {
        "logging", "auth", "rate_limiting", "scanner", "updater",
        "plugins", "housekeeping", "webhooks", "integrations",
        # Stage 4 (audit follow-up): the "dashboard" category was
        # added for ``dashboard_issue_min_severity``. Expanding the
        # set here is the deliberate change — the test exists
        # specifically to catch a NEW category arriving without a
        # field to fill it, which is the inverse failure mode.
        "dashboard",
    }


# ── List + override delta ────────────────────────────────────
@pytest.mark.asyncio
async def test_list_shows_env_defaults_initially(
    client: AsyncClient,
) -> None:
    headers = await _login(client, admin=True)
    r = await client.get("/api/v1/system/runtime-settings", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Nothing customized → every field is is_override=False
    for key, entry in body.items():
        assert entry["is_override"] is False, key
        assert "value" in entry
        assert "env_default" in entry


# ── Write CRUD ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_override_and_clear(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)

    # Set
    r = await client.put(
        "/api/v1/system/runtime-settings/access_token_ttl_minutes",
        headers=headers,
        json={"value": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "key": "access_token_ttl_minutes",
        "value": 5,
        "is_override": True,
    }

    # List reflects the override
    r = await client.get("/api/v1/system/runtime-settings", headers=headers)
    listing = r.json()
    assert listing["access_token_ttl_minutes"]["value"] == 5
    assert listing["access_token_ttl_minutes"]["is_override"] is True

    # Clear
    r = await client.delete(
        "/api/v1/system/runtime-settings/access_token_ttl_minutes",
        headers=headers,
    )
    assert r.status_code == 204

    # List shows env default again
    r = await client.get("/api/v1/system/runtime-settings", headers=headers)
    listing = r.json()
    assert listing["access_token_ttl_minutes"]["is_override"] is False


@pytest.mark.asyncio
async def test_set_invalid_value_returns_422(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/runtime-settings/access_token_ttl_minutes",
        headers=headers,
        json={"value": 99999},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_unknown_key_returns_422_with_env_hint(
    client: AsyncClient,
) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/runtime-settings/secret_key",
        headers=headers,
        json={"value": "anything"},
    )
    assert r.status_code == 422
    # Error message should guide the operator to the env file —
    # without that, they don't know how to change the setting they
    # were trying to change.
    assert "env file" in r.text or "not a runtime-editable" in r.text


@pytest.mark.asyncio
async def test_clear_unknown_key_returns_422(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.delete(
        "/api/v1/system/runtime-settings/secret_key", headers=headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_clear_nonexistent_override_is_idempotent(
    client: AsyncClient,
) -> None:
    """Clearing a key that has no override row is a no-op, not an
    error — the operator's mental model is "there's no override
    here" which is satisfied either way."""
    headers = await _login(client, admin=True)
    r = await client.delete(
        "/api/v1/system/runtime-settings/log_level", headers=headers
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_overrides_persist_across_requests(
    client: AsyncClient,
) -> None:
    """The override is in the DB, so a fresh request sees it.
    This is the basic "did we actually persist" check."""
    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
        json={"value": 8},
    )
    # Fresh GET — should see the override.
    r = await client.get(
        "/api/v1/system/runtime-settings", headers=headers
    )
    body = r.json()
    assert body["scanner_worker_concurrency"]["value"] == 8
    assert body["scanner_worker_concurrency"]["is_override"] is True


# ── Stage 2: audit log + history endpoint ─────────────────────


@pytest.mark.asyncio
async def test_set_writes_audit_row(client: AsyncClient) -> None:
    """One PUT → one row in the history endpoint, capturing the
    new value and recording None as the previous value (because the
    field was at env default)."""
    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
        json={"value": 4},
    )
    r = await client.get(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency/history",
        headers=headers,
    )
    assert r.status_code == 200
    changes = r.json()["changes"]
    assert len(changes) == 1
    assert changes[0]["key"] == "scanner_worker_concurrency"
    assert changes[0]["prev_value"] is None
    assert changes[0]["next_value"] == 4
    assert changes[0]["set_by_user_id"] is not None


@pytest.mark.asyncio
async def test_repeated_sets_record_prev_value(
    client: AsyncClient,
) -> None:
    """Second PUT captures the previous override value, not None."""
    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
        json={"value": 4},
    )
    await client.put(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
        json={"value": 8},
    )
    r = await client.get(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency/history",
        headers=headers,
    )
    changes = r.json()["changes"]
    assert len(changes) == 2
    # Newest first ordering.
    assert changes[0]["prev_value"] == 4
    assert changes[0]["next_value"] == 8
    assert changes[1]["prev_value"] is None
    assert changes[1]["next_value"] == 4


@pytest.mark.asyncio
async def test_clear_writes_audit_row_with_null_next_value(
    client: AsyncClient,
) -> None:
    """A DELETE that actually removes an override appends a row with
    ``next_value = null``. A DELETE on an already-default field is a
    no-op and does NOT write an audit row."""
    headers = await _login(client, admin=True)
    # Set, then clear.
    await client.put(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
        json={"value": 4},
    )
    await client.delete(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
    )
    r = await client.get(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency/history",
        headers=headers,
    )
    changes = r.json()["changes"]
    assert len(changes) == 2
    assert changes[0]["prev_value"] == 4
    assert changes[0]["next_value"] is None  # cleared back to default

    # Now clear again — already-default; no new row.
    await client.delete(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency",
        headers=headers,
    )
    r = await client.get(
        "/api/v1/system/runtime-settings/scanner_worker_concurrency/history",
        headers=headers,
    )
    assert len(r.json()["changes"]) == 2  # no new row added


@pytest.mark.asyncio
async def test_history_requires_admin(client: AsyncClient) -> None:
    """Non-admin sees 403 on the history endpoint, same posture as
    the rest of the runtime-settings surface."""
    headers = await _login(client, admin=False)
    r = await client.get(
        "/api/v1/system/runtime-settings/log_level/history", headers=headers
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_history_rejects_unknown_key(client: AsyncClient) -> None:
    """A typo'd key gets a 422 with an explanation, not a silent
    empty list (which would mask the typo)."""
    headers = await _login(client, admin=True)
    r = await client.get(
        "/api/v1/system/runtime-settings/not_a_real_setting/history",
        headers=headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_describe_emits_stage2_fields(client: AsyncClient) -> None:
    """The describe endpoint includes the three Stage 2 metadata
    fields on every entry."""
    headers = await _login(client, admin=True)
    r = await client.get(
        "/api/v1/system/runtime-settings/describe", headers=headers
    )
    assert r.status_code == 200
    fields = r.json()["fields"]
    for entry in fields:
        assert "group" in entry
        assert "sensitivity" in entry
        assert "restart_required" in entry
        assert entry["sensitivity"] in ("normal", "elevated")
        assert isinstance(entry["restart_required"], bool)
    # Spot-check a known grouping.
    by_key = {e["key"]: e for e in fields}
    assert by_key["access_token_ttl_minutes"]["group"] == "tokens"
    assert by_key["housekeeping_delivery_retention_days"]["group"] == "retention"
