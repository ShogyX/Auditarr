"""Built-in notification channel providers."""

from app.notifications.providers.apprise import AppriseNotificationProvider
from app.notifications.providers.email import EmailNotificationProvider
from app.notifications.providers.http import (
    DiscordNotificationProvider,
    SlackNotificationProvider,
    WebhookNotificationProvider,
)

__all__ = [
    "AppriseNotificationProvider",
    "DiscordNotificationProvider",
    "EmailNotificationProvider",
    "SlackNotificationProvider",
    "WebhookNotificationProvider",
]


def builtin_providers() -> list:
    """Return one instance of each built-in provider."""
    return [
        EmailNotificationProvider(),
        WebhookNotificationProvider(),
        DiscordNotificationProvider(),
        SlackNotificationProvider(),
        AppriseNotificationProvider(),
    ]
