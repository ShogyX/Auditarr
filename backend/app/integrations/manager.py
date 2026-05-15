"""Integration manager.

Glues persisted :class:`Integration` rows to the runtime
:class:`IntegrationProvider` instances registered by connector plugins.

Concretely the manager:
* Looks up the provider for a given ``kind`` from the service registry.
* Builds a decrypted :class:`IntegrationConfig` snapshot.
* Dispatches healthcheck / library discovery / tag sync calls.
* Persists the latest healthcheck result to the integrations table.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.registry import ServiceRegistry
from app.events.bus import EventBus
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)
from app.models.integration import Integration
from app.security.secrets import SecretBox, SecretDecryptionError
from app.services.repositories import IntegrationRepository
from app.utils.datetime import utcnow

log = get_logger("auditarr.integrations.manager", category="integrations")


class IntegrationManager:
    """Service-layer entrypoint for everything integration-related."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: ServiceRegistry,
        secret_box: SecretBox,
        event_bus: EventBus,
    ) -> None:
        self._session = session
        self._registry = registry
        self._secret_box = secret_box
        self._bus = event_bus
        self._repo = IntegrationRepository(session)

    # ── Provider lookup ──────────────────────────────────────────
    def provider_for(self, kind: str) -> IntegrationProvider | None:
        """Return the provider registered under ``integration.<kind>``."""
        providers = self._registry.providers_for(f"integration.{kind}")
        return providers[0] if providers else None

    def known_kinds(self) -> list[str]:
        """All currently-registered integration kinds (e.g. plex, sonarr)."""
        return [
            cap.removeprefix("integration.")
            for cap in self._registry.capabilities()
            if cap.startswith("integration.")
        ]

    # ── Config materialization ───────────────────────────────────
    def build_config(self, integration: Integration) -> IntegrationConfig:
        """Decrypt secrets and assemble the runtime config snapshot."""
        if integration.secrets_ciphertext:
            try:
                secrets = self._secret_box.decrypt_dict(
                    integration.secrets_ciphertext
                )
            except SecretDecryptionError:
                log.error(
                    "integration.secret_decrypt_failed",
                    integration_id=integration.id,
                    name=integration.name,
                )
                raise
        else:
            secrets = {}
        return IntegrationConfig(
            integration_id=integration.id,
            name=integration.name,
            kind=integration.kind,
            options=dict(integration.config or {}),
            secrets=secrets,
        )

    # ── Healthcheck ──────────────────────────────────────────────
    async def healthcheck(self, integration: Integration) -> HealthReport:
        provider = self.provider_for(integration.kind)
        if provider is None:
            report = HealthReport(
                status="error",
                detail=f"No provider registered for kind={integration.kind!r}",
            )
        else:
            try:
                config = self.build_config(integration)
                report = await provider.healthcheck(config)
            except Exception as exc:  # noqa: BLE001 — surfaced as health error
                log.warning(
                    "integration.healthcheck_failed",
                    name=integration.name,
                    kind=integration.kind,
                    error=str(exc),
                )
                report = HealthReport(status="error", detail=str(exc)[:500])

        integration.health_status = report.status
        integration.health_detail = report.detail
        integration.health_checked_at = utcnow()

        await self._bus.emit(
            "integration.health_changed",
            {
                "integration_id": integration.id,
                "name": integration.name,
                "kind": integration.kind,
                "status": report.status,
                "detail": report.detail,
            },
            source="integrations",
        )
        return report

    async def healthcheck_all(self) -> dict[str, HealthReport]:
        """Run healthcheck on every enabled integration. Returns id → report."""
        rows = await self._repo.list_all(enabled_only=True)
        out: dict[str, HealthReport] = {}
        for row in rows:
            out[row.id] = await self.healthcheck(row)
        return out

    async def preflight(
        self,
        *,
        kind: str,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> HealthReport:
        """Run a healthcheck against an un-persisted candidate config.

        Used at create/update time so the operator gets immediate feedback
        if the URL is wrong, the token is rejected, or the upstream is
        unreachable — *before* the row hits the database. Nothing is
        persisted; no event is emitted.
        """
        provider = self.provider_for(kind)
        if provider is None:
            return HealthReport(
                status="error",
                detail=f"No provider registered for kind={kind!r}",
            )
        candidate = IntegrationConfig(
            integration_id="(preflight)",
            name="(preflight)",
            kind=kind,
            options=dict(config or {}),
            secrets=dict(secrets or {}),
        )
        try:
            return await provider.healthcheck(candidate)
        except Exception as exc:  # noqa: BLE001
            return HealthReport(status="error", detail=str(exc)[:500])

    async def ensure_reachable(self, integration: Integration) -> None:
        """Refuse sync operations against an integration that's not healthy.

        We *always* run a fresh healthcheck rather than trusting the cached
        ``health_status``: tokens may have been rotated, the server may be
        down, and treating the cache as authoritative leads to retrying
        broken syncs that fail in confusing ways further down the stack.
        Calling :meth:`healthcheck` here also updates the persisted state
        and emits the standard ``integration.health_changed`` event, so
        the dashboard stays in sync for free.
        """
        report = await self.healthcheck(integration)
        if report.status == "error":
            raise ValidationError(
                "Integration is not reachable; resolve the healthcheck first.",
                details={
                    "integration_id": integration.id,
                    "name": integration.name,
                    "kind": integration.kind,
                    "detail": report.detail,
                },
            )

    # ── Discovery / sync ─────────────────────────────────────────
    async def discover_libraries(
        self, integration: Integration
    ) -> list[DiscoveredLibrary]:
        provider = self.provider_for(integration.kind)
        if provider is None:
            raise NotFoundError(
                f"No provider registered for kind={integration.kind!r}"
            )
        await self.ensure_reachable(integration)
        config = self.build_config(integration)
        return await provider.discover_libraries(config)

    async def sync_tags(self, integration: Integration) -> list[TagSync]:
        provider = self.provider_for(integration.kind)
        if provider is None:
            raise NotFoundError(
                f"No provider registered for kind={integration.kind!r}"
            )
        await self.ensure_reachable(integration)
        config = self.build_config(integration)
        return await provider.sync_tags(config)

    # ── Mutations ────────────────────────────────────────────────
    async def encrypt_and_set_secrets(
        self, integration: Integration, secrets: dict[str, object]
    ) -> None:
        if not secrets:
            integration.secrets_ciphertext = None
            return
        integration.secrets_ciphertext = self._secret_box.encrypt_dict(secrets)

    def validate_config_against_schema(
        self,
        kind: str,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> None:
        """Lightweight check: required keys present, no unknown keys.

        We don't ship a full JSON Schema validator — the schema is small,
        the manager is the only consumer, and any complex validation should
        live in the provider's ``healthcheck`` anyway.
        """
        provider = self.provider_for(kind)
        if provider is None:
            raise ValidationError(f"Unknown integration kind: {kind!r}")

        schema = provider.config_schema or {}
        required = set(schema.get("required", []))
        properties = set(schema.get("properties", {}).keys())

        missing = required - set(config.keys())
        if missing:
            raise ValidationError(
                "Missing required config fields",
                details={"missing": sorted(missing)},
            )
        unknown = set(config.keys()) - properties
        if properties and unknown:
            raise ValidationError(
                "Unknown config fields",
                details={"unknown": sorted(unknown)},
            )

        required_secrets = set(provider.secret_fields)
        # Empty/None values for declared secrets are also missing.
        provided = {k for k, v in secrets.items() if v not in (None, "")}
        missing_secrets = required_secrets - provided
        if missing_secrets:
            raise ValidationError(
                "Missing required secret fields",
                details={"missing_secrets": sorted(missing_secrets)},
            )
