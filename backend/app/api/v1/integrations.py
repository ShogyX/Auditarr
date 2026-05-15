"""Integrations router (``/api/v1/integrations``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.manager import IntegrationManager
from app.integrations.types import IntegrationProvider
from app.models.integration import Integration
from app.schemas.integrations import (
    DiscoveredLibraryRead,
    IntegrationCreate,
    IntegrationHealthRead,
    IntegrationKind,
    IntegrationRead,
    IntegrationUpdate,
)
from app.security.secrets import get_secret_box
from app.services.repositories import IntegrationRepository

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _manager(
    session: SessionDep, registry: RegistryDep, bus: EventBusDep
) -> IntegrationManager:
    return IntegrationManager(
        session=session,
        registry=registry,
        secret_box=get_secret_box(),
        event_bus=bus,
    )


def _to_read(row: Integration) -> IntegrationRead:
    return IntegrationRead(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        poll_interval_seconds=row.poll_interval_seconds,
        config=row.config or {},
        health_status=row.health_status,
        health_detail=row.health_detail,
        health_checked_at=row.health_checked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        has_secrets=bool(row.secrets_ciphertext),
    )


@router.get(
    "/kinds",
    response_model=list[IntegrationKind],
    summary="List integration kinds advertised by loaded plugins",
)
async def list_kinds(_user: CurrentUser, registry: RegistryDep) -> list[IntegrationKind]:
    out: list[IntegrationKind] = []
    for cap in sorted(registry.capabilities()):
        if not cap.startswith("integration."):
            continue
        providers = registry.providers_for(cap)
        if not providers:
            continue
        provider: IntegrationProvider = providers[0]  # type: ignore[assignment]
        out.append(
            IntegrationKind(
                kind=provider.kind,
                label=provider.label,
                config_schema=provider.config_schema or {},
                secret_fields=list(provider.secret_fields or ()),
            )
        )
    return out


@router.get(
    "",
    response_model=list[IntegrationRead],
    summary="List configured integrations",
)
async def list_integrations(
    _user: CurrentUser, session: SessionDep
) -> list[IntegrationRead]:
    rows = await IntegrationRepository(session).list_all()
    return [_to_read(r) for r in rows]


@router.post(
    "/test",
    response_model=IntegrationHealthRead,
    summary="Test a candidate configuration without saving it",
)
async def preflight_integration(
    body: IntegrationCreate,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> IntegrationHealthRead:
    """Run a healthcheck against an un-persisted candidate config.

    Useful as a "Test connection" button in the Connect dialog so the
    operator can verify reachability before clicking Save.
    """
    manager = _manager(session, registry, bus)
    manager.validate_config_against_schema(body.kind, body.config, body.secrets)
    report = await manager.preflight(
        kind=body.kind, config=body.config, secrets=body.secrets
    )
    return IntegrationHealthRead(
        integration_id="(preflight)",
        status=report.status,
        detail=report.detail,
        metadata=report.metadata,
    )


@router.post(
    "",
    response_model=IntegrationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an integration",
)
async def create_integration(
    body: IntegrationCreate,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
    skip_preflight: bool = Query(
        default=False,
        description=(
            "Skip the reachability check before creating. Use only when the "
            "upstream is intentionally unavailable (e.g. coordinated "
            "maintenance); the integration will be saved with an unknown "
            "health status."
        ),
    ),
) -> IntegrationRead:
    manager = _manager(session, registry, bus)
    manager.validate_config_against_schema(body.kind, body.config, body.secrets)
    repo = IntegrationRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("An integration with that name already exists")

    if not skip_preflight:
        report = await manager.preflight(
            kind=body.kind, config=body.config, secrets=body.secrets
        )
        if report.status == "error":
            raise ValidationError(
                "Cannot reach the upstream service with the provided "
                "configuration. Verify the URL and credentials, then try "
                "again. To save anyway, pass ?skip_preflight=true.",
                details={"detail": report.detail},
            )

    integration = Integration(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        config=body.config,
        poll_interval_seconds=body.poll_interval_seconds,
    )
    await manager.encrypt_and_set_secrets(integration, body.secrets)
    await repo.add(integration)

    # Seed health state from the preflight result so the dashboard shows
    # an accurate status immediately rather than waiting for the first
    # scheduler tick.
    if not skip_preflight:
        await manager.healthcheck(integration)

        # Stage 17 (audit follow-up): auto-snapshot discovered
        # libraries so the Path Mappings panel can surface unmapped
        # upstream paths without an extra round-trip. Non-fatal —
        # the upstream may not implement discovery (apprise, generic
        # webhook), in which case we just leave the field NULL. The
        # operator can hit the rediscover endpoint later.
        try:
            await _snapshot_discovered_paths(manager, integration)
        except Exception as exc:  # noqa: BLE001
            # Log + carry on. Discovery is advisory, not load-bearing.
            from app.core.logging import get_logger

            get_logger(__name__).warning(
                "integration.discover_paths_failed",
                integration_id=integration.id,
                kind=integration.kind,
                error=str(exc)[:200],
            )

    return _to_read(integration)


async def _snapshot_discovered_paths(
    manager: Any, integration: Integration
) -> None:
    """Stage 17 (audit follow-up): refresh ``discovered_paths`` from
    the upstream. Pure side-effect — caller decides whether to
    swallow exceptions or let them propagate.
    """
    from dataclasses import asdict
    from datetime import UTC, datetime

    discovered = await manager.discover_libraries(integration)
    now_iso = datetime.now(UTC).isoformat()
    integration.discovered_paths = [
        {
            "library_id": d.upstream_id,
            "label": d.name,
            "upstream_path": d.root_path or "",
            "discovered_at": now_iso,
        }
        for d in discovered
        # Skip entries without a path — they can't be mapped anyway,
        # and surfacing them in the panel would be confusing.
        if asdict(d).get("root_path")
    ]


@router.get(
    "/{integration_id}",
    response_model=IntegrationRead,
    summary="Get a single integration",
)
async def get_integration(
    integration_id: str, _user: CurrentUser, session: SessionDep
) -> IntegrationRead:
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    return _to_read(integration)


@router.patch(
    "/{integration_id}",
    response_model=IntegrationRead,
    summary="Update integration config or rotate secrets",
)
async def update_integration(
    integration_id: str,
    body: IntegrationUpdate,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
    skip_preflight: bool = Query(
        default=False,
        description=(
            "Skip the reachability check when changing config or secrets."
        ),
    ),
) -> IntegrationRead:
    repo = IntegrationRepository(session)
    integration = await repo.get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")

    manager = _manager(session, registry, bus)
    config_or_secrets_changed = body.config is not None or body.secrets is not None

    if config_or_secrets_changed:
        new_config = body.config if body.config is not None else integration.config
        # When secrets aren't supplied for update, we can't easily round-trip
        # the existing ones for validation. Only validate against the schema
        # when the operator is providing new secrets.
        if body.secrets is not None:
            manager.validate_config_against_schema(
                integration.kind, new_config or {}, body.secrets
            )
        # Preflight the candidate config against the upstream. We need a
        # full secrets dict, so when the operator is only changing config
        # we decrypt the existing secrets to test the new config with them.
        if not skip_preflight:
            secrets_for_preflight: dict[str, object]
            if body.secrets is not None:
                secrets_for_preflight = dict(body.secrets)
            elif integration.secrets_ciphertext:
                secrets_for_preflight = dict(
                    get_secret_box().decrypt_dict(integration.secrets_ciphertext)
                )
            else:
                secrets_for_preflight = {}
            report = await manager.preflight(
                kind=integration.kind,
                config=new_config or {},
                secrets=secrets_for_preflight,
            )
            if report.status == "error":
                raise ValidationError(
                    "Cannot reach the upstream service with the new "
                    "configuration. Changes were not saved. Pass "
                    "?skip_preflight=true to save anyway.",
                    details={"detail": report.detail},
                )
        integration.config = new_config or {}

    if body.secrets is not None:
        await manager.encrypt_and_set_secrets(integration, body.secrets)
    if body.name is not None:
        integration.name = body.name
    if body.enabled is not None:
        integration.enabled = body.enabled
    if body.poll_interval_seconds is not None:
        integration.poll_interval_seconds = body.poll_interval_seconds

    await session.flush()

    # Refresh persisted health state after a config/secrets change so the
    # dashboard reflects the new reality without waiting for the next tick.
    if config_or_secrets_changed and not skip_preflight:
        await manager.healthcheck(integration)

    return _to_read(integration)


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an integration",
)
async def delete_integration(
    integration_id: str, _admin: AdminUser, session: SessionDep
) -> None:
    repo = IntegrationRepository(session)
    integration = await repo.get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    await repo.delete(integration)


@router.post(
    "/{integration_id}/healthcheck",
    response_model=IntegrationHealthRead,
    summary="Run a healthcheck against an integration",
)
async def trigger_healthcheck(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> IntegrationHealthRead:
    # Bug-hunt 3: previously open to any authenticated user.
    # Triggering a healthcheck makes an outbound HTTP request
    # against the integration's (admin-configured) base_url and
    # surfaces network detail in the response — both
    # operationally sensitive. The dashboard's health summary is
    # already visible to non-admins (read-only); admin-gating
    # the *trigger* matches the rest of the integration write
    # surface, all of which is admin-only.
    manager = _manager(session, registry, bus)
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    report = await manager.healthcheck(integration)
    return IntegrationHealthRead(
        integration_id=integration.id,
        status=report.status,
        detail=report.detail,
        metadata=report.metadata,
    )


@router.get(
    "/{integration_id}/libraries",
    response_model=list[DiscoveredLibraryRead],
    summary="Discover libraries/sections on the upstream service",
)
async def discover_libraries(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> list[DiscoveredLibraryRead]:
    from dataclasses import asdict

    manager = _manager(session, registry, bus)
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    discovered = await manager.discover_libraries(integration)
    return [DiscoveredLibraryRead(**asdict(d)) for d in discovered]


@router.post(
    "/{integration_id}/discover-paths",
    summary="Stage 17: refresh the discovered_paths snapshot used by Path Mappings",
)
async def rediscover_paths(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> dict[str, Any]:
    """Refresh the integration's ``discovered_paths`` snapshot from
    the upstream. The panel renders the snapshot's mapped/missing/
    stale state; this endpoint is what the "Discover now" admin
    button calls. Returns the new snapshot.
    """
    manager = _manager(session, registry, bus)
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    await _snapshot_discovered_paths(manager, integration)
    await session.commit()
    return {
        "integration_id": integration.id,
        "discovered_paths": integration.discovered_paths or [],
    }


@router.post(
    "/{integration_id}/sync-tags",
    summary="Pull tags from the upstream and reconcile them with media_tags",
)
async def sync_tags(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> dict:
    from app.integrations.tag_sync import IntegrationTagSync

    manager = _manager(session, registry, bus)
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")
    tags = await manager.sync_tags(integration)
    report = await IntegrationTagSync(session=session, event_bus=bus).apply(
        integration, tags
    )
    return {
        "integration_id": report.integration_id,
        "inserted": report.inserted,
        "removed": report.removed,
        "title_count": report.title_count,
        "skipped_no_path": report.skipped_no_path,
    }


@router.post(
    "/{integration_id}/webhook-secret",
    summary="Stage 19: generate (or rotate) the webhook HMAC secret",
)
async def generate_webhook_secret(
    integration_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> dict[str, Any]:
    """Generate (or rotate) a per-integration webhook secret.

    The plaintext is returned in the response ONCE — the row holds
    only the ciphertext. Operators must copy the value into the
    upstream's webhook configuration immediately; we cannot fetch
    it again later.

    Idempotency: each call generates a fresh secret and replaces
    the previous one. The endpoint is admin-only because rotating
    is destructive (existing upstream signatures will start
    failing until the upstream's config is updated).
    """
    import secrets

    from app.security.secrets import get_secret_box

    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError("Integration not found")

    # 32 bytes of entropy → 64-char hex string. Comfortably above
    # the practical brute-force threshold for HMAC-SHA256.
    plaintext = secrets.token_hex(32)
    box = get_secret_box()
    integration.webhook_secret_ciphertext = box.encrypt_dict(
        {"value": plaintext}
    )
    await session.commit()
    return {
        "integration_id": integration.id,
        "webhook_secret": plaintext,
        "webhook_url_suffix": f"/api/v1/webhooks/{integration.kind}/{integration.id}",
        "instructions": (
            "Copy this secret into the upstream service's webhook "
            "configuration. Set the signature header to "
            "X-Auditarr-Signature with format sha256=<hex>. "
            "This value is NOT retrievable again — store it now."
        ),
    }
