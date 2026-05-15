"""Runtime settings + secrets API (Stage 21).

The Settings page in the UI talks to these endpoints:

* ``GET    /api/v1/system/runtime-settings/describe`` — UI metadata
  (label, description, type, constraints, category, impact). Used
  to render the editor without hard-coding the field list.
* ``GET    /api/v1/system/runtime-settings`` — current effective
  values, with an ``is_override`` flag per field.
* ``PUT    /api/v1/system/runtime-settings/{key}`` — set an override.
* ``DELETE /api/v1/system/runtime-settings/{key}`` — clear override
  (revert to env default).
* ``GET    /api/v1/system/secrets`` — metadata for every managed
  secret slot. Never returns plaintext.
* ``PUT    /api/v1/system/secrets/{key}`` — set a secret.
* ``DELETE /api/v1/system/secrets/{key}`` — clear a secret.
* ``POST   /api/v1/system/secrets/{key}/test`` — verify the secret
  by calling the upstream API. Result is stored as audit metadata.

All write endpoints are admin-only. The describe endpoint is
admin-only too, because the UI it powers is itself admin-only —
non-admin users have no business knowing the editable surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth_deps import AdminUser
from app.api.dependencies import SessionDep, SettingsDep
from app.core.exceptions import IntegrationError, ValidationError
from app.core.runtime_settings_schema import (
    RuntimeSettingValidationError,
    describe_runtime_settings,
    describe_secrets,
)
from app.services.runtime_settings import RuntimeSettingsService, SecretService

router = APIRouter(prefix="/system", tags=["system"])


# ── Schemas ──────────────────────────────────────────────────
class RuntimeSettingWrite(BaseModel):
    """Body for ``PUT /system/runtime-settings/{key}``."""

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(
        description="Coerced + range-checked by the runtime schema."
    )


class SecretWrite(BaseModel):
    """Body for ``PUT /system/secrets/{key}``."""

    model_config = ConfigDict(extra="forbid")

    plaintext: str = Field(
        min_length=1,
        max_length=4096,
        description="Plaintext secret. Encrypted before storage.",
    )


# ── Describe endpoints (no DB access; static metadata) ───────
@router.get(
    "/runtime-settings/describe",
    summary="Metadata for every runtime-editable setting",
)
async def runtime_settings_describe(
    _admin: AdminUser,
) -> dict[str, list[dict[str, Any]]]:
    return {"fields": describe_runtime_settings()}


@router.get(
    "/secrets/describe",
    summary="Metadata for every managed secret slot",
)
async def secrets_describe(
    _admin: AdminUser,
) -> dict[str, list[dict[str, Any]]]:
    return {"secrets": describe_secrets()}


# ── Runtime settings CRUD ────────────────────────────────────
@router.get(
    "/runtime-settings",
    summary="Current effective values for every runtime-editable setting",
)
async def runtime_settings_list(
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, dict[str, Any]]:
    service = RuntimeSettingsService(session=session, settings=settings)
    return await service.list_effective()


@router.put(
    "/runtime-settings/{key}",
    summary="Set a runtime override",
)
async def runtime_settings_set(
    key: str,
    body: RuntimeSettingWrite,
    admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    service = RuntimeSettingsService(session=session, settings=settings)
    try:
        # Stage 2: pass admin.id through so the audit row records
        # the operator who made the change.
        coerced = await service.set_override(
            key, body.value, set_by_user_id=admin.id
        )
    except RuntimeSettingValidationError as exc:
        raise ValidationError(str(exc)) from exc
    return {"key": key, "value": coerced, "is_override": True}


@router.delete(
    "/runtime-settings/{key}",
    summary="Clear a runtime override (revert to env default)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def runtime_settings_clear(
    key: str,
    admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> None:
    service = RuntimeSettingsService(session=session, settings=settings)
    try:
        # Stage 2: pass admin.id through so the audit row records
        # the operator who cleared the override.
        await service.clear_override(key, set_by_user_id=admin.id)
    except RuntimeSettingValidationError as exc:
        raise ValidationError(str(exc)) from exc


@router.get(
    "/runtime-settings/{key}/history",
    summary="List recent override changes for a runtime setting (Stage 2)",
)
async def runtime_settings_history(
    key: str,
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Return the recent change log for ``key``, newest first.

    Admin-only because the audit log can reveal operational
    sensitivity (e.g. who disabled ``ws_require_auth`` and when).
    ``limit`` is clamped to [1, 500] inside the service so a
    malicious ``?limit=`` query can't dump the full table.
    """
    service = RuntimeSettingsService(session=session, settings=settings)
    try:
        changes = await service.list_history(key, limit=limit)
    except RuntimeSettingValidationError as exc:
        raise ValidationError(str(exc)) from exc
    return {"changes": changes}


# ── Secrets CRUD + test ──────────────────────────────────────
@router.get(
    "/secrets",
    summary="Metadata (never plaintext) for every managed secret",
)
async def secrets_list(
    _admin: AdminUser, session: SessionDep
) -> dict[str, list[dict[str, Any]]]:
    return {"secrets": await SecretService(session=session).list_status()}


@router.put(
    "/secrets/{key}",
    summary="Store an encrypted secret",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def secrets_set(
    key: str,
    body: SecretWrite,
    admin: AdminUser,
    session: SessionDep,
) -> None:
    service = SecretService(session=session)
    try:
        await service.set_secret(
            key=key, plaintext=body.plaintext, set_by_user_id=admin.id
        )
    except RuntimeSettingValidationError as exc:
        raise ValidationError(str(exc)) from exc


@router.delete(
    "/secrets/{key}",
    summary="Clear a stored secret",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def secrets_clear(
    key: str, _admin: AdminUser, session: SessionDep
) -> None:
    service = SecretService(session=session)
    try:
        await service.clear_secret(key)
    except RuntimeSettingValidationError as exc:
        raise ValidationError(str(exc)) from exc


@router.post(
    "/secrets/{key}/test",
    summary="Probe the upstream API with the stored secret",
)
async def secrets_test(
    key: str, _admin: AdminUser, session: SessionDep
) -> dict[str, Any]:
    from app.services.secret_testers import run_secret_test

    service = SecretService(session=session)
    plaintext = await service.get_plaintext(key)
    if plaintext is None:
        raise ValidationError(
            f"No secret stored for {key!r}. Set the value first."
        )
    ok, detail = await run_secret_test(key, plaintext)
    await service.record_test_outcome(key=key, ok=ok, detail=detail)
    if not ok:
        # 502 because the upstream test failed — distinct from a
        # validation error so the UI can show "external API rejected
        # the secret" copy.
        raise IntegrationError(detail or "Upstream test failed")
    return {"ok": True, "detail": detail}
