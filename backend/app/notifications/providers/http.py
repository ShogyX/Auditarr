"""HTTP-based notification channels.

Three providers, all delivering via a single JSON POST:

* ``webhook``  — generic, payload shape is Auditarr's own.
* ``discord``  — Discord incoming webhook (``content`` + optional embed).
* ``slack``    — Slack incoming webhook (``text`` + optional Block Kit).

The transport code is shared via :func:`_post_json`; what differs
per-provider is the payload shape and the config_schema.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.notifications.types import (
    ChannelConfig,
    DeliveryReport,
    NotificationMessage,
)


async def _post_json(
    url: str, payload: dict[str, Any], *, timeout: float = 10.0
) -> DeliveryReport:
    """Single-shot JSON POST that returns a normalized DeliveryReport."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return DeliveryReport(
                status="failed",
                detail=f"HTTP {response.status_code}: {response.text[:200]}",
            )
    except httpx.HTTPError as exc:
        return DeliveryReport(status="failed", detail=str(exc)[:500])
    return DeliveryReport(status="sent", detail=f"HTTP {response.status_code}")


# ── Generic webhook ─────────────────────────────────────────
class WebhookNotificationProvider:
    """Generic webhook provider.

    Stage 15 (audit follow-up): supports custom HTTP method, custom
    headers, and optional HMAC-SHA256 signing of the request body
    using a per-channel secret. The HMAC follows the standard
    webhook-security pattern: when ``secret_header_name`` is set
    and the ``webhook_secret`` secret is configured on the channel,
    every request carries an ``X-...: sha256=<hex>`` header so the
    receiver can verify authenticity.
    """

    kind = "webhook"
    label = "Webhook"
    config_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {
                "type": "string",
                "title": "Webhook URL",
                "description": "Receives a JSON POST with subject/body/context.",
            },
            "method": {
                "type": "string",
                "title": "HTTP method",
                "description": "Defaults to POST. PUT is allowed for APIs that prefer it.",
                "default": "POST",
                "enum": ["POST", "PUT"],
            },
            "headers": {
                "type": "object",
                "title": "Custom request headers",
                "description": (
                    "Static headers to attach on every send. "
                    "Useful for bearer tokens, tenant IDs, etc."
                ),
                "additionalProperties": {"type": "string"},
            },
            "secret_header_name": {
                "type": "string",
                "title": "HMAC signature header",
                "description": (
                    "When set together with the 'webhook_secret' secret, "
                    "the request body is signed with HMAC-SHA256 and the "
                    "signature is attached as 'sha256=<hex>' in this header. "
                    "Standard pattern: 'X-Auditarr-Signature'."
                ),
            },
        },
    }
    # Stage 15 (audit follow-up): one optional secret for HMAC.
    # Empty/unset → no signature.
    secret_fields: tuple[str, ...] = ("webhook_secret",)

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        url = str(config.options.get("url", "")).strip()
        if not url:
            return DeliveryReport(status="failed", detail="No URL configured")
        method = str(config.options.get("method", "POST")).upper()
        if method not in ("POST", "PUT"):
            return DeliveryReport(
                status="failed", detail=f"Unsupported method: {method!r}"
            )
        custom_headers = config.options.get("headers") or {}
        if not isinstance(custom_headers, dict):
            return DeliveryReport(
                status="failed",
                detail="'headers' must be an object of string→string",
            )

        payload = {
            "subject": message.subject,
            "body": message.body,
            "severity": message.severity,
            "severity_rank": message.severity_rank,
            "context": message.context,
        }
        # We serialize to bytes ONCE so the HMAC and the wire send
        # operate on identical bytes — round-tripping through httpx's
        # ``json=`` would re-serialize and could yield a different
        # encoding (whitespace, key order) than what we signed.
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        headers: dict[str, str] = {"content-type": "application/json"}
        for k, v in custom_headers.items():
            headers[str(k)] = str(v)

        secret = (config.secrets or {}).get("webhook_secret")
        sig_header = str(
            config.options.get("secret_header_name") or ""
        ).strip()
        if sig_header and secret:
            digest = hmac.new(
                str(secret).encode("utf-8"), body_bytes, hashlib.sha256
            ).hexdigest()
            headers[sig_header] = f"sha256={digest}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.request(
                    method, url, content=body_bytes, headers=headers
                )
            if response.status_code >= 400:
                return DeliveryReport(
                    status="failed",
                    detail=f"HTTP {response.status_code}: {response.text[:200]}",
                )
        except httpx.HTTPError as exc:
            return DeliveryReport(status="failed", detail=str(exc)[:500])
        return DeliveryReport(
            status="sent", detail=f"HTTP {response.status_code}"
        )


# ── Discord ─────────────────────────────────────────────────
class DiscordNotificationProvider:
    kind = "discord"
    label = "Discord"
    config_schema = {
        "type": "object",
        "required": ["webhook_url"],
        "properties": {
            "webhook_url": {
                "type": "string",
                "title": "Discord webhook URL",
                "description": "Channel → Integrations → Webhooks → New Webhook.",
            },
            "username": {
                "type": "string",
                "title": "Bot username (optional)",
                "default": "Auditarr",
            },
        },
    }
    secret_fields: tuple[str, ...] = ()

    # Map severity → embed colour. Discord wants an integer.
    _COLOURS = {
        "ok": 0x2ECC71,
        "info": 0x3498DB,
        "warn": 0xF1C40F,
        "high": 0xE67E22,
        "error": 0xE74C3C,
        "crit": 0x992D22,
    }

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        url = str(config.options.get("webhook_url", "")).strip()
        if not url:
            return DeliveryReport(
                status="failed", detail="No webhook URL configured"
            )
        colour = self._COLOURS.get(message.severity, 0x95A5A6)
        # 2000-char limit on Discord ``content``; we truncate generously
        # and let the embed's ``description`` carry the body (4096-char).
        payload: dict[str, Any] = {
            "username": config.options.get("username") or "Auditarr",
            "embeds": [
                {
                    "title": message.subject[:256],
                    "description": message.body[:4000],
                    "color": colour,
                }
            ],
        }
        return await _post_json(url, payload)


# ── Slack ────────────────────────────────────────────────────
class SlackNotificationProvider:
    kind = "slack"
    label = "Slack"
    config_schema = {
        "type": "object",
        "required": ["webhook_url"],
        "properties": {
            "webhook_url": {
                "type": "string",
                "title": "Slack incoming webhook URL",
            },
        },
    }
    secret_fields: tuple[str, ...] = ()

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        url = str(config.options.get("webhook_url", "")).strip()
        if not url:
            return DeliveryReport(
                status="failed", detail="No webhook URL configured"
            )
        # Slack accepts a simple ``text`` payload; richer Block Kit is
        # available but adds complexity without a clear payoff for short
        # alerts. Operators wanting custom rendering can use the generic
        # ``webhook`` provider against the same URL.
        payload = {
            "text": f"*{message.subject}*\n{message.body}",
        }
        return await _post_json(url, payload)
