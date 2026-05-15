"""Auth router (``/api/v1/auth``)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.auth_deps import (
    AuthContextDep,
    AuthServiceDep,
    CurrentUser,
    SettingsDep,
)
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.security import Role
from app.security.rate_limit import get_rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_token_response(pair, settings) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with username/email + password",
)
async def login(
    body: LoginRequest,
    request: Request,
    auth: AuthServiceDep,
    settings: SettingsDep,
    ctx: AuthContextDep,
) -> TokenResponse:
    await get_rate_limiter().check(request, "login")
    _user, pair = await auth.login(login=body.login, password=body.password, ctx=ctx)
    return _to_token_response(pair, settings)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new token pair",
)
async def refresh(
    body: RefreshRequest,
    auth: AuthServiceDep,
    settings: SettingsDep,
    ctx: AuthContextDep,
) -> TokenResponse:
    _user, pair = await auth.refresh(body.refresh_token, ctx=ctx)
    return _to_token_response(pair, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the supplied refresh token",
)
async def logout(
    body: RefreshRequest | None,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> Response:
    refresh_token = body.refresh_token if body else None
    await auth.logout(refresh_token, ctx=ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every refresh session for the current user",
)
async def logout_all(
    user: CurrentUser,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> Response:
    await auth.logout_all(user, ctx=ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user account",
)
async def register(
    body: RegisterRequest,
    request: Request,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> UserPublic:
    await get_rate_limiter().check(request, "register")
    user = await auth.register(
        email=body.email,
        username=body.username,
        password=body.password,
        full_name=body.full_name,
        role=Role.USER,
        ctx=ctx,
    )
    return UserPublic.model_validate(user)


@router.get("/me", response_model=UserPublic, summary="Return the authenticated user")
async def me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.patch(
    "/me",
    response_model=UserPublic,
    summary="Update the authenticated user's profile (email, full name)",
)
async def update_me(
    body: ProfileUpdateRequest,
    user: CurrentUser,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> UserPublic:
    """Patch the current user's profile fields.

    Username changes are intentionally not allowed here — usernames
    appear in audit logs and renaming them in-place would break
    historical attribution. Operators who genuinely need to rename a
    user do so via admin tooling.

    Password changes go through ``POST /password/change`` so the
    current-password confirmation flow stays explicit; this endpoint
    deliberately doesn't accept a password field.
    """
    updated = await auth.update_profile(
        user,
        email=body.email,
        full_name=body.full_name,
        ctx=ctx,
    )
    return UserPublic.model_validate(updated)


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the authenticated user's password",
)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> Response:
    await auth.change_password(
        user,
        current_password=body.current_password,
        new_password=body.new_password,
        ctx=ctx,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a password-reset email if the address is recognised",
)
async def request_reset(
    body: PasswordResetRequest,
    request: Request,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> dict[str, str]:
    await get_rate_limiter().check(request, "password_reset")
    await auth.request_password_reset(email=body.email, ctx=ctx)
    return {"status": "accepted"}


@router.post(
    "/password/reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password using a reset token",
)
async def confirm_reset(
    body: PasswordResetConfirm,
    auth: AuthServiceDep,
    ctx: AuthContextDep,
) -> Response:
    await auth.confirm_password_reset(
        token=body.token, new_password=body.new_password, ctx=ctx
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
