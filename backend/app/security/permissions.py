"""Role and permission primitives.

Stage 2 ships a minimal admin/user role split with explicit permission strings
that future stages (rules, integrations, plugins) can check against.
Permissions are dotted lowercase strings, matching the capability registry
naming convention.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """Coarse role tiers."""

    ADMIN = "admin"
    USER = "user"


# All permissions known to the system. Plugin authors MUST namespace under
# ``plugin.<id>.<action>`` rather than extending this list.
PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "users.read",
        "users.write",
        "media.read",
        "media.write",
        "rules.read",
        "rules.write",
        "automations.read",
        "automations.write",
        "integrations.read",
        "integrations.write",
        "notifications.read",
        "notifications.write",
        "settings.read",
        "settings.write",
        "updates.read",
        "updates.write",
    }
)


# Default grants per role. Admins always implicitly have *all* permissions
# (see :func:`role_has`); regular users get a read-only baseline.
ROLE_DEFAULTS: Final[dict[Role, frozenset[str]]] = {
    Role.ADMIN: PERMISSIONS,
    Role.USER: frozenset(
        {
            "media.read",
            "rules.read",
            "automations.read",
            "integrations.read",
            "notifications.read",
            "settings.read",
            "updates.read",
        }
    ),
}


def role_has(role: Role, permission: str) -> bool:
    """Return True if *role* implicitly grants *permission*."""
    if role is Role.ADMIN:
        return True
    return permission in ROLE_DEFAULTS.get(role, frozenset())
