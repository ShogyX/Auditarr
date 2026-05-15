"""Email subsystem."""

from app.services.email.message import EmailMessage, EmailProvider
from app.services.email.service import EmailService
from app.services.email.settings import EmailSettings, get_email_settings

__all__ = [
    "EmailMessage",
    "EmailProvider",
    "EmailService",
    "EmailSettings",
    "get_email_settings",
]
