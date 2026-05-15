"""Email notification channel (SMTP).

Reuses :class:`app.services.email.providers.smtp.SmtpEmailProvider` so
SMTP semantics live in one place. We build a per-channel
:class:`EmailSettings` from the operator's config rather than reading
the global SMTP env vars — channels stand on their own and don't depend
on ``AUDITARR_SMTP_*`` being set.
"""

from __future__ import annotations

from app.notifications.types import (
    ChannelConfig,
    DeliveryReport,
    NotificationMessage,
)


class EmailNotificationProvider:
    kind = "email"
    label = "Email (SMTP)"
    config_schema = {
        "type": "object",
        "required": ["to", "smtp_host"],
        "properties": {
            "to": {
                "type": "string",
                "title": "Recipient(s)",
                "description": "Comma-separated list of recipient addresses.",
            },
            "from_email": {
                "type": "string",
                "title": "From address",
                "default": "auditarr@localhost",
            },
            "from_name": {
                "type": "string",
                "title": "From name",
                "default": "Auditarr",
            },
            "smtp_host": {"type": "string", "title": "SMTP host"},
            "smtp_port": {
                "type": "integer",
                "title": "SMTP port",
                "default": 587,
            },
            "use_tls": {
                "type": "boolean",
                "title": "STARTTLS",
                "default": True,
            },
            "use_ssl": {
                "type": "boolean",
                "title": "SSL on connect",
                "default": False,
            },
        },
    }
    secret_fields = ("smtp_username", "smtp_password")

    async def send(
        self, config: ChannelConfig, message: NotificationMessage
    ) -> DeliveryReport:
        # Local imports keep optional pydantic-settings overhead off the
        # hot path until a channel actually fires.
        from app.services.email.message import EmailMessage
        from app.services.email.providers.smtp import SmtpEmailProvider
        from app.services.email.settings import EmailSettings

        opts = config.options
        secrets = config.secrets
        recipients = [
            addr.strip()
            for addr in str(opts.get("to", "")).split(",")
            if addr.strip()
        ]
        if not recipients:
            return DeliveryReport(status="failed", detail="No recipient configured")

        try:
            settings = EmailSettings(
                enabled=True,
                backend="smtp",
                host=str(opts["smtp_host"]),
                port=int(opts.get("smtp_port", 587)),
                username=secrets.get("smtp_username") or None,
                password=secrets.get("smtp_password") or None,
                use_tls=bool(opts.get("use_tls", True)),
                use_ssl=bool(opts.get("use_ssl", False)),
                from_email=str(opts.get("from_email", "auditarr@localhost")),
                from_name=str(opts.get("from_name", "Auditarr")),
            )
            provider = SmtpEmailProvider(settings)
            await provider.send(
                EmailMessage(
                    to=recipients,
                    subject=message.subject,
                    text_body=message.body,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return DeliveryReport(status="failed", detail=str(exc)[:500])
        return DeliveryReport(
            status="sent", detail=f"Delivered to {len(recipients)} recipient(s)"
        )
