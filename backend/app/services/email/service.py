"""Email service.

Selects a provider per :class:`EmailSettings` and renders Jinja2 templates
shipped with the application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.logging import get_logger
from app.services.email.message import EmailMessage, EmailProvider
from app.services.email.providers import ConsoleEmailProvider, SmtpEmailProvider
from app.services.email.settings import EmailSettings

log = get_logger("auditarr.email.service", category="notifications")

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_provider(settings: EmailSettings) -> EmailProvider:
    if not settings.enabled or settings.backend == "console":
        return ConsoleEmailProvider()
    return SmtpEmailProvider(settings)


class EmailService:
    """High-level email API used by :class:`AuthService` and others."""

    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings
        self._provider: EmailProvider = _build_provider(settings)
        self._env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html", "xml"]),
            keep_trailing_newline=True,
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def send(self, message: EmailMessage) -> None:
        await self._provider.send(message)

    async def send_password_reset(
        self, *, to: str, full_name: str | None, token: str
    ) -> None:
        link = f"{self._settings.reset_link_base.rstrip('/')}/reset-password?token={token}"
        ctx: dict[str, Any] = {
            "name": full_name or to,
            "link": link,
            "from_name": self._settings.from_name,
        }
        text = self._env.get_template("password_reset.txt.j2").render(**ctx)
        html = self._env.get_template("password_reset.html.j2").render(**ctx)
        await self.send(
            EmailMessage(
                to=[to],
                subject="Reset your Auditarr password",
                text_body=text,
                html_body=html,
            )
        )

    async def healthcheck(self) -> bool:
        return await self._provider.healthcheck()
