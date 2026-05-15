"""Console email provider — useful for local dev and tests."""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.email.message import EmailMessage

log = get_logger("auditarr.email.console", category="notifications")


class ConsoleEmailProvider:
    name = "console"

    async def send(self, message: EmailMessage) -> None:
        log.info(
            "email.console_send",
            to=message.to,
            subject=message.subject,
            text_preview=message.text_body[:200],
        )

    async def healthcheck(self) -> bool:
        return True
