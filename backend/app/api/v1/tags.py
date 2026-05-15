"""Top-level tag catalog (Stage 18 audit follow-up).

Exposes the union of distinct tag names across every file, used by:

* the visual rule builder's tag-condition autocomplete, and
* the automation scope-by-tag chip-input.

This is intentionally NOT under ``/media`` — it's a catalog, not a
per-file resource, and the path-greedy ``/media/{media_id}`` route
would shadow ``/media/tags`` otherwise.

The endpoint is non-admin-visible because tag catalog data is
needed by the same audiences that can author rules.
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.auth_deps import CurrentUser
from app.api.dependencies import SessionDep
from app.models.tag import MediaTag

router = APIRouter(prefix="/tags", tags=["tags"])


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
