"""Top-level tag catalog (Stage 18 audit follow-up).

Exposes the union of distinct tag names across every file, used by:

* the visual rule builder's tag-condition autocomplete, and
* the automation scope-by-tag chip-input.

Plus an admin-only management surface (v1.10): bulk-delete tags
imported from integrations once an operator decides they no longer
want them mirrored locally.

This is intentionally NOT under ``/media`` — it's a catalog, not a
per-file resource, and the path-greedy ``/media/{media_id}`` route
would shadow ``/media/tags`` otherwise.

The list endpoint is non-admin-visible because tag catalog data is
needed by the same audiences that can author rules. Delete is
admin-gated because dropping a synced tag affects every file
carrying it and every rule that matches against it.
"""
from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.models.tag import MediaTag

router = APIRouter(prefix="/tags", tags=["tags"])


class TagSummaryRow(BaseModel):
    """One row in the tag management table."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    source: str
    file_count: int


class TagDeleteRequest(BaseModel):
    """Bulk-delete filter. At least one of ``name`` / ``source`` is
    required so an operator can't accidentally wipe every tag with
    a misclick on an empty form."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    source: str | None = Field(default=None, min_length=1, max_length=32)


class TagDeleteResponse(BaseModel):
    deleted: int


@router.get(
    "",
    response_model=list[str],
    summary="Distinct tag names across every file",
)
async def list_tag_names(
    _user: CurrentUser, session: SessionDep
) -> list[str]:
    """Return the sorted, distinct set of every tag name in use.

    Includes manual tags, rule-applied tags, and tags synced from
    integrations — the consumer (rule editor / automation form)
    doesn't care about the source, only the names available for
    matching. Tag casing is preserved as stored; "4K" and "4k"
    surface as distinct entries (see Stage 13 guard rail).
    """
    rows = (
        await session.execute(
            select(MediaTag.name).distinct().order_by(MediaTag.name)
        )
    ).scalars().all()
    return list(rows)


@router.get(
    "/summary",
    response_model=list[TagSummaryRow],
    summary="Distinct (name, source) pairs with the count of files carrying each",
)
async def tag_summary(
    _user: CurrentUser, session: SessionDep
) -> list[TagSummaryRow]:
    """One row per ``(name, source)`` pair with the number of files it
    currently appears on. Drives the Settings → Tags management
    table.

    Empty result is normal on a fresh install; the table renders an
    empty state and waits for the first integration sync / rule run.
    """
    result = await session.execute(
        select(
            MediaTag.name,
            MediaTag.source,
            func.count(MediaTag.id).label("file_count"),
        )
        .group_by(MediaTag.name, MediaTag.source)
        .order_by(MediaTag.source, MediaTag.name)
    )
    return [
        TagSummaryRow(name=row.name, source=row.source, file_count=row.file_count)
        for row in result.all()
    ]


@router.post(
    "/delete",
    response_model=TagDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk-delete tags matching the given filter (admin)",
)
async def bulk_delete_tags(
    body: TagDeleteRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> TagDeleteResponse:
    """Remove every ``MediaTag`` matching the supplied filter.

    Both ``name`` and ``source`` are optional but at least one must
    be set — passing an empty filter would drop every tag in the
    catalog, which is almost never what the operator means and which
    they can do by hand if they really need to.

    Examples:

    * ``{"source": "sonarr"}`` — drop every tag synced from Sonarr.
    * ``{"name": "missing-subs:fr"}`` — drop a specific tag from
      every file regardless of source.
    * ``{"name": "4k", "source": "manual"}`` — drop a specific
      manually-applied tag from every file.

    The next integration sync will re-import any tags the upstream
    still owns. To suppress that, use the integration's
    ``tag_denylist`` setting alongside the delete.
    """
    if body.name is None and body.source is None:
        from app.core.exceptions import ValidationError

        raise ValidationError(
            "Provide at least one of ``name`` or ``source``. An empty "
            "filter would drop every tag in the catalog; if that's "
            "really what you want, do it manually.",
        )

    stmt = delete(MediaTag)
    if body.name is not None:
        stmt = stmt.where(MediaTag.name == body.name)
    if body.source is not None:
        stmt = stmt.where(MediaTag.source == body.source)
    result = await session.execute(stmt)
    await session.commit()
    return TagDeleteResponse(deleted=int(result.rowcount or 0))
