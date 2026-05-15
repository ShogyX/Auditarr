"""Tests for the encrypted-secrets endpoints (Stage 21).

These pin the core safety promise of the secrets surface: the
plaintext value MUST NEVER be visible via any API response, audit
log entry, or error message. A regression here would mean an
operator's VirusTotal key (or whatever future secret we manage)
could leak in a way that's hard to detect after the fact.

Pinned contracts:

- Admin-only writes; non-admin reads also forbidden (the existence
  of a secret is operationally sensitive).
- The plaintext sent in a PUT body never echoes back in any response.
- The GET response carries metadata only: ``has_value``, ``last_set_at``,
  optionally ``last_tested_at`` / ``last_test_ok`` / ``last_test_detail``.
- Length bounds (32..128 for virustotal_api_key) enforced.
- Unknown secret keys rejected as 422.
- Clear works idempotently; test endpoint requires a stored value.
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
# A recognizable plaintext we can grep for. 40 chars = within the
# 32..128 bound for virustotal_api_key.
PLAINTEXT_SECRET = "supersecret-virustotal-key-DO-NOT-LEAK-A"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "secrets_api.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings
    from app.security.secrets import reset_secret_box

    get_settings.cache_clear()
    reset_secret_box()

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
    reset_secret_box()


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
async def test_list_requires_admin(client: AsyncClient) -> None:
    headers = await _login(client, admin=False)
    r = await client.get("/api/v1/system/secrets", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_set_requires_admin(client: AsyncClient) -> None:
    headers = await _login(client, admin=False)
    r = await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    assert r.status_code == 403


# ── The big one: plaintext never leaks ───────────────────────
@pytest.mark.asyncio
async def test_plaintext_never_appears_in_list_response(
    client: AsyncClient,
) -> None:
    """After setting a secret, the list endpoint must not contain
    the plaintext anywhere in its response body."""
    headers = await _login(client, admin=True)

    r = await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    assert r.status_code == 204, r.text

    r = await client.get("/api/v1/system/secrets", headers=headers)
    assert r.status_code == 200
    raw = r.text
    assert PLAINTEXT_SECRET not in raw
    # Also assert it's not present in any sneaky alternate encoding —
    # base64'd, hex'd, etc. We only test base64 because that's the
    # main accidental leak path (an encrypt that forgets to actually
    # encrypt).
    import base64
    b64 = base64.b64encode(PLAINTEXT_SECRET.encode()).decode()
    assert b64 not in raw


@pytest.mark.asyncio
async def test_set_response_is_empty(client: AsyncClient) -> None:
    """The 204 on PUT means no body — the secret can't echo back if
    there's nothing to echo. Pin that."""
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    assert r.status_code == 204
    assert r.text == ""


@pytest.mark.asyncio
async def test_list_metadata_shape_after_set(client: AsyncClient) -> None:
    """The UI uses the metadata fields to render "set / unset / last
    tested" UI. Pin every field it reads."""
    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    r = await client.get("/api/v1/system/secrets", headers=headers)
    secrets = r.json()["secrets"]
    by_key = {s["key"]: s for s in secrets}
    vt = by_key["virustotal_api_key"]
    assert vt["has_value"] is True
    assert vt["last_set_at"] is not None
    # Initial set: no test yet.
    assert vt["last_tested_at"] is None
    assert vt["last_test_ok"] is None
    # Field exists even when no value has ever been set — confirm by
    # rendering on a fresh fixture if we needed it; here the same
    # call also exercises label/category.
    assert vt["label"]
    assert vt["category"] == "integrations"


@pytest.mark.asyncio
async def test_list_has_entry_even_when_unset(client: AsyncClient) -> None:
    """The UI needs to render the editor for unset secrets too —
    so the list always carries one entry per managed secret slot,
    with has_value=False when no row exists yet."""
    headers = await _login(client, admin=True)
    r = await client.get("/api/v1/system/secrets", headers=headers)
    secrets = r.json()["secrets"]
    keys = {s["key"] for s in secrets}
    assert "virustotal_api_key" in keys
    vt = [s for s in secrets if s["key"] == "virustotal_api_key"][0]
    assert vt["has_value"] is False
    assert vt["last_set_at"] is None


# ── Length bounds + validation ───────────────────────────────
@pytest.mark.asyncio
async def test_set_too_short_returns_422(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": "tooshort"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_too_long_returns_422(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": "x" * 200},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_unknown_secret_returns_422(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/secrets/aws_secret_access_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    assert r.status_code == 422


# ── Clear ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_clear_removes_secret(client: AsyncClient) -> None:
    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    r = await client.delete(
        "/api/v1/system/secrets/virustotal_api_key", headers=headers
    )
    assert r.status_code == 204
    # List confirms the value is gone.
    r = await client.get("/api/v1/system/secrets", headers=headers)
    vt = [s for s in r.json()["secrets"] if s["key"] == "virustotal_api_key"][0]
    assert vt["has_value"] is False


# ── Test endpoint ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_test_requires_stored_value(client: AsyncClient) -> None:
    """POST /secrets/{key}/test errors out with 422 when nothing is
    stored — the test endpoint can't probe a non-existent secret."""
    headers = await _login(client, admin=True)
    r = await client.post(
        "/api/v1/system/secrets/virustotal_api_key/test", headers=headers
    )
    assert r.status_code == 422
    assert "Set the value first" in r.text


@pytest.mark.asyncio
async def test_test_with_invalid_key_records_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end test endpoint path without hitting VirusTotal:
    monkey-patch the handler to simulate a 401 from upstream and
    assert the failure path records the outcome on the DB row."""
    from app.services import secret_testers

    async def fake_handler(_plaintext: str) -> tuple[bool, str]:
        return False, "VirusTotal rejected the API key (401/403)."

    monkeypatch.setitem(
        secret_testers._HANDLERS, "virustotal_api_key", fake_handler
    )

    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    r = await client.post(
        "/api/v1/system/secrets/virustotal_api_key/test", headers=headers
    )
    # Upstream failure → 502
    assert r.status_code == 502
    # ...and metadata records the outcome.
    r = await client.get("/api/v1/system/secrets", headers=headers)
    vt = [s for s in r.json()["secrets"] if s["key"] == "virustotal_api_key"][0]
    assert vt["last_test_ok"] is False
    assert "401" in (vt["last_test_detail"] or "")
    # Plaintext still hidden in the metadata response.
    assert PLAINTEXT_SECRET not in r.text


@pytest.mark.asyncio
async def test_test_success_records_ok(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import secret_testers

    async def fake_handler(_plaintext: str) -> tuple[bool, str]:
        return True, "Authenticated to VirusTotal."

    monkeypatch.setitem(
        secret_testers._HANDLERS, "virustotal_api_key", fake_handler
    )

    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )
    r = await client.post(
        "/api/v1/system/secrets/virustotal_api_key/test", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Detail string should be present and not contain the secret.
    assert body["detail"] == "Authenticated to VirusTotal."
    assert PLAINTEXT_SECRET not in r.text


# ── Cipher integrity: tampered ciphertext rejected ───────────
@pytest.mark.asyncio
async def test_corrupt_ciphertext_cannot_be_used(
    client: AsyncClient,
) -> None:
    """If something corrupts the ciphertext in the DB, the
    decrypt path raises rather than silently returning garbage —
    so subsequent operations using the secret fail cleanly."""
    from app.models.runtime_setting import EncryptedSecret

    headers = await _login(client, admin=True)
    await client.put(
        "/api/v1/system/secrets/virustotal_api_key",
        headers=headers,
        json={"plaintext": PLAINTEXT_SECRET},
    )

    async with get_database().session() as sess:
        row = await sess.get(EncryptedSecret, "virustotal_api_key")
        assert row is not None
        # Flip one byte deep in the ciphertext to invalidate the GCM tag.
        bad = bytearray(row.ciphertext)
        bad[-1] ^= 0x01
        row.ciphertext = bytes(bad)
        await sess.commit()

    # Test endpoint pulls plaintext → decrypt fails → 500 (or 502
    # depending on how the exception bubbles). What matters is the
    # plaintext never leaks regardless. The exact status code is
    # less important than the absence of the secret in the body.
    r = await client.post(
        "/api/v1/system/secrets/virustotal_api_key/test", headers=headers
    )
    assert r.status_code >= 500
    assert PLAINTEXT_SECRET not in r.text
