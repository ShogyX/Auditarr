"""Authentication service.

Coordinates the user, refresh-session, password-reset, and audit repositories
plus the password hasher and token service. Returns DTOs (not ORM rows) so
the API layer never hands raw models to clients.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.settings import Settings
from app.events.bus import EventBus
from app.models.password_reset import PasswordResetToken
from app.models.session import RefreshSession
from app.models.user import User
from app.security import (
    Role,
    TokenService,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.services.audit_service import AuditService
from app.services.email import EmailService
from app.services.repositories import (
    PasswordResetRepository,
    RefreshSessionRepository,
    UserRepository,
)

log = get_logger("auditarr.auth", category="security")

RESET_TOKEN_TTL_MINUTES = 30
# Stage 12 (v1.7) — terminal-OTP path uses a tighter TTL than
# the long email token. Plan §577 specifies 15 minutes; the
# shorter OTP (71 bits of entropy) doesn't need the longer
# window the email path uses for the much longer token.
TERMINAL_OTP_TTL_MINUTES = 15


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"


@dataclass(slots=True)
class AuthContext:
    """Information about the actor making an auth request."""

    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        tokens: TokenService,
        email: EmailService,
        event_bus: EventBus,
    ) -> None:
        self._session = session
        self._settings = settings
        self._tokens = tokens
        self._email = email
        self._bus = event_bus
        self._users = UserRepository(session)
        self._sessions = RefreshSessionRepository(session)
        self._resets = PasswordResetRepository(session)
        self._audit = AuditService(session)

    # ── Registration / bootstrap ──────────────────────────────
    async def register(
        self,
        *,
        email: str,
        username: str,
        password: str,
        full_name: str | None = None,
        role: Role = Role.USER,
        ctx: AuthContext | None = None,
    ) -> User:
        email_norm = email.strip().lower()
        username_norm = username.strip().lower()
        _validate_password(password)

        if await self._users.get_by_email(email_norm):
            raise ConflictError("A user with that email already exists")
        if await self._users.get_by_username(username_norm):
            raise ConflictError("A user with that username already exists")

        user = User(
            email=email_norm,
            username=username_norm,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role.value,
        )
        await self._users.add(user)
        await self._audit.record(
            "auth.register",
            actor_id=user.id,
            actor_label=user.username,
            target_type="user",
            target_id=user.id,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
            metadata={"role": user.role},
        )
        await self._bus.emit(
            "system.user_registered",
            {"id": user.id, "username": user.username},
            source="auth",
        )
        return user

    # ── Login / refresh / logout ──────────────────────────────
    async def login(
        self, *, login: str, password: str, ctx: AuthContext | None = None
    ) -> tuple[User, TokenPair]:
        user = await self._users.find_by_login(login)
        if user is None or not user.is_active:
            await self._audit.record(
                "auth.login_failed",
                actor_label=login,
                ip_address=(ctx.ip_address if ctx else None),
                request_id=(ctx.request_id if ctx else None),
                metadata={"reason": "unknown_or_inactive"},
            )
            raise AuthenticationError("Invalid credentials")

        if not verify_password(password, user.password_hash):
            await self._audit.record(
                "auth.login_failed",
                actor_id=user.id,
                actor_label=user.username,
                ip_address=(ctx.ip_address if ctx else None),
                request_id=(ctx.request_id if ctx else None),
                metadata={"reason": "bad_password"},
            )
            raise AuthenticationError("Invalid credentials")

        # Opportunistically upgrade outdated argon2 parameters.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        await self._users.touch_login(user)
        pair = await self._issue_pair(user, ctx)
        await self._audit.record(
            "auth.login",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
        )
        return user, pair

    async def refresh(
        self, refresh_token: str, ctx: AuthContext | None = None
    ) -> tuple[User, TokenPair]:
        from app.security.tokens import REFRESH
        from app.utils.datetime import is_past

        claims = self._tokens.decode(refresh_token, expected_type=REFRESH)
        record = await self._sessions.get(claims.token_id)
        if record is None or record.is_revoked or is_past(record.expires_at):
            raise AuthenticationError("Refresh token is no longer valid")

        user = await self._users.get(claims.subject)
        if user is None or not user.is_active:
            raise AuthenticationError("User is no longer active")
        if user.token_version != claims.token_version:
            raise AuthenticationError("Refresh token has been invalidated")

        # Rotate: revoke the old refresh, issue a new pair.
        await self._sessions.revoke(record.jti)
        pair = await self._issue_pair(user, ctx)
        await self._audit.record(
            "auth.refresh",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
        )
        return user, pair

    async def logout(
        self, refresh_token: str | None, *, ctx: AuthContext | None = None
    ) -> None:
        from app.security.tokens import REFRESH

        if not refresh_token:
            return
        try:
            claims = self._tokens.decode(refresh_token, expected_type=REFRESH)
        except AuthenticationError:
            return
        revoked = await self._sessions.revoke(claims.token_id)
        if revoked:
            await self._audit.record(
                "auth.logout",
                actor_id=claims.subject,
                ip_address=(ctx.ip_address if ctx else None),
                request_id=(ctx.request_id if ctx else None),
            )

    async def logout_all(self, user: User, *, ctx: AuthContext | None = None) -> int:
        count = await self._sessions.revoke_for_user(user.id)
        await self._audit.record(
            "auth.logout_all",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
            metadata={"sessions_revoked": count},
        )
        return count

    # ── Password management ───────────────────────────────────
    async def change_password(
        self,
        user: User,
        *,
        current_password: str,
        new_password: str,
        ctx: AuthContext | None = None,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect")
        _validate_password(new_password)
        user.password_hash = hash_password(new_password)
        # Stage 12 (plan §581) — a successful password change
        # clears the forced-change flag. Whether the user
        # arrived here via the terminal-OTP flow or just
        # ordinarily updating their password, the next login
        # should NOT be blocked by the gate.
        user.must_change_password = False
        await self._users.bump_token_version(user)
        await self._sessions.revoke_for_user(user.id)
        await self._audit.record(
            "auth.password_changed",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
        )

    async def update_profile(
        self,
        user: User,
        *,
        email: str | None,
        full_name: str | None,
        ctx: AuthContext | None = None,
    ) -> User:
        """Patch the user's profile fields.

        ``email`` and ``full_name`` are both optional. Passing
        ``None`` for either leaves the existing value alone; passing
        a value updates it. Empty-string ``email`` is rejected (the
        schema validator already enforces ``EmailStr``). Empty-string
        ``full_name`` clears the field — handled via passing an empty
        string explicitly from the caller.

        Email changes that collide with another account raise
        :class:`ValidationError` to preserve the unique constraint
        message-shape callers expect.
        """
        changed: dict[str, str | None] = {}

        if email is not None:
            email_norm = email.strip().lower()
            if email_norm != user.email:
                existing = await self._users.get_by_email(email_norm)
                if existing is not None and existing.id != user.id:
                    raise ValidationError(
                        "Another account is already using that email."
                    )
                changed["email"] = email_norm
                user.email = email_norm
                # Email change invalidates email-verified state; the
                # operator must re-verify. Until verification flow
                # ships in a later stage this just resets the flag.
                user.is_verified = False

        if full_name is not None:
            # full_name is nullable on the column — treat empty
            # string from the operator as "clear it".
            new_name = full_name.strip() or None
            if new_name != user.full_name:
                changed["full_name"] = new_name
                user.full_name = new_name

        if changed:
            await self._users.touch(user)
            await self._audit.record(
                "auth.profile_updated",
                actor_id=user.id,
                actor_label=user.username,
                ip_address=(ctx.ip_address if ctx else None),
                request_id=(ctx.request_id if ctx else None),
                metadata={"fields": sorted(changed.keys())},
            )
        return user

    async def request_password_reset(
        self, *, email: str, ctx: AuthContext | None = None
    ) -> None:
        email_norm = email.strip().lower()
        user = await self._users.get_by_email(email_norm)
        # Always pretend success to avoid email enumeration.
        if user is None or not user.is_active:
            await self._audit.record(
                "auth.password_reset_requested",
                actor_label=email_norm,
                ip_address=(ctx.ip_address if ctx else None),
                request_id=(ctx.request_id if ctx else None),
                metadata={"matched": False},
            )
            return

        # Stage 12 (v1.7) — split the path based on email
        # provider configuration. When email isn't enabled,
        # we generate a short human-typable OTP (12 base64
        # chars) and print it to the operator's terminal via
        # both a WARNING log AND ``print()`` (addendum B.9:
        # production INFO logs may not go to stdout; the
        # bordered banner must always reach the operator).
        # We persist the hash with ``must_change_on_use=True``
        # so the post-reset login flow forces a password
        # change.
        if self._email.enabled:
            token = secrets.token_urlsafe(48)
            must_change_on_use = False
            ttl_minutes = RESET_TOKEN_TTL_MINUTES
        else:
            # Operator-typeable OTP. base64 (no '/' or '+')
            # at 9 bytes → 12 chars. URL-safe so no special
            # chars to escape when the operator types it.
            token = secrets.token_urlsafe(9)
            must_change_on_use = True
            # Tighter TTL for the terminal path — the OTP is
            # shorter (~71 bits vs ~384 bits for the email
            # token) so we minimize the brute-force window.
            ttl_minutes = TERMINAL_OTP_TTL_MINUTES

        token_hash = _hash_reset_token(token)
        expires = _dt.datetime.now(_dt.UTC) + _dt.timedelta(minutes=ttl_minutes)

        await self._resets.delete_for_user(user.id)
        await self._resets.add(
            PasswordResetToken(
                token_hash=token_hash,
                user_id=user.id,
                expires_at=expires,
                must_change_on_use=must_change_on_use,
            )
        )

        if self._email.enabled:
            try:
                await self._email.send_password_reset(
                    to=user.email, full_name=user.full_name, token=token
                )
            except Exception as exc:  # noqa: BLE001
                # Don't leak failure to the caller; just log + audit.
                log.error("auth.reset_email_failed", error=str(exc))
        else:
            # Stage 12 (plan §572 + addendum B.9) — terminal
            # OTP banner. WARNING log so it lands above the
            # operator's default INFO threshold, AND print()
            # so it always reaches stdout regardless of log
            # config. The banner shape matches the plan
            # spec verbatim.
            _print_otp_banner(
                username=user.username,
                otp=token,
                ttl_minutes=ttl_minutes,
            )

        await self._audit.record(
            "auth.password_reset_requested",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
            metadata={
                "matched": True,
                "delivery": "email" if self._email.enabled else "terminal_otp",
            },
        )

    async def confirm_password_reset(
        self,
        *,
        token: str,
        new_password: str,
        ctx: AuthContext | None = None,
    ) -> None:
        from app.utils.datetime import is_past

        _validate_password(new_password)
        record = await self._resets.get(_hash_reset_token(token))
        if record is None or record.is_used or is_past(record.expires_at):
            raise AuthenticationError("Reset token is invalid or has expired")

        user = await self._users.get(record.user_id)
        if user is None or not user.is_active:
            raise NotFoundError("User no longer exists")

        user.password_hash = hash_password(new_password)
        # Stage 12 (plan §581) — flag the user for forced
        # password change on next login when the consumed
        # token came from the terminal-OTP path. The flag
        # gets cleared by ``change_password`` (below).
        if record.must_change_on_use:
            user.must_change_password = True
        await self._users.bump_token_version(user)
        await self._sessions.revoke_for_user(user.id)
        await self._resets.mark_used(record)
        await self._audit.record(
            "auth.password_reset_confirmed",
            actor_id=user.id,
            actor_label=user.username,
            ip_address=(ctx.ip_address if ctx else None),
            request_id=(ctx.request_id if ctx else None),
            metadata={
                "must_change_on_use": record.must_change_on_use,
            },
        )

    # ── Helpers ───────────────────────────────────────────────
    async def _issue_pair(
        self, user: User, ctx: AuthContext | None
    ) -> TokenPair:
        access = self._tokens.issue_access(
            user.id, token_version=user.token_version
        )
        refresh = self._tokens.issue_refresh(
            user.id, token_version=user.token_version
        )
        from app.security.tokens import REFRESH

        claims = self._tokens.decode(refresh, expected_type=REFRESH)
        await self._sessions.add(
            RefreshSession(
                jti=claims.token_id,
                user_id=user.id,
                expires_at=claims.expires_at,
                ip_address=(ctx.ip_address if ctx else None),
                user_agent=(ctx.user_agent if ctx else None),
            )
        )
        return TokenPair(access_token=access, refresh_token=refresh)


def _validate_password(password: str) -> None:
    """Minimum policy: length 12, not all whitespace."""
    if password is None or len(password.strip()) == 0:
        raise ValidationError("Password is required")
    if len(password) < 12:
        raise ValidationError("Password must be at least 12 characters")


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Stage 12 (v1.7) — terminal OTP banner ───────────────────────


def _print_otp_banner(*, username: str, otp: str, ttl_minutes: int) -> None:
    """Emit the password-reset OTP to the operator's terminal.

    Per addendum B.9: emit at WARNING level (so it lands above
    any default INFO threshold) AND via ``print()`` (so it
    always reaches stdout regardless of log configuration —
    operators in production may have INFO/WARNING redirected
    elsewhere). The duplication is intentional and minimal.

    Banner shape matches plan §572 verbatim. Width chosen so
    a 12-char OTP fits comfortably with surrounding context;
    the box-drawing characters are ASCII-compatible across all
    POSIX terminals.
    """
    # Build the banner. The plan spec uses 60-char inner width.
    width = 60
    line_top = "┌" + "─" * (width + 2) + "┐"
    line_mid = "└" + "─" * (width + 2) + "┘"

    def _pad(text: str) -> str:
        # Truncate or pad to the inner width so the right
        # border lines up.
        if len(text) > width:
            text = text[: width - 1] + "…"
        return "│ " + text.ljust(width) + " │"

    lines = [
        line_top,
        _pad(
            f"AUDITARR — Password reset request for user '{username}'"
        ),
        _pad(f"One-time password: {otp}"),
        _pad(
            f"Valid for {ttl_minutes} minutes. "
            "Operator must change on next login."
        ),
        line_mid,
    ]
    banner = "\n".join(lines)

    # WARNING-level structured log so the operator's log
    # aggregator catches it.
    log.warning(
        "auth.password_reset_terminal_otp",
        username=username,
        ttl_minutes=ttl_minutes,
        otp_length=len(otp),
        banner=banner,
    )

    # And direct stdout so the OTP always reaches the
    # operator's terminal even if log routing redirects
    # WARNING elsewhere.
    print("\n" + banner + "\n", flush=True)
