"""Apprise notification channel.

`Apprise <https://github.com/caronc/apprise>`_ is a single library that
speaks ~70 notification protocols (Telegram, Pushover, Matrix, Gotify,
MSTeams, etc.). Rather than duplicating each one, we expose a single
``apprise`` channel whose config carries an Apprise URL.

The Apprise library is an optional dependency: if it's not installed,
the provider returns a friendly failure rather than ImportError'ing on
process start. Operators who need apprise can ``pip install apprise``
inside the container; non-users pay no startup cost.
"""

from __future__ import annotations

from app.notifications.types import (
    ChannelConfig,
    DeliveryReport,
    NotificationMessage,
)


class AppriseNotificationProvider:
    kind = "apprise"
    label = "Apprise (telegram, pushover, matrix, etc.)"
    config_schema = {
        "type": "object",
        "required": ["urls"],
        "properties": {
            "urls": {
                "type": "string",
                "title": "Apprise URL(s)",
                "description": (
                    "One or more Apprise URLs, newline- or comma-separated. "
                    "See https://github.com/caronc/apprise/wiki for the URL "
                    "format for your destination."
                ),
            },
        },
    }
    secret_fields: tuple[str, ...] = ()

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        try:
            import apprise  # type: ignore[import-not-found]
        except ImportError:
            return DeliveryReport(
                status="failed",
                detail=(
                    "The 'apprise' package is not installed. Install it in "
                    "the container with `pip install apprise` to use this "
                    "channel."
                ),
            )

        raw_urls = str(config.options.get("urls", "")).strip()
        if not raw_urls:
            return DeliveryReport(status="failed", detail="No Apprise URLs configured")
        urls = [u.strip() for u in raw_urls.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            return DeliveryReport(status="failed", detail="No Apprise URLs configured")

        try:
            apobj = apprise.Apprise()
            for url in urls:
                if not apobj.add(url):
                    return DeliveryReport(
                        status="failed",
                        detail=f"Apprise rejected URL: {url[:80]}",
                    )
            # ``apobj.notify`` runs sync and blocks on network I/O; offload
            # to a worker thread so the dispatcher's async loop stays free.
            import asyncio

            ok = await asyncio.to_thread(
                apobj.notify, body=message.body, title=message.subject
            )
        except Exception as exc:  # noqa: BLE001
            return DeliveryReport(status="failed", detail=str(exc)[:500])
        if not ok:
            return DeliveryReport(
                status="failed",
                detail="Apprise reported that one or more destinations failed.",
            )
        return DeliveryReport(
            status="sent",
            detail=f"Apprise delivered to {len(urls)} destination(s)",
        )
