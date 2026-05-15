"""Stage 15 (audit follow-up) — notification provider completeness.

Pins:
  1. Webhook: custom HTTP method (PUT) is honored.
  2. Webhook: custom headers attach on every send.
  3. Webhook: HMAC-SHA256 body signature attaches when
     ``secret_header_name`` is set AND ``webhook_secret`` is
     configured; verifiable against the raw body bytes.
  4. Webhook: HMAC is omitted when only one of the two is set.
  5. Webhook: non-2xx upstream surfaces ``status="failed"`` with
     the status code in detail.
  6. Webhook: unsupported HTTP method is rejected without a network
     call.
  7. Email: failure path surfaces ``status="failed"`` with the
     transport error verbatim (truncated).
  8. ``GET /notifications/kinds`` includes both ``email`` and
     ``webhook`` and surfaces the new webhook config keys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.notifications.providers.email import EmailNotificationProvider
from app.notifications.providers.http import WebhookNotificationProvider
from app.notifications.types import ChannelConfig, NotificationMessage
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from sqlalchemy import update

PASSWORD = "supersecret-password-1!"


def _mock_transport(captured: dict[str, Any], *, status_code: int = 204):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = bytes(request.content)
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


def _message() -> NotificationMessage:
    return NotificationMessage(
        subject="Stage 15 test",
        body="hello",
        severity="warn",
        severity_rank=40,
        context={"rule_id": "r-1"},
    )


def _config(**opts: Any) -> ChannelConfig:
    return ChannelConfig(
        channel_id="c-1",
        name="Test channel",
        kind="webhook",
        options=opts,
        secrets={},
    )


@pytest.mark.asyncio
async def test_webhook_custom_method_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await WebhookNotificationProvider().send(
        _config(url="http://hook.test/in", method="PUT"), _message()
    )
    assert report.status == "sent"
    assert captured["method"] == "PUT"


@pytest.mark.asyncio
async def test_webhook_custom_headers_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await WebhookNotificationProvider().send(
        _config(
            url="http://hook.test/in",
            headers={
                "X-Tenant-Id": "tenant-7",
                "Authorization": "Bearer secret",
            },
        ),
        _message(),
    )
    assert report.status == "sent"
    # httpx lowercases header names in the captured dict.
    assert captured["headers"]["x-tenant-id"] == "tenant-7"
    assert captured["headers"]["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_webhook_hmac_signature_attached_and_verifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    secret = "deadbeef-secret"
    cfg = ChannelConfig(
        channel_id="c-hmac",
        name="HMAC channel",
        kind="webhook",
        options={
            "url": "http://hook.test/in",
            "secret_header_name": "X-Auditarr-Signature",
        },
        secrets={"webhook_secret": secret},
    )

    report = await WebhookNotificationProvider().send(cfg, _message())
    assert report.status == "sent"
    sig = captured["headers"]["x-auditarr-signature"]
    assert sig.startswith("sha256=")
    # Verify the digest against the bytes the server received.
    expected = hmac.new(
        secret.encode("utf-8"), captured["body"], hashlib.sha256
    ).hexdigest()
    assert sig == f"sha256={expected}"


@pytest.mark.asyncio
async def test_webhook_hmac_omitted_when_only_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If secret is set but secret_header_name is not, no signature
    is attached. This protects against accidentally leaking the
    secret to the wrong header in a misconfigured channel."""
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    cfg = ChannelConfig(
        channel_id="c",
        name="x",
        kind="webhook",
        options={"url": "http://hook.test/in"},
        secrets={"webhook_secret": "set-but-no-header"},
    )
    await WebhookNotificationProvider().send(cfg, _message())
    for k in captured["headers"]:
        assert "signature" not in k.lower()


@pytest.mark.asyncio
async def test_webhook_4xx_surfaces_status_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault(
            "transport", _mock_transport(captured, status_code=500)
        )
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await WebhookNotificationProvider().send(
        _config(url="http://hook.test/in"), _message()
    )
    assert report.status == "failed"
    assert "500" in (report.detail or "")


@pytest.mark.asyncio
async def test_webhook_unsupported_method_rejected_without_network() -> None:
    """An invalid method short-circuits before any network call."""
    report = await WebhookNotificationProvider().send(
        _config(url="http://hook.test/in", method="DELETE"), _message()
    )
    assert report.status == "failed"
    assert "method" in (report.detail or "").lower()


@pytest.mark.asyncio
async def test_email_provider_failure_surfaces_status_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email provider must NOT raise on transport failure — the
    Stage 15 guard rail says the dispatcher must still be able to
    record a NotificationDelivery row."""
    # Patch the SMTP provider's send to raise.
    from app.services.email.providers import smtp as smtp_module

    async def boom(self, *args, **kwargs):
        raise RuntimeError("SMTP server unreachable")

    monkeypatch.setattr(smtp_module.SmtpEmailProvider, "send", boom)

    cfg = ChannelConfig(
        channel_id="e-1",
        name="email",
        kind="email",
        options={
            "to": "ops@example.com",
            "smtp_host": "mail.example.com",
            "smtp_port": 587,
            "from_email": "auditarr@example.com",
        },
        secrets={"smtp_username": "u", "smtp_password": "p"},
    )
    report = await EmailNotificationProvider().send(cfg, _message())
    assert report.status == "failed"
    assert "SMTP server unreachable" in (report.detail or "")


# ── /notifications/kinds endpoint ──────────────────────────────
@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage15.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
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
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
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


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = r.json()
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_kinds_endpoint_lists_email_and_webhook(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.get("/api/v1/notifications/kinds", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    kinds = {row["kind"] for row in body}
    assert "email" in kinds
    assert "webhook" in kinds

    webhook = next(row for row in body if row["kind"] == "webhook")
    # Stage 15 (audit follow-up): the new config keys must be visible
    # to the frontend's dynamic-form builder.
    props = webhook["config_schema"]["properties"]
    assert "url" in props
    assert "method" in props
    assert "headers" in props
    assert "secret_header_name" in props
    # And the secret field must be declared.
    assert "webhook_secret" in webhook["secret_fields"]


@pytest.mark.asyncio
async def test_kinds_endpoint_webhook_method_enum(
    client: AsyncClient,
) -> None:
    """The `method` field must enumerate POST and PUT — that's the
    contract the frontend's dropdown reads."""
    headers = await _admin_headers(client)
    r = await client.get("/api/v1/notifications/kinds", headers=headers)
    body = r.json()
    webhook = next(row for row in body if row["kind"] == "webhook")
    method_field = webhook["config_schema"]["properties"]["method"]
    assert sorted(method_field["enum"]) == ["POST", "PUT"]
