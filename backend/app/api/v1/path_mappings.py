"""Cross-integration path-mappings surface (Stage 21).

Integrations each carry their own ``config.path_mappings`` list,
which is what the rest of the codebase reads. The Settings UI wants
a flat "show me every path mapping across every integration" view
so the operator can audit + edit them in one place rather than
clicking through each integration page.

This module is a thin read/write façade over
:class:`Integration.config['path_mappings']`. We deliberately don't
introduce a separate ``path_mappings`` table — the source of truth
stays where the rest of the code expects it. Bulk-editing here just
rewrites the corresponding integration row's JSON config.

Stage 5 (audit follow-up): adds a second surface for **global** path
mappings — applied across every integration, stored in the new
``global_path_mappings`` table. The two layers are independent;
``/system/path-suggestions`` exposes the union of known
roots (library root_paths + integration config roots) so the UI can
offer autocomplete instead of free-text inputs (audit Issue 19).

Endpoints:

* ``GET    /api/v1/system/path-mappings``
  Per-integration mappings (existing).
* ``PUT    /api/v1/system/path-mappings/{integration_id}``
  Replace mappings on one integration (existing).
* ``GET    /api/v1/system/path-mappings/global``
  List the global mappings (Stage 5).
* ``POST   /api/v1/system/path-mappings/global``
  Create a global mapping (Stage 5).
* ``PATCH  /api/v1/system/path-mappings/global/{id}``
  Update a single global mapping (Stage 5).
* ``DELETE /api/v1/system/path-mappings/global/{id}``
  Remove a global mapping (Stage 5).
* ``GET    /api/v1/system/path-suggestions``
  Union of library root_paths and integration config roots — the
  UI uses this to populate autocomplete dropdowns for from/to
  fields so operators don't have to retype paths (Stage 5).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.core.exceptions import NotFoundError, ValidationError
from app.integrations.path_mapping import parse_mappings
from app.models.integration import Integration
from app.models.library import Library
from app.models.path_mapping import GlobalPathMapping
from app.services.repositories import GlobalPathMappingRepository

router = APIRouter(prefix="/system", tags=["system"])


def _strip(v: object) -> object:
    if isinstance(v, str):
        return v.strip().rstrip("/")
    return v


# ── Schemas ──────────────────────────────────────────────────
class PathMappingEntry(BaseModel):
    """One ``from → to`` prefix rewrite as the UI sees it."""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(
        alias="from",
        min_length=1,
        max_length=1024,
        description="Path as seen by the integration.",
    )
    to: str = Field(
        min_length=1,
        max_length=1024,
        description="Path as seen by Auditarr (matches MediaFile.path).",
    )


class PathMappingsUpdate(BaseModel):
    """Body for ``PUT /system/path-mappings/{integration_id}``.

    Replaces the entire list — there's no partial-update semantic.
    Empty list clears all mappings for that integration.
    """

    model_config = ConfigDict(extra="forbid")

    mappings: list[PathMappingEntry] = Field(
        default_factory=list, max_length=200
    )


# ── Stage 5: Global path mapping schemas ────────────────────
class GlobalPathMappingRead(BaseModel):
    """One global mapping as returned to the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    from_path: str
    to_path: str
    enabled: bool
    priority: int


class GlobalPathMappingCreate(BaseModel):
    """Create a global mapping."""

    model_config = ConfigDict(extra="forbid")

    from_path: str = Field(min_length=1, max_length=1024)
    to_path: str = Field(min_length=1, max_length=1024)
    enabled: bool = True
    priority: int = Field(default=0, ge=-1000, le=1000)

    _strip = field_validator("from_path", "to_path", mode="before")(_strip)


class GlobalPathMappingUpdate(BaseModel):
    """Patch a global mapping. Every field optional."""

    model_config = ConfigDict(extra="forbid")

    from_path: str | None = Field(default=None, min_length=1, max_length=1024)
    to_path: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)

    _strip = field_validator("from_path", "to_path", mode="before")(_strip)


# ── Read aggregator ──────────────────────────────────────────
@router.get(
    "/path-mappings",
    summary="Path mappings aggregated across every integration",
)
async def list_path_mappings(
    _user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """One JSON object per integration that *can* have path mappings
    (any integration kind that declares a ``path_mappings`` field on
    its config schema). Integrations with no mappings configured still
    appear with ``mappings: []`` so the UI can show them as
    "configurable but empty".

    Non-admin users get a read-only view — admins are the only ones
    who can PUT, gated separately below. The read view is non-admin-
    visible because operators consulting "where does Plex think
    /data/tv lives?" is a routine debugging task that shouldn't
    require admin.
    """
    rows = (
        await session.execute(
            select(Integration).order_by(Integration.name)
        )
    ).scalars().all()

    out: list[dict[str, Any]] = []
    for ig in rows:
        raw = ig.config.get("path_mappings") if isinstance(ig.config, dict) else None
        # Run through the same parser the rest of the codebase uses
        # so the UI sees exactly what the scanner / poller would see.
        mappings = parse_mappings(raw)
        out.append(
            {
                "integration_id": ig.id,
                "name": ig.name,
                "kind": ig.kind,
                "is_active": ig.enabled,
                "mappings": [
                    {"from": m.src_prefix, "to": m.dst_prefix}
                    for m in mappings
                ],
                # Surface the raw value too, in case parse_mappings
                # dropped malformed entries — the operator should see
                # those in the UI so they can fix them, rather than
                # have them silently disappear.
                "raw": raw if isinstance(raw, list) else [],
                # Stage 17 (audit follow-up): the snapshot of
                # libraries discovered from the upstream at
                # integration-create time (or via the rediscover
                # endpoint). The UI uses this to highlight unmapped
                # / stale paths. ``None`` means "never discovered"
                # — the panel surfaces that as a "Discover now"
                # button rather than as an empty discovered set.
                "discovered_paths": ig.discovered_paths,
            }
        )
    return {"integrations": out}


# ── Write per-integration ────────────────────────────────────
@router.put(
    "/path-mappings/{integration_id}",
    summary="Replace path mappings for one integration",
    status_code=status.HTTP_200_OK,
)
async def update_path_mappings(
    integration_id: str,
    body: PathMappingsUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> dict[str, Any]:
    ig = await session.get(Integration, integration_id)
    if ig is None:
        raise NotFoundError(f"Integration {integration_id!r} not found")

    # Build the JSON-shaped list the integration's config schema
    # expects. We round-trip through ``parse_mappings`` to apply the
    # same trimming + de-empty rules used at read time, so the value
    # we store matches what the scanner will later read back.
    raw_mappings = [{"from": e.from_, "to": e.to} for e in body.mappings]
    parsed = parse_mappings(raw_mappings)
    if len(parsed) != len(raw_mappings):
        # parse_mappings silently drops malformed entries; surface
        # that as a 422 so the UI can show the rejection reason.
        raise ValidationError(
            "One or more mappings had empty 'from' or 'to' after "
            "trimming trailing slashes. Re-check the entries and resubmit."
        )

    # Mutate config in-place. ``config`` is a JSON column; SQLAlchemy
    # needs to be told the dict changed (it doesn't deep-watch JSON
    # mutations by default).
    new_config = dict(ig.config or {})
    new_config["path_mappings"] = [
        {"from": m.src_prefix, "to": m.dst_prefix} for m in parsed
    ]
    ig.config = new_config

    await session.flush([ig])
    await session.commit()

    return {
        "integration_id": ig.id,
        "name": ig.name,
        "kind": ig.kind,
        "mappings": new_config["path_mappings"],
    }


# ── Stage 5: Global path mappings ────────────────────────────
@router.get(
    "/path-mappings/global",
    summary="List global path mappings (Stage 5)",
    response_model=list[GlobalPathMappingRead],
)
async def list_global_path_mappings(
    _user: CurrentUser, session: SessionDep
) -> list[GlobalPathMappingRead]:
    """Returns every global mapping ordered by priority asc, then
    created_at. Non-admin-visible because reading the mapping list
    is a routine debug step ("which mappings affect this path?").
    Write operations are admin-only.
    """
    rows = await GlobalPathMappingRepository(session).list_all()
    return [GlobalPathMappingRead.model_validate(r) for r in rows]


@router.post(
    "/path-mappings/global",
    summary="Create a global path mapping (Stage 5)",
    status_code=status.HTTP_201_CREATED,
    response_model=GlobalPathMappingRead,
)
async def create_global_path_mapping(
    body: GlobalPathMappingCreate,
    _admin: AdminUser,
    session: SessionDep,
) -> GlobalPathMappingRead:
    repo = GlobalPathMappingRepository(session)
    if not body.from_path or not body.to_path:
        # Should be caught by Field(min_length=1) but be defensive
        # — paths trimmed to empty by the validator land here.
        raise ValidationError(
            "Both from_path and to_path are required after trimming."
        )
    mapping = GlobalPathMapping(
        from_path=body.from_path,
        to_path=body.to_path,
        enabled=body.enabled,
        priority=body.priority,
    )
    await repo.add(mapping)
    await session.commit()
    return GlobalPathMappingRead.model_validate(mapping)


@router.patch(
    "/path-mappings/global/{mapping_id}",
    summary="Update a global path mapping (Stage 5)",
    response_model=GlobalPathMappingRead,
)
async def update_global_path_mapping(
    mapping_id: str,
    body: GlobalPathMappingUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> GlobalPathMappingRead:
    repo = GlobalPathMappingRepository(session)
    mapping = await repo.get(mapping_id)
    if mapping is None:
        raise NotFoundError(f"Global path mapping {mapping_id!r} not found")
    patch = body.model_dump(exclude_none=True)
    for field, value in patch.items():
        setattr(mapping, field, value)
    await session.flush([mapping])
    await session.commit()
    return GlobalPathMappingRead.model_validate(mapping)


@router.delete(
    "/path-mappings/global/{mapping_id}",
    summary="Delete a global path mapping (Stage 5)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_global_path_mapping(
    mapping_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> None:
    repo = GlobalPathMappingRepository(session)
    mapping = await repo.get(mapping_id)
    if mapping is None:
        raise NotFoundError(f"Global path mapping {mapping_id!r} not found")
    await repo.delete(mapping)
    await session.commit()


# ── Stage 5: Path suggestions for autocomplete ───────────────
@router.get(
    "/path-suggestions",
    summary="Union of known filesystem roots (Stage 5)",
)
async def path_suggestions(
    _user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Returns the union of:

    - Every ``Library.root_path``.
    - Every distinct ``from`` and ``to`` already present in any
      integration's ``config.path_mappings``.
    - Every distinct ``from_path`` / ``to_path`` on a
      :class:`GlobalPathMapping` row.

    The UI uses this to populate autocomplete dropdowns on the
    Path Mappings panel so the operator picks from known roots
    instead of free-typing (audit Issue 19 — "should auto-pull
    path mappings from integrations").

    Output shape::

      {
        "library_roots": [...],
        "integration_paths": [{"from": "...", "to": "..."}],
        "global_paths": [...]
      }
    """
    # Library roots.
    lib_rows = await session.execute(select(Library.root_path))
    library_roots = sorted({r for r in lib_rows.scalars().all() if r})

    # Integration path-mapping entries — read straight off the JSON
    # column. We don't run them through parse_mappings here because
    # the operator may have malformed rows that they'd still like to
    # see surfaced for editing.
    ig_rows = (
        await session.execute(select(Integration))
    ).scalars().all()
    integration_paths: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for ig in ig_rows:
        raw = (
            ig.config.get("path_mappings") if isinstance(ig.config, dict) else None
        )
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            f = entry.get("from") or entry.get("src_prefix")
            t = entry.get("to") or entry.get("dst_prefix")
            if not isinstance(f, str) or not isinstance(t, str):
                continue
            key = (f, t)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            integration_paths.append({"from": f, "to": t})

    # Global path mappings — pre-computed for the union view.
    g_rows = await GlobalPathMappingRepository(session).list_all()
    global_paths = sorted(
        {r.from_path for r in g_rows} | {r.to_path for r in g_rows}
    )

    return {
        "library_roots": library_roots,
        "integration_paths": integration_paths,
        "global_paths": global_paths,
    }
