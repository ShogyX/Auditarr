"""First-boot admin bootstrap.

If ``AUDITARR_BOOTSTRAP_ADMIN_USERNAME`` and ``AUDITARR_BOOTSTRAP_ADMIN_PASSWORD``
are set in the environment AND no users exist yet, the lifespan creates an
admin account so the operator never gets locked out of a fresh install.
"""

from __future__ import annotations

import os

from app.core.logging import get_logger
from app.security import Role, hash_password
from app.services.repositories import UserRepository
from app.storage.database import Database

log = get_logger("auditarr.bootstrap", category="security")


async def bootstrap_admin_if_needed(database: Database) -> None:
    username = os.environ.get("AUDITARR_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = os.environ.get("AUDITARR_BOOTSTRAP_ADMIN_PASSWORD", "")
    email = os.environ.get(
        "AUDITARR_BOOTSTRAP_ADMIN_EMAIL", f"{username}@auditarr.local"
    ).strip()

    from app.models.user import User

    async with database.session() as session:
        repo = UserRepository(session)
        user_count = await repo.count()

    if user_count > 0:
        log.debug("bootstrap.skipped_existing_users")
        return

    if not username or not password:
        # No users AND no bootstrap envs — nobody can log in. Surface this
        # loudly so the operator notices on first run.
        log.warning(
            "bootstrap.empty_database_no_admin",
            hint=(
                "No users exist and AUDITARR_BOOTSTRAP_ADMIN_USERNAME / "
                "AUDITARR_BOOTSTRAP_ADMIN_PASSWORD are unset. Set them and "
                "restart, or POST to /api/v1/auth/register to create the "
                "first user (will be a regular user; promote it via SQL)."
            ),
        )
        return

    if len(password) < 12:
        log.warning(
            "bootstrap.admin_password_too_short",
            min_length=12,
            actual=len(password),
        )
        return

    async with database.session() as session:
        repo = UserRepository(session)
        # Re-check inside the same transaction to avoid races with another worker.
        if await repo.count() > 0:
            log.debug("bootstrap.skipped_existing_users")
            return
        admin = User(
            email=email.lower(),
            username=username.lower(),
            full_name="Administrator",
            password_hash=hash_password(password),
            role=Role.ADMIN.value,
            is_active=True,
            is_verified=True,
        )
        await repo.add(admin)
        await session.commit()
        log.info(
            "bootstrap.admin_created",
            username=admin.username,
            email=admin.email,
        )
