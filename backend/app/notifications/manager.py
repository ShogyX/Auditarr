"""Notification channel manager.

Mirrors :class:`app.integrations.manager.IntegrationManager` — same
provider Protocol, same secret-box wire format, same plugin extension
point. The dispatcher (below) drives this; tests and the test-send API
also call it directly.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.registry import ServiceRegistry
from app.events.bus import EventBus
from app.models.notification_channel import NotificationChannel
from app.notifications.providers import builtin_providers
from app.notifications.types import (
    ChannelConfig,
    DeliveryReport,
    NotificationMessage,
    NotificationProvider,
)
from app.security.secrets import SecretBox

log = get_logger("auditarr.notifications.manager", category="notifications")


class NotificationManager:
    """Operates on persisted channels through registered providers."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: ServiceRegistry,
        secret_box: SecretBox,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._box = secret_box
        self._bus = event_bus

    # ── Provider lookup ─────────────────────────────────────────
    def _provider_pool(self) -> list[NotificationProvider]:
        """Built-in providers + anything the plugin registry has registered."""
        out: list[NotificationProvider] = list(builtin_providers())
        out.extend(self._registry.providers_for("notifications.channel"))
        return out

    def provider_for(self, kind: str) -> NotificationProvider | None:
        for provider in self._provider_pool():
            if provider.kind == kind:
                return provider
        return None

    def known_kinds(self) -> list[NotificationProvider]:
        # Dedup by kind in case a plugin shadows a built-in.
        seen: dict[str, NotificationProvider] = {}
        for provider in self._provider_pool():
            seen.setdefault(provider.kind, provider)
        return list(seen.values())

    # ── Validation + secret encryption ──────────────────────────
    def validate_config_against_schema(
        self, kind: str, config: dict, secrets: dict
    ) -> None:
        """Lightweight check: required keys present, no unknown keys.

        Mirrors :meth:`IntegrationManager.validate_config_against_schema`.
        We don't ship a full JSON Schema validator — the schemas are
        small and the manager is the only consumer.
        """
        provider = self.provider_for(kind)
        if provider is None:
            raise ValidationError(f"Unknown notification kind: {kind!r}")

        schema = provider.config_schema or {}
        required = set(schema.get("required", []))
        # Templates are universal optional keys — every channel can carry
        # ``subject_template``/``body_template`` overrides regardless of
        # whether the provider's own schema declared them.
        properties = set(schema.get("properties", {}).keys()) | {
            "subject_template",
            "body_template",
        }

        missing = required - set(config.keys())
        if missing:
            raise ValidationError(
                "Missing required channel config fields",
                details={"missing": sorted(missing)},
            )
        unknown = set(config.keys()) - properties
        if properties and unknown:
            raise ValidationError(
                "Unknown channel config fields",
                details={"unknown": sorted(unknown)},
            )

        required_secrets = set(provider.secret_fields)
        # Empty/None values for declared secrets count as missing.
        provided = {k for k, v in secrets.items() if v not in (None, "")}
        missing_secrets = required_secrets - provided
        if missing_secrets:
            raise ValidationError(
                "Missing required channel secret fields",
                details={"missing_secrets": sorted(missing_secrets)},
            )

    async def encrypt_and_set_secrets(
        self, channel: NotificationChannel, secrets: dict
    ) -> None:
        if secrets:
            channel.secrets_ciphertext = self._box.encrypt_dict(secrets)
        else:
            channel.secrets_ciphertext = None

    def build_config(self, channel: NotificationChannel) -> ChannelConfig:
        """Materialize a ChannelConfig for the provider, decrypting secrets."""
        secrets = (
            self._box.decrypt_dict(channel.secrets_ciphertext)
            if channel.secrets_ciphertext
            else {}
        )
        return ChannelConfig(
            channel_id=channel.id,
            name=channel.name,
            kind=channel.kind,
            options=dict(channel.config or {}),
            secrets=dict(secrets),
        )

    # ── Sending ─────────────────────────────────────────────────
    async def send(
        self, channel: NotificationChannel, message: NotificationMessage
    ) -> DeliveryReport:
        provider = self.provider_for(channel.kind)
        if provider is None:
            return DeliveryReport(
                status="failed",
                detail=f"No provider registered for kind={channel.kind!r}",
            )
        config = self.build_config(channel)
        try:
            return await provider.send(config, message)
        except Exception as exc:  # noqa: BLE001
            return DeliveryReport(status="failed", detail=str(exc)[:500])

    # ── Preflight (test from un-persisted config) ──────────────
    async def preflight(
        self,
        *,
        kind: str,
        config: dict,
        secrets: dict,
        message: NotificationMessage,
    ) -> DeliveryReport:
        """Test-send without persisting the channel."""
        provider = self.provider_for(kind)
        if provider is None:
            return DeliveryReport(
                status="failed",
                detail=f"No provider registered for kind={kind!r}",
            )
        candidate = ChannelConfig(
            channel_id="(preflight)",
            name="(preflight)",
            kind=kind,
            options=dict(config or {}),
            secrets=dict(secrets or {}),
        )
        try:
            return await provider.send(candidate, message)
        except Exception as exc:  # noqa: BLE001
            return DeliveryReport(status="failed", detail=str(exc)[:500])

    @staticmethod
    def require_channel(channel: NotificationChannel | None) -> NotificationChannel:
        if channel is None:
            raise NotFoundError("Notification channel not found")
        return channel
