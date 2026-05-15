"""Email backend implementations."""

from app.services.email.providers.console import ConsoleEmailProvider
from app.services.email.providers.smtp import SmtpEmailProvider

__all__ = ["ConsoleEmailProvider", "SmtpEmailProvider"]
