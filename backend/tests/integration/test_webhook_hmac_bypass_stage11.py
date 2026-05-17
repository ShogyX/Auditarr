"""Stage 11 (v1.7) — Webhook HMAC-bypass test.

Plan §552 contract:
    Channel with ``hmac_required=False`` and no secret sends
    successfully; the healthcheck ``detail`` contains
    "unsigned".

We exercise the WebhookNotificationProvider directly (rather
than through the dispatcher + ASGI) because the contract
under test is the provider's send/healthcheck branching on
the new ``hmac_required`` flag. The dispatcher already has
coverage from Stage 15 + Stage 19; this file pins the new
opt-out path.

All HTTP calls mock httpx so no real wire traffic fires.
"""

from __future__ import annotations

import httpx
import pytest

from app.notifications.providers.http import WebhookNotificationProvider
from app.notifications.types import (
    ChannelConfig,
    NotificationMessage,
)


def _mock_transport() -> httpx.MockTransport:
    """Echoes a 200 OK for any request. The Stage 11 contract is
    about the provider's send-decision branching, not the
    upstream's response — so any 2xx is fine."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


def _msg() -> NotificationMessage:
    return NotificationMessage(
        subject="hello",
        body="world",
        severity="info",
        severity_rank=1,
        context={},
    )


# ── Test 1 — Plan §552 contract: hmac_required=False + no secret ─


@pytest.mark.asyncio
async def test_send_succeeds_with_hmac_disabled_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §552: with ``hmac_required=False`` and no secret
    configured, the send goes out unsigned and succeeds. The
    DeliveryReport's ``detail`` carries the security note so
    the operator sees the downgrade in their delivery log."""
    real_init = httpx.AsyncClient.__init__

    captured: dict = {}

    def patched(self, *args, **kwargs):
        # Capture so we can inspect that NO signature header is sent.
        kwargs.setdefault("transport", _mock_transport())
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    # Monkeypatch the request layer to capture outbound headers.
    real_request = httpx.AsyncClient.request

    async def request_capture(self, method, url, **kwargs):
        captured["headers"] = dict(kwargs.get("headers") or {})
        return await real_request(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", request_capture)

    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "hmac_required": False,
            "secret_header_name": "X-Auditarr-Signature",
        },
        secrets={},  # no webhook_secret
    )
    report = await provider.send(config, _msg())

    # Send must succeed (this is the whole point of the opt-out).
    assert report.status == "sent"
    # Detail flags the unsigned send so the operator sees it.
    assert "unsigned" in (report.detail or "").lower()

    # No signature header was attached.
    sent_headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-auditarr-signature" not in sent_headers


# ── Test 2 — Plan §552 contract: healthcheck "unsigned" detail ──


@pytest.mark.asyncio
async def test_healthcheck_warns_about_unsigned_sends() -> None:
    """Plan §552: healthcheck on an hmac-disabled channel must
    return ``status="ok"`` with a ``detail`` that contains
    "unsigned" so the operator's healthcheck UI surfaces the
    security downgrade."""
    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "hmac_required": False,
        },
        secrets={},
    )
    report = await provider.healthcheck(config)
    assert report.status == "ok"
    assert "unsigned" in (report.detail or "").lower()


# ── Test 3 — Default config requires HMAC (regression guard) ───


@pytest.mark.asyncio
async def test_send_refuses_when_hmac_required_but_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``hmac_required=True``: if a signature header is
    configured but the secret is missing, the send is REFUSED
    rather than silently going out unsigned. Stage 11 fixes
    the "you thought you were signing but weren't" footgun."""
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport())
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "secret_header_name": "X-Auditarr-Signature",
            # ``hmac_required`` defaults to True.
        },
        secrets={},  # no secret → misconfigured.
    )
    report = await provider.send(config, _msg())
    assert report.status == "failed"
    assert "hmac required" in (report.detail or "").lower()


# ── Test 4 — Signed sends still work (regression guard) ────────


@pytest.mark.asyncio
async def test_send_signs_when_secret_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Stage 15 signed-send path must still work after
    Stage 11's changes. With both the signature header AND
    the secret configured, the request carries the HMAC
    signature."""
    captured: dict = {}
    real_init = httpx.AsyncClient.__init__
    real_request = httpx.AsyncClient.request

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport())
        real_init(self, *args, **kwargs)

    async def request_capture(self, method, url, **kwargs):
        captured["headers"] = dict(kwargs.get("headers") or {})
        return await real_request(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    monkeypatch.setattr(httpx.AsyncClient, "request", request_capture)

    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "secret_header_name": "X-Auditarr-Signature",
        },
        secrets={"webhook_secret": "super-secret-key"},
    )
    report = await provider.send(config, _msg())
    assert report.status == "sent"

    sent_headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-auditarr-signature" in sent_headers
    assert sent_headers["x-auditarr-signature"].startswith("sha256=")


# ── Test 5 — Healthcheck flags misconfigured channel as failed ─


@pytest.mark.asyncio
async def test_healthcheck_flags_misconfigured_channel_as_failed() -> None:
    """When ``hmac_required=True`` AND signature header is
    set AND secret is missing, the healthcheck returns
    ``status="failed"`` so the operator's UI shows a red
    state rather than a misleading green."""
    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "secret_header_name": "X-Auditarr-Signature",
            "hmac_required": True,
        },
        secrets={},
    )
    report = await provider.healthcheck(config)
    assert report.status == "failed"
    assert "hmac required" in (report.detail or "").lower()


# ── Test 6 — Healthcheck reports happy state for signed channel ─


@pytest.mark.asyncio
async def test_healthcheck_reports_happy_state_for_signed_channel() -> None:
    """A correctly-configured signing channel reports
    ``status="ok"`` with detail mentioning HMAC signing."""
    provider = WebhookNotificationProvider()
    config = ChannelConfig(
        channel_id="c1", name="test", kind="webhook",
        options={
            "url": "https://hook.example.com/",
            "secret_header_name": "X-Auditarr-Signature",
        },
        secrets={"webhook_secret": "k"},
    )
    report = await provider.healthcheck(config)
    assert report.status == "ok"
    assert "hmac" in (report.detail or "").lower()


# ── Test 7 — Missing URL fails healthcheck hard ────────────────


@pytest.mark.asyncio
async def test_healthcheck_fails_without_url() -> None:
    """No URL → healthcheck is a hard failure. Distinct from
    the security-downgrade warning."""
    provider = WebhookNotificationProvider()
    config = ChannelConfig(channel_id="c1", name="test", kind="webhook", options={}, secrets={})
    report = await provider.healthcheck(config)
    assert report.status == "failed"
    assert "url" in (report.detail or "").lower()
