"""Stage 11 (v1.7) — Webhook source-whitelist test.

Plan §553 contract:
    POST to the webhook from an allowed IP succeeds; from a
    disallowed IP, 403.

Addendum B.8: whitelist is per-Integration (per webhook
endpoint), not per-channel. Each Integration row IS an
endpoint, so an operator wanting two upstreams with
different whitelists configures two Integration rows.

We exercise the matcher directly (no ASGI overhead) for the
unit-cleaner tests, plus end-to-end via the ASGI app for the
happy/sad paths the plan calls out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.webhooks import _matches_source_whitelist
from app.main import create_app
from app.models.integration import Integration
from app.security.secrets import get_secret_box
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


# ── Unit tests for the matcher helper ──────────────────────────


def test_matcher_exact_ip_matches() -> None:
    assert _matches_source_whitelist("192.168.1.10", ["192.168.1.10"]) is True


def test_matcher_exact_ip_mismatch() -> None:
    assert _matches_source_whitelist("192.168.1.10", ["192.168.1.11"]) is False


def test_matcher_cidr_v4() -> None:
    """IPv4 CIDR ranges match addresses inside them."""
    wl = ["192.168.1.0/24"]
    assert _matches_source_whitelist("192.168.1.5", wl) is True
    assert _matches_source_whitelist("192.168.1.255", wl) is True
    assert _matches_source_whitelist("192.168.2.5", wl) is False
    assert _matches_source_whitelist("10.0.0.1", wl) is False


def test_matcher_cidr_v6() -> None:
    """IPv6 CIDR ranges work the same way."""
    wl = ["2001:db8::/32"]
    assert _matches_source_whitelist("2001:db8::1", wl) is True
    assert _matches_source_whitelist("2001:dead::1", wl) is False


def test_matcher_mixed_entries() -> None:
    """A whitelist with a mix of IPs, CIDRs, and hostnames
    matches when ANY entry matches."""
    wl = [
        "192.168.1.0/24",
        "10.0.0.5",
        "localhost",
    ]
    assert _matches_source_whitelist("192.168.1.50", wl) is True
    assert _matches_source_whitelist("10.0.0.5", wl) is True
    assert _matches_source_whitelist("127.0.0.1", wl) is True  # via localhost
    assert _matches_source_whitelist("8.8.8.8", wl) is False


def test_matcher_empty_whitelist_returns_false() -> None:
    """Empty whitelist → no match. The caller is responsible
    for skipping the check entirely when the list is empty;
    this is the defensive fallback."""
    assert _matches_source_whitelist("192.168.1.1", []) is False
    assert _matches_source_whitelist("192.168.1.1", None) is False  # type: ignore[arg-type]


def test_matcher_handles_malformed_entries_gracefully() -> None:
    """Bad CIDR / unresolvable hostname / blank string don't
    crash the matcher — they just contribute no match."""
    wl = [
        "not-a-real-cidr/99",
        "",
        "host.does.not.exist.invalid.localdomain",
        "10.0.0.0/8",  # this one should match.
    ]
    assert _matches_source_whitelist("10.5.5.5", wl) is True
    assert _matches_source_whitelist("8.8.8.8", wl) is False


def test_matcher_empty_client_host_returns_false() -> None:
    """If the request didn't carry a client host (rare but
    possible in tests / behind certain proxies), no match."""
    assert _matches_source_whitelist("", ["192.168.1.0/24"]) is False


# ── End-to-end tests against the ASGI webhook endpoint ─────────


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "wh11.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    try:
        yield {"client": client, "db": db}
    finally:
        await client.aclose()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_settings.cache_clear()


async def _seed_integration_with_whitelist(
    db, *, whitelist: list[str], with_secret: bool = True
) -> str:
    """Seed a Sonarr integration with the given source_whitelist
    + a webhook secret so the post-whitelist signature check
    passes when called from an allowed IP."""

    async with db.session() as session:
        box = get_secret_box()
        ciphertext = (
            box.encrypt_dict({"value": "test-secret-key"})
            if with_secret
            else None
        )
        integration = Integration(
            name="Sonarr WH11",
            kind="sonarr",
            enabled=True,
            poll_interval_seconds=900,
            config={"source_whitelist": whitelist},
            health_status="ok",
            webhook_secret_ciphertext=ciphertext,
        )
        session.add(integration)
        await session.commit()
        return integration.id


# ── Test: allowed IP succeeds (plan §553 happy path) ───────────


@pytest.mark.asyncio
async def test_webhook_succeeds_when_source_ip_in_whitelist(env) -> None:
    """Plan §553: POST from an allowed IP succeeds."""
    import hashlib
    import hmac as _hmac
    import json

    # The ASGITransport reports ``testclient`` as the host by
    # default; we add it to the whitelist so the request is
    # allowed past the source check. We also provide CIDR
    # ranges as a smoke test.
    integration_id = await _seed_integration_with_whitelist(
        env["db"], whitelist=["127.0.0.1", "testclient", "::1"]
    )

    body = json.dumps({"eventType": "Test"}).encode("utf-8")
    sig = _hmac.new(
        b"test-secret-key", body, hashlib.sha256
    ).hexdigest()

    response = await env["client"].post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body,
        headers={
            "content-type": "application/json",
            "x-auditarr-signature": f"sha256={sig}",
        },
    )
    # Allowed source + valid signature → 200 (the dispatcher
    # may further ignore the event but the source/signature
    # gates passed).
    assert response.status_code == 200, response.text


# ── Test: disallowed IP returns 403 (plan §553 sad path) ───────


@pytest.mark.asyncio
async def test_webhook_returns_403_when_source_ip_not_in_whitelist(
    env,
) -> None:
    """Plan §553: POST from a disallowed IP returns 403 BEFORE
    signature verification runs."""
    integration_id = await _seed_integration_with_whitelist(
        env["db"], whitelist=["10.255.0.1"]  # ASGITransport host won't match
    )

    response = await env["client"].post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        json={"eventType": "Test"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    # The app wraps HTTPException(detail=...) in an envelope
    # with ``code`` / ``message`` / ``request_id``. The
    # whitelist-rejection message lands in ``message``.
    detail_text = (body.get("message") or body.get("detail") or "").lower()
    assert "whitelist" in detail_text


# ── Test: empty whitelist → check is skipped entirely ──────────


@pytest.mark.asyncio
async def test_webhook_skips_check_when_whitelist_empty(env) -> None:
    """Empty whitelist (or absent config key) → no source
    check, behaviour matches pre-Stage-11."""
    import hashlib
    import hmac as _hmac
    import json

    integration_id = await _seed_integration_with_whitelist(
        env["db"], whitelist=[]
    )

    body = json.dumps({"eventType": "Test"}).encode("utf-8")
    sig = _hmac.new(
        b"test-secret-key", body, hashlib.sha256
    ).hexdigest()
    response = await env["client"].post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body,
        headers={
            "content-type": "application/json",
            "x-auditarr-signature": f"sha256={sig}",
        },
    )
    # No source check → signature is the only gate.
    assert response.status_code == 200, response.text


# ── Test: whitelist check runs BEFORE signature check ──────────


@pytest.mark.asyncio
async def test_webhook_rejects_disallowed_source_before_signature_check(
    env,
) -> None:
    """The whitelist gate runs BEFORE signature verification —
    so a request from a disallowed IP gets 403, not 401 (which
    is what missing-signature would return). This protects
    against signature-brute-force-from-anywhere when an
    operator has set a whitelist."""
    integration_id = await _seed_integration_with_whitelist(
        env["db"], whitelist=["10.255.0.1"]
    )

    response = await env["client"].post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        # No signature header AT ALL — a 401 would normally
        # be the response for this. The 403 from whitelist
        # rejection takes priority.
        json={"eventType": "Test"},
    )
    assert response.status_code == 403, response.text
