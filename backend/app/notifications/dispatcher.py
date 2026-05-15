"""Notification dispatcher.

Takes a structured alert (severity + context) and:

1. Pulls every enabled channel.
2. Filters channels whose ``min_severity_rank`` exceeds the alert's rank.
3. Renders subject + body via :mod:`app.notifications.templating`.
4. Calls the channel's provider to deliver.
5. Persists a :class:`NotificationDelivery` row per attempt (including
   ``skipped`` rows for channels filtered out by the threshold).

The dispatcher is the only place that knows how to write
``NotificationDelivery`` rows. Callers (the rules engine, the manual
test-send endpoint) call :meth:`dispatch` and never touch the delivery
log directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.registry import ServiceRegistry
from app.events.bus import EventBus
from app.models.notification_channel import NotificationChannel
from app.models.notification_delivery import NotificationDelivery
from app.notifications.manager import NotificationManager
from app.notifications.templating import render_body, render_subject
from app.notifications.types import NotificationMessage
from app.rules.schema import SEVERITY_LEVELS
from app.security.secrets import get_secret_box
from app.services.repositories.notification import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
)
from app.utils.datetime import utcnow

log = get_logger("auditarr.notifications.dispatcher", category="notifications")


@dataclass(slots=True)
class DispatchReport:
    """What ``dispatch()`` did across all channels for one alert."""

    delivery_ids: list[str]
    sent: int
    failed: int
    skipped: int


class NotificationDispatcher:
    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: ServiceRegistry,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._bus = event_bus
        self._channels = NotificationChannelRepository(session)
        self._deliveries = NotificationDeliveryRepository(session)
        self._manager = NotificationManager(
            session=session,
            registry=registry,
            secret_box=get_secret_box(),
            event_bus=event_bus,
        )

    # ── Public API ──────────────────────────────────────────────
    async def dispatch(
        self,
        *,
        severity: str,
        rule_id: str | None,
        rule_name: str | None,
        media_file_id: str | None,
        context: dict[str, Any] | None = None,
        message_override: str | None = None,
    ) -> DispatchReport:
        """Fan an alert out to every eligible channel.

        ``message_override`` is the rule's ``notify.message`` if set;
        when ``None`` the default template body renders without it.
        """
        rank = SEVERITY_LEVELS.get(severity, 0)
        now = utcnow()
        ctx = dict(context or {})
        variables = self._variables(
            severity=severity,
            rank=rank,
            rule_id=rule_id,
            rule_name=rule_name,
            media_file_id=media_file_id,
            context=ctx,
            message=message_override,
            now=now,
        )

        channels = await self._channels.list_all(enabled_only=True)
        report = DispatchReport(delivery_ids=[], sent=0, failed=0, skipped=0)

        for channel in channels:
            delivery = await self._deliver_one(
                channel=channel,
                severity=severity,
                rank=rank,
                variables=variables,
                context=ctx,
                now=now,
            )
            report.delivery_ids.append(delivery.id)
            if delivery.status == "sent":
                report.sent += 1
            elif delivery.status == "failed":
                report.failed += 1
            elif delivery.status == "skipped":
                report.skipped += 1

        return report

    async def test_send(
        self,
        channel: NotificationChannel,
        *,
        severity: str = "info",
        message_override: str | None = "This is a test notification from Auditarr.",
    ) -> NotificationDelivery:
        """Manual test from the channel edit dialog."""
        rank = SEVERITY_LEVELS.get(severity, 0)
        now = utcnow()
        variables = self._variables(
            severity=severity,
            rank=rank,
            rule_id=None,
            rule_name="(manual test)",
            media_file_id=None,
            context={},
            message=message_override,
            now=now,
        )
        return await self._deliver_one(
            channel=channel,
            severity=severity,
            rank=rank,
            variables=variables,
            context={"trigger": "manual_test"},
            now=now,
            ignore_threshold=True,
        )

    # ── Internals ───────────────────────────────────────────────
    async def _deliver_one(
        self,
        *,
        channel: NotificationChannel,
        severity: str,
        rank: int,
        variables: dict[str, Any],
        context: dict[str, Any],
        now,
        ignore_threshold: bool = False,
    ) -> NotificationDelivery:
        subject = render_subject(
            channel.config.get("subject_template") if channel.config else None,
            variables,
        )
        body = render_body(
            channel.config.get("body_template") if channel.config else None,
            variables,
        )

        # Below-threshold: log a ``skipped`` row so the audit log shows
        # *why* a channel didn't fire, then bail before contacting any
        # network.
        if not ignore_threshold and rank < channel.min_severity_rank:
            delivery = NotificationDelivery(
                channel_id=channel.id,
                channel_name=channel.name,
                channel_kind=channel.kind,
                status="skipped",
                severity=severity,
                subject=subject[:255],
                body=body,
                context=context,
                attempted_at=now,
                completed_at=now,
                duration_ms=0,
            )
            await self._deliveries.add(delivery)
            return delivery

        # Send.
        delivery = NotificationDelivery(
            channel_id=channel.id,
            channel_name=channel.name,
            channel_kind=channel.kind,
            status="pending",
            severity=severity,
            subject=subject[:255],
            body=body,
            context=context,
            attempted_at=now,
        )
        await self._deliveries.add(delivery)

        start = time.monotonic()
        report = await self._manager.send(
            channel,
            NotificationMessage(
                subject=delivery.subject,
                body=delivery.body,
                severity=severity,
                severity_rank=rank,
                context=context,
            ),
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        delivery.status = report.status
        delivery.completed_at = utcnow()
        delivery.duration_ms = duration_ms
        if report.status == "failed":
            delivery.error = report.detail
        # Mirror the latest delivery onto the channel row for the UI's
        # quick status indicator. The audit table is the source of truth.
        channel.last_delivery_status = report.status
        channel.last_delivery_at = delivery.completed_at
        channel.last_delivery_error = (
            report.detail if report.status == "failed" else None
        )

        await self._session.flush()
        if self._bus is not None:
            await self._bus.emit(
                "notification.sent" if report.status == "sent" else "notification.failed",
                {
                    "delivery_id": delivery.id,
                    "channel_id": channel.id,
                    "channel_kind": channel.kind,
                    "status": report.status,
                    "severity": severity,
                    "duration_ms": duration_ms,
                },
                source="notifications",
            )
        return delivery

    @staticmethod
    def _variables(
        *,
        severity: str,
        rank: int,
        rule_id: str | None,
        rule_name: str | None,
        media_file_id: str | None,
        context: dict[str, Any],
        message: str | None,
        now,
    ) -> dict[str, Any]:
        return {
            "severity": severity,
            "severity_rank": rank,
            "rule_id": rule_id or "",
            "rule_name": rule_name or "(unnamed rule)",
            "media_file_id": media_file_id or "",
            "path": context.get("path", ""),
            "filename": context.get("filename", ""),
            "library_name": context.get("library_name", ""),
            "message": message or "",
            "time": now.isoformat(),
        }
