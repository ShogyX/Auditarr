"""Permission helper tests."""

from __future__ import annotations

from app.security.permissions import PERMISSIONS, ROLE_DEFAULTS, Role, role_has


def test_admin_has_everything() -> None:
    for perm in PERMISSIONS:
        assert role_has(Role.ADMIN, perm) is True
    assert role_has(Role.ADMIN, "made.up.permission") is True


def test_user_has_read_baseline() -> None:
    assert role_has(Role.USER, "media.read") is True
    assert role_has(Role.USER, "media.write") is False


def test_role_defaults_subset_of_permissions() -> None:
    for perm in ROLE_DEFAULTS[Role.USER]:
        assert perm in PERMISSIONS
