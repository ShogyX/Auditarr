"""Authentication dependencies.

Endpoints take ``CurrentUser`` to access the authenticated principal,
``AdminUser`` to require the admin role, or call ``require_permission(...)``
for fine-grained checks. ``OptionalUser`` returns ``None`` when no/invalid
credentials are supplied so endpoints can branch on auth state without
raising.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import EventBusDep, SessionDep, SettingsDep
from app.core.exceptions import (
    AuditarrError,
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.settings import Settings
from app.models.user import User
from app.security import ACCESS, Role, TokenService, role_has
from app.security.tokens import TokenClaims
from app.services.auth_service import AuthContext, AuthService
from app.services.email import EmailService, get_email_settings
from app.services.repositories import UserRepository

_log = get_logger("auditarr.auth_deps", category="security")

# auto_error=False so we can raise our own AuditarrError subclass.
_bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


# ── Service factories ────────────────────────────────────────────
def get_token_service(settings: SettingsDep) -> TokenService:
    return TokenService(settings)


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


def get_email_service() -> EmailService:
    return EmailService(get_email_settings())


EmailServiceDep = Annotated[EmailService, Depends(get_email_service)]


def get_auth_service(
    session: SessionDep,
    settings: SettingsDep,
    tokens: TokenServiceDep,
    email: EmailServiceDep,
    bus: EventBusDep,
) -> AuthService:
    return AuthService(
        session=session,
        settings=settings,
        tokens=tokens,
        email=email,
        event_bus=bus,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


# ── Auth context (ip / ua / request id) ──────────────────────────
def get_auth_context(
    request: Request,
    user_agent: Annotated[str | None, Header()] = None,
) -> AuthContext:
    return AuthContext(
        ip_address=_client_ip(request),
        user_agent=user_agent,
        request_id=getattr(request.state, "request_id", None),
    )


AuthContextDep = Annotated[AuthContext, Depends(get_auth_context)]


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Bearer-token resolution ─────────────────────────────────────
async def _resolve_user(
    session: AsyncSession, claims: TokenClaims
) -> User:
    # (Stage 1) The repository fetch can fail in three categories:
    #   1. Domain errors (``AuthenticationError`` etc.) — bubble up,
    #      the global handler maps them to the correct status.
    #   2. The user genuinely doesn't exist / is inactive — raise an
    #      authentication error so the client sees a clean 401.
    #   3. Anything else (transient DB connection error, etc.) — the
    #      logs previously showed ``unhandled errors in a TaskGroup``
    #      because ``HTTPBearer`` was running this in a Starlette
    #      dependency-resolution task group. Re-raising as
    #      ``ServiceUnavailableError`` lets the global handler emit a
    #      structured 503 instead of bubbling out of the request cycle
    #      with a 500. 503 is the right status — the request was
    #      well-formed; the dependency is just temporarily unavailable.
    try:
        user = await UserRepository(session).get(claims.subject)
    except AuditarrError:
        raise
    except Exception as exc:
        _log.warning(
            "auth.resolve_user_failed",
            subject=claims.subject,
            error=str(exc),
            exc_info=True,
        )
        raise ServiceUnavailableError(
            "Authentication backend temporarily unavailable",
            details={"hint": "retry the request after a short delay"},
        ) from exc

    if user is None or not user.is_active:
        raise AuthenticationError("User is no longer active")
    if user.token_version != claims.token_version:
        raise AuthenticationError("Token has been invalidated")
    return user


async def current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    session: SessionDep,
    tokens: TokenServiceDep,
    _settings: SettingsDep,
) -> User:
    if credentials is None:
        raise AuthenticationError("Authentication required")
    claims = tokens.decode(credentials.credentials, expected_type=ACCESS)
    return await _resolve_user(session, claims)


async def optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    session: SessionDep,
    tokens: TokenServiceDep,
) -> User | None:
    if credentials is None:
        return None
    try:
        claims = tokens.decode(credentials.credentials, expected_type=ACCESS)
    except AuthenticationError:
        return None
    try:
        return await _resolve_user(session, claims)
    except AuthenticationError:
        return None


CurrentUser = Annotated[User, Depends(current_user)]
OptionalCurrentUser = Annotated["User | None", Depends(optional_user)]


# ── Role / permission guards ────────────────────────────────────
async def require_admin(user: CurrentUser) -> User:
    if user.role_enum is not Role.ADMIN:
        raise AuthorizationError("Administrator role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_permission(permission: str) -> Callable[[User], User]:
    """Build a dependency that asserts *permission* on the current user."""

    async def _dep(user: CurrentUser) -> User:
        if not role_has(user.role_enum, permission):
            raise AuthorizationError(
                f"Missing required permission: {permission}",
                details={"permission": permission},
            )
        return user

    return _dep


# Re-export Settings to keep imports tidy at call sites.
__all__ = [
    "AdminUser",
    "AuthContextDep",
    "AuthServiceDep",
    "CurrentUser",
    "EmailServiceDep",
    "OptionalCurrentUser",
    "Settings",
    "TokenServiceDep",
    "current_user",
    "optional_user",
    "require_admin",
    "require_permission",
]
