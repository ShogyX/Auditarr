"""SMTP email provider.

Uses ``aiosmtplib``-style semantics through the stdlib ``smtplib`` run inside a
worker thread. Avoids adding another dependency for what is in practice
infrequent traffic; if higher throughput is ever needed, swap the provider.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage as _StdEmailMessage

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.services.email.message import EmailMessage
from app.services.email.settings import EmailSettings

log = get_logger("auditarr.email.smtp", category="notifications")


class SmtpEmailProvider:
    name = "smtp"

    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_blocking, message)

    def _send_blocking(self, message: EmailMessage) -> None:
        msg = _StdEmailMessage()
        msg["From"] = f"{self._settings.from_name} <{self._settings.from_email}>"
        msg["To"] = ", ".join(message.to)
        msg["Subject"] = message.subject
        for k, v in message.headers.items():
            msg[k] = v
        msg.set_content(message.text_body)
        if message.html_body:
            msg.add_alternative(message.html_body, subtype="html")

        try:
            if self._settings.use_ssl:
                client_cls: type[smtplib.SMTP] = smtplib.SMTP_SSL
            else:
                client_cls = smtplib.SMTP
            with client_cls(self._settings.host, self._settings.port, timeout=15) as smtp:
                if self._settings.use_tls and not self._settings.use_ssl:
                    smtp.starttls()
                if self._settings.username and self._settings.password:
                    smtp.login(self._settings.username, self._settings.password)
                smtp.send_message(msg)
            log.info("email.smtp_sent", to=message.to, subject=message.subject)
        except (smtplib.SMTPException, OSError) as exc:
            log.error("email.smtp_failed", error=str(exc))
            raise IntegrationError(
                "Email send failed", details={"reason": str(exc)}
            ) from exc

    async def healthcheck(self) -> bool:
        if not self._settings.host:
            return False
        try:
            await asyncio.to_thread(
                lambda: smtplib.SMTP(
                    self._settings.host, self._settings.port, timeout=5
                ).quit()
            )
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("email.smtp_unreachable", error=str(exc))
            return False
