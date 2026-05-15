"""HTTP notification provider tests (webhook, discord, slack).

Uses ``httpx.MockTransport`` so no actual network traffic happens. Each
test asserts the payload shape the provider sends — this is what Stage
12 plugins will use to extend the system, so the public payload
contracts are worth pinning down.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.notifications.providers.http import (
    DiscordNotificationProvider,
    SlackNotificationProvider,
    WebhookNotificationProvider,
)
from app.notifications.types import ChannelConfig, NotificationMessage


def _mock_transport(captured: dict[str, Any], *, status_code: int = 204):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.content.decode("utf-8")
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


def _message(severity: str = "warn") -> NotificationMessage:
    return NotificationMessage(
        subject="Big files in Movies",
        body="big.mkv exceeded 25 Mbps",
        severity=severity,
        severity_rank=40,
        context={"rule_id": "r1"},
    )


def _config(kind: str, **opts: Any) -> ChannelConfig:
    return ChannelConfig(
        channel_id="c1",
        name="Test",
        kind=kind,
        options=opts,
        secrets={},
    )


@pytest.mark.asyncio
async def test_webhook_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic webhook produces an Auditarr-shaped JSON payload."""
    captured: dict[str, Any] = {}
    # Patch httpx.AsyncClient to use a mock transport. This is the
    # canonical pattern used throughout Stage 5 connector tests.
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await WebhookNotificationProvider().send(
        _config("webhook", url="http://hook.test/in"),
        _message(),
    )
    assert report.status == "sent"
    assert captured["url"] == "http://hook.test/in"
    import json as _json

    payload = _json.loads(captured["json"])
    assert payload["subject"] == "Big files in Movies"
    assert payload["body"].startswith("big.mkv")
    assert payload["severity"] == "warn"
    assert payload["context"] == {"rule_id": "r1"}


@pytest.mark.asyncio
async def test_webhook_missing_url_fails() -> None:
    report = await WebhookNotificationProvider().send(
        _config("webhook"), _message()
    )
    assert report.status == "failed"
    assert "URL" in (report.detail or "")


@pytest.mark.asyncio
async def test_discord_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await DiscordNotificationProvider().send(
        _config(
            "discord",
            webhook_url="https://discord.test/webhook",
            username="Auditarr Bot",
        ),
        _message("high"),
    )
    assert report.status == "sent"

    import json as _json

    payload = _json.loads(captured["json"])
    assert payload["username"] == "Auditarr Bot"
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == "Big files in Movies"
    assert embed["color"] == DiscordNotificationProvider._COLOURS["high"]


@pytest.mark.asyncio
async def test_slack_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await SlackNotificationProvider().send(
        _config("slack", webhook_url="https://slack.test/services/x/y/z"),
        _message(),
    )
    assert report.status == "sent"

    import json as _json

    payload = _json.loads(captured["json"])
    assert "Big files in Movies" in payload["text"]
    assert "big.mkv" in payload["text"]


@pytest.mark.asyncio
async def test_http_failure_surfaces_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(captured, status_code=500))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await WebhookNotificationProvider().send(
        _config("webhook", url="http://hook.test/in"),
        _message(),
    )
    assert report.status == "failed"
    assert "500" in (report.detail or "")
