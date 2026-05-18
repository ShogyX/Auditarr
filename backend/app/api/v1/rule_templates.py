"""Rule templates router (``/api/v1/rule-templates``) — v1.9 Stage 4.4.

Templates are reference-quality rule bodies shipped by the
codebase. Operators see them in a new Rules-page tab and click
"Use template" to create a normal operator-owned ``Rule`` row
seeded from the template's definition.

Endpoints:
  * ``GET /api/v1/rule-templates``                  list all
  * ``POST /api/v1/rule-templates/{id}/use``        create Rule

Both endpoints require an authenticated user. The list endpoint
is read-only and harmless; the use-endpoint creates a Rule and is
therefore admin-gated to mirror the existing ``POST /api/v1/rules``
authorization (rules.py).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.core.exceptions import NotFoundError
from app.models.rule import Rule
from app.schemas.rules import RuleRead
from app.services.repositories import RuleTemplateRepository

router = APIRouter(prefix="/rule-templates", tags=["rules"])


class RuleTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    priority: int
    definition: dict[str, Any]
    seeded_at: _dt.datetime
    created_at: _dt.datetime
    updated_at: _dt.datetime


@router.get(
    "",
    response_model=list[RuleTemplateRead],
    summary="List all rule templates",
)
async def list_templates(
    _user: CurrentUser,
    session: SessionDep,
) -> list[RuleTemplateRead]:
    """Return every shipped template, ordered by priority asc.

    Read-only — operators don't author templates; the codebase is
    the source of truth. The Templates tab on the Rules page calls
    this endpoint to render the list of "starting points" the
    operator can clone."""
    repo = RuleTemplateRepository(session)
    rows = await repo.list_all()
    return [RuleTemplateRead.model_validate(row) for row in rows]


@router.post(
    "/{template_id}/use",
    response_model=RuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Rule from a template",
)
async def use_template(
    template_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleRead:
    """Create a normal operator-owned ``Rule`` row from the
    template's body.

    Naming: the new Rule starts with the template's name verbatim.
    If a Rule with that name already exists, we append " (copy)" /
    " (copy 2)" / etc. until unique — so an operator who clicks
    "Use template" twice ends up with two distinct rows rather
    than a 409 conflict that loses the gesture.

    Other fields: ``description`` and ``definition`` copy
    verbatim; ``priority`` copies the template's suggested value;
    ``enabled`` defaults to True (operator just expressed intent
    by clicking Use); ``is_builtin`` is False (operator owns the
    copy and can edit it freely).
    """
    repo = RuleTemplateRepository(session)
    template = await repo.get_by_id(template_id)
    if template is None:
        raise NotFoundError(f"Rule template {template_id!r} not found")

    # Find a unique name. The base case (no collision) doesn't
    # touch the database beyond the one already-done get; the
    # collision case re-queries with appended suffixes.
    base_name = template.name
    candidate = base_name
    suffix = 1
    while True:
        existing = (
            await session.execute(
                select(Rule.id).where(Rule.name == candidate).limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            break
        suffix += 1
        candidate = (
            f"{base_name} (copy)"
            if suffix == 2
            else f"{base_name} (copy {suffix - 1})"
        )

    rule = Rule(
        name=candidate,
        description=template.description,
        enabled=True,
        priority=template.priority,
        definition=template.definition,
        is_builtin=False,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return RuleRead.model_validate(rule)


# ── v1.9 audit fix (OP-6) — operator-triggered re-seed ──────────


@router.post(
    "/reseed",
    summary="Re-seed built-in templates (admin)",
)
async def reseed_templates(
    _admin: AdminUser,
    session: SessionDep,
) -> dict[str, object]:
    """v1.9 audit fix (OP-6) — operators can manually trigger the
    built-in template seed.

    Templates are seeded on every app startup, but operators
    upgrading from a pre-v1.9 install whose migration didn't
    run, or who manually emptied the rule_templates table, can
    use this endpoint to force a re-seed without restarting the
    service.

    Returns the standard seed stats:
      * ``inserted``  — newly added rows
      * ``refreshed`` — existing rows whose body was updated
      * ``unchanged`` — existing rows already matching
      * ``total_after`` — total templates after the operation,
        for the UI's confirmation banner.
    """
    from app.rules.builtin import register_builtin_templates

    stats = await register_builtin_templates(session)
    repo = RuleTemplateRepository(session)
    rows = await repo.list_all()
    return {
        **stats,
        "total_after": len(rows),
    }
