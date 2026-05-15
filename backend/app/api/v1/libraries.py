"""Libraries router (``/api/v1/libraries``)."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import SessionDep
from app.core.exceptions import ConflictError, NotFoundError
from app.models.library import Library
from app.schemas.media import LibraryCreate, LibraryRead, LibraryUpdate
from app.services.repositories import LibraryRepository

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("", response_model=list[LibraryRead], summary="List configured libraries")
async def list_libraries(_user: CurrentUser, session: SessionDep) -> list[LibraryRead]:
    repo = LibraryRepository(session)
    return [LibraryRead.model_validate(lib) for lib in await repo.list_all()]


@router.post(
    "",
    response_model=LibraryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a library",
)
async def create_library(
    body: LibraryCreate,
    _admin: AdminUser,
    session: SessionDep,
) -> LibraryRead:
    repo = LibraryRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("A library with that name already exists")
    library = Library(
        name=body.name,
        root_path=body.root_path,
        kind=body.kind,
        enabled=body.enabled,
        scan_interval_minutes=body.scan_interval_minutes,
        integration_link=body.integration_link,
    )
    await repo.add(library)
    return LibraryRead.model_validate(library)


@router.get("/{library_id}", response_model=LibraryRead, summary="Get a library")
async def get_library(
    library_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> LibraryRead:
    library = await LibraryRepository(session).get(library_id)
    if library is None:
        raise NotFoundError("Library not found")
    return LibraryRead.model_validate(library)


@router.patch("/{library_id}", response_model=LibraryRead, summary="Update a library")
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> LibraryRead:
    repo = LibraryRepository(session)
    library = await repo.get(library_id)
    if library is None:
        raise NotFoundError("Library not found")
    data = body.model_dump(exclude_none=True)
    for field, value in data.items():
        setattr(library, field, value)
    return LibraryRead.model_validate(library)


@router.delete(
    "/{library_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a library and all of its media records",
)
async def delete_library(
    library_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> None:
    repo = LibraryRepository(session)
    library = await repo.get(library_id)
    if library is None:
        raise NotFoundError("Library not found")
    await repo.delete(library)
