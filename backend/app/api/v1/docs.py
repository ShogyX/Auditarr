"""Documentation API (``/api/v1/docs``).

Read-only endpoints, public — Auditarr's documentation is shipped with the
application and not user-specific. Operators contribute new pages by
dropping ``.md`` files into the docs directory and POSTing to
``/docs/reload`` (admin only) or restarting the service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.auth_deps import AdminUser
from app.documentation import get_documentation_service

router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("", summary="List documentation pages")
async def list_pages(
    category: str | None = Query(None, description="Filter by category"),
    tag: str | None = Query(None, description="Filter by tag"),
) -> list[dict[str, Any]]:
    svc = get_documentation_service()
    pages = svc.list_pages()
    if category:
        pages = [p for p in pages if p.category == category]
    if tag:
        pages = [p for p in pages if tag in p.tags]
    return [p.to_summary() for p in pages]


@router.get("/categories", summary="List documentation categories")
async def list_categories() -> dict[str, list[dict[str, Any]]]:
    svc = get_documentation_service()
    return {
        cat: [page.to_summary() for page in pages]
        for cat, pages in sorted(svc.categories().items())
    }


@router.get("/search", summary="Full-text search across documentation")
async def search(
    q: str = Query("", min_length=0, max_length=200),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict[str, Any]]:
    svc = get_documentation_service()
    if not q.strip():
        return []
    return [hit.to_dict() for hit in svc.search(q, limit=limit)]


@router.get("/help/{help_context}", summary="Pages registered for a help-context key")
async def help_context(help_context: str) -> list[dict[str, Any]]:
    svc = get_documentation_service()
    return [p.to_summary() for p in svc.by_help_context(help_context)]


@router.get("/{page_id:path}", summary="Fetch a single documentation page")
async def get_page(page_id: str) -> dict[str, Any]:
    svc = get_documentation_service()
    page = svc.get(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Documentation page not found")
    return page.to_full()


@router.post(
    "/reload",
    summary="Re-scan the documentation directory (admin only)",
)
async def reload(_admin: AdminUser) -> dict[str, int]:
    svc = get_documentation_service()
    count = svc.reload()
    return {"pages_indexed": count}
