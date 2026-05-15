"""Media extension rules CRUD (Stage 9 audit follow-up).

Per-extension scanner + rule-engine overrides. See
:mod:`app.models.extension_rule` for the four dispositions.

Endpoints:

* ``GET    /api/v1/system/extension-rules``         — list (non-admin)
* ``POST   /api/v1/system/extension-rules``         — create (admin)
* ``PATCH  /api/v1/system/extension-rules/{id}``    — update (admin)
* ``DELETE /api/v1/system/extension-rules/{id}``    — remove (admin)
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.extension_rule import MediaExtensionRule
from app.services.repositories import MediaExtensionRuleRepository

router = APIRouter(prefix="/system", tags=["system"])


# ── Valid dispositions ─────────────────────────────────────────
Disposition = Literal["ignore", "stats_only", "malicious", "accepted"]
VALID_DISPOSITIONS = {"ignore", "stats_only", "malicious", "accepted"}


def _normalize_extension(ext: str) -> str:
    """Lower-case, strip leading dot. Operators commonly type
    ".mp4" — accept that and normalize to "mp4" so the stored shape
    is canonical."""
    if not isinstance(ext, str):
        raise ValueError("extension must be a string")
    return ext.strip().lstrip(".").lower()


# ── Schemas ────────────────────────────────────────────────────
class ExtensionRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    extension: str
    disposition: str
    enabled: bool


class ExtensionRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension: str = Field(min_length=1, max_length=32)
    disposition: Disposition
    enabled: bool = True

    @field_validator("extension", mode="before")
    @classmethod
    def _ext(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("extension must be a string")
        return _normalize_extension(v)


class ExtensionRuleUpdate(BaseModel):
    """Patch shape. Every field optional."""

    model_config = ConfigDict(extra="forbid")

    extension: str | None = Field(default=None, min_length=1, max_length=32)
    disposition: Disposition | None = None
    enabled: bool | None = None

    @field_validator("extension", mode="before")
    @classmethod
    def _ext(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("extension must be a string")
        return _normalize_extension(v)


# ── Endpoints ──────────────────────────────────────────────────
@router.get(
    "/extension-rules",
    response_model=list[ExtensionRuleRead],
    summary="List media extension rules (Stage 9)",
)
async def list_extension_rules(
    _user: CurrentUser, session: SessionDep
) -> list[ExtensionRuleRead]:
    rows = await MediaExtensionRuleRepository(session).list_all()
    return [ExtensionRuleRead.model_validate(r) for r in rows]


@router.post(
    "/extension-rules",
    response_model=ExtensionRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a media extension rule (Stage 9)",
)
async def create_extension_rule(
    body: ExtensionRuleCreate,
    _admin: AdminUser,
    session: SessionDep,
) -> ExtensionRuleRead:
    repo = MediaExtensionRuleRepository(session)
    if not body.extension:
        raise ValidationError("extension is required")
    # Conflict check: extension is unique.
    existing = await repo.get_by_extension(body.extension)
    if existing is not None:
        raise ConflictError(
            f"A rule for extension {body.extension!r} already exists",
            details={"existing_rule_id": existing.id},
        )
    rule = MediaExtensionRule(
        extension=body.extension,
        disposition=body.disposition,
        enabled=body.enabled,
    )
    await repo.add(rule)
    await session.commit()
    return ExtensionRuleRead.model_validate(rule)


@router.patch(
    "/extension-rules/{rule_id}",
    response_model=ExtensionRuleRead,
    summary="Update a media extension rule (Stage 9)",
)
async def update_extension_rule(
    rule_id: str,
    body: ExtensionRuleUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> ExtensionRuleRead:
    repo = MediaExtensionRuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise NotFoundError(f"Extension rule {rule_id!r} not found")
    patch = body.model_dump(exclude_none=True)
    # If extension is being changed, enforce uniqueness against other rows.
    if "extension" in patch and patch["extension"] != rule.extension:
        conflict = await repo.get_by_extension(patch["extension"])
        if conflict is not None and conflict.id != rule.id:
            raise ConflictError(
                f"Another rule for extension {patch['extension']!r} already exists",
                details={"existing_rule_id": conflict.id},
            )
    for field, value in patch.items():
        setattr(rule, field, value)
    await session.flush([rule])
    await session.commit()
    return ExtensionRuleRead.model_validate(rule)


@router.delete(
    "/extension-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a media extension rule (Stage 9)",
)
async def delete_extension_rule(
    rule_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> None:
    repo = MediaExtensionRuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise NotFoundError(f"Extension rule {rule_id!r} not found")
    await repo.delete(rule)
    await session.commit()
