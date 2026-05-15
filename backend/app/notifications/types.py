"""Notification provider types.

A :class:`NotificationProvider` is anything that can deliver a
:class:`NotificationMessage` to its configured destination. Providers
are stateless — all configuration travels in on every call, the same
way :class:`app.integrations.types.IntegrationProvider` works.

Built-in providers live in :mod:`app.notifications.providers`; plugins
may register additional ones through the SDK
(``context.register_notification_channel(provider)``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class NotificationMessage:
    """A formatted alert ready to deliver."""

    subject: str
    body: str
    severity: str
    severity_rank: int
    # Context the rules engine attached: rule_id, media_file_id, etc.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChannelConfig:
    """All the configuration a provider sees for one channel."""

    channel_id: str
    name: str
    kind: str
    options: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryReport:
    """Outcome of a single send."""

    status: str  # ``sent`` | ``failed``
    detail: str | None = None


@runtime_checkable
class NotificationProvider(Protocol):
    """Stateless channel provider."""

    kind: str
    label: str
    config_schema: dict[str, Any]
    secret_fields: tuple[str, ...]

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        ...
