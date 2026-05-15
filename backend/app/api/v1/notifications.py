"""Notifications router (``/api/v1/notifications``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import ConflictError, NotFoundError
from app.models.notification_channel import NotificationChannel
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.manager import NotificationManager
from app.schemas.notifications import (
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    NotificationDeliveryRead,
    NotificationKind,
    NotificationTestRequest,
)
from app.security.secrets import get_secret_box
from app.services.repositories import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _manager(session, registry, bus) -> NotificationManager:
    return NotificationManager(
        session=session,
        registry=registry,
        secret_box=get_secret_box(),
        event_bus=bus,
    )


def _to_read(channel: NotificationChannel) -> NotificationChannelRead:
    return NotificationChannelRead.model_validate(channel)


# ── Kind directory ──────────────────────────────────────────
@router.get(
    "/kinds",
    response_model=list[NotificationKind],
    summary="List available notification channel kinds",
)
async def list_kinds(
    _user: CurrentUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> list[NotificationKind]:
    providers = _manager(session, registry, bus).known_kinds()
    return [
        NotificationKind(
            kind=p.kind,
            label=p.label,
            config_schema=p.config_schema,
            secret_fields=list(p.secret_fields),
        )
        for p in providers
    ]


# ── Channel CRUD ────────────────────────────────────────────
@router.get(
    "",
    response_model=list[NotificationChannelRead],
    summary="List notification channels",
)
async def list_channels(
    _user: CurrentUser, session: SessionDep
) -> list[NotificationChannelRead]:
    rows = await NotificationChannelRepository(session).list_all()
    return [_to_read(r) for r in rows]


# ── Deliveries log ──────────────────────────────────────────
# IMPORTANT: this route MUST be declared before any route that uses
# ``/{channel_id}`` as its first path segment, or FastAPI's path-param
# match will treat the literal string ``deliveries`` as a channel_id and
# every ``GET /api/v1/notifications/deliveries`` request will resolve
# to ``get_channel`` and return 404.
@router.get(
    "/deliveries",
    response_model=list[NotificationDeliveryRead],
    summary="List recent notification deliveries",
)
async def list_deliveries(
    _user: CurrentUser,
    session: SessionDep,
    channel_id: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[NotificationDeliveryRead]:
    rows = await NotificationDeliveryRepository(session).list_recent(
        channel_id=channel_id, status=status_, limit=limit
    )
    return [NotificationDeliveryRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=NotificationChannelRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification channel",
)
async def create_channel(
    body: NotificationChannelCreate,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> NotificationChannelRead:
    mgr = _manager(session, registry, bus)
    mgr.validate_config_against_schema(body.kind, body.config, body.secrets)
    repo = NotificationChannelRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("A channel with that name already exists")
    channel = NotificationChannel(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        config=body.config,
        min_severity_rank=body.min_severity_rank,
    )
    await mgr.encrypt_and_set_secrets(channel, body.secrets)
    await repo.add(channel)
    return _to_read(channel)


@router.get(
    "/{channel_id}",
    response_model=NotificationChannelRead,
    summary="Get a notification channel",
)
async def get_channel(
    channel_id: str, _user: CurrentUser, session: SessionDep
) -> NotificationChannelRead:
    channel = await NotificationChannelRepository(session).get(channel_id)
    if channel is None:
        raise NotFoundError("Channel not found")
    return _to_read(channel)


@router.patch(
    "/{channel_id}",
    response_model=NotificationChannelRead,
    summary="Update a notification channel",
)
async def update_channel(
    channel_id: str,
    body: NotificationChannelUpdate,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> NotificationChannelRead:
    repo = NotificationChannelRepository(session)
    channel = await repo.get(channel_id)
    if channel is None:
        raise NotFoundError("Channel not found")

    mgr = _manager(session, registry, bus)
    if body.config is not None or body.secrets is not None:
        new_config = body.config if body.config is not None else channel.config
        # Schema validation requires secrets too — pass {} when the
        # operator isn't rotating them. The provider's secret_fields are
        # advisory at this layer, so missing secrets only logs.
        mgr.validate_config_against_schema(
            channel.kind, new_config or {}, body.secrets or {}
        )
        channel.config = new_config or {}

    if body.secrets is not None:
        await mgr.encrypt_and_set_secrets(channel, body.secrets)
    if body.name is not None:
        channel.name = body.name
    if body.enabled is not None:
        channel.enabled = body.enabled
    if body.min_severity_rank is not None:
        channel.min_severity_rank = body.min_severity_rank

    await session.flush()
    return _to_read(channel)


@router.delete(
    "/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a notification channel (delivery log entries retain channel_name)",
)
async def delete_channel(
    channel_id: str, _admin: AdminUser, session: SessionDep
) -> None:
    repo = NotificationChannelRepository(session)
    channel = await repo.get(channel_id)
    if channel is None:
        raise NotFoundError("Channel not found")
    await repo.delete(channel)


# ── Test send ───────────────────────────────────────────────
@router.post(
    "/{channel_id}/test",
    response_model=NotificationDeliveryRead,
    summary="Send a test notification through a channel",
)
async def test_channel(
    channel_id: str,
    body: NotificationTestRequest,
    _admin: AdminUser,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> NotificationDeliveryRead:
    channel = await NotificationChannelRepository(session).get(channel_id)
    if channel is None:
        raise NotFoundError("Channel not found")
    dispatcher = NotificationDispatcher(
        session=session, registry=registry, event_bus=bus
    )
    delivery = await dispatcher.test_send(
        channel, severity=body.severity, message_override=body.message
    )
    await session.commit()
    return NotificationDeliveryRead.model_validate(delivery)
