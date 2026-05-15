"""Documentation service.

Loads Markdown pages from the configured docs directory once at startup
(or on demand via ``reload``), exposes them by id and by ``help_context``
key, and provides a search interface backed by the in-memory index.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from threading import RLock

from app.core.logging import get_logger
from app.documentation.loader import MarkdownLoader
from app.documentation.search import SearchIndex
from app.documentation.types import DocPage, DocSearchHit

log = get_logger("auditarr.docs", category="system")


class DocumentationService:
    """Read-only documentation index with hot reload."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._loader = MarkdownLoader()
        self._search = SearchIndex()
        self._pages: dict[str, DocPage] = {}
        self._by_help_context: dict[str, list[DocPage]] = defaultdict(list)
        self._categories: dict[str, list[DocPage]] = defaultdict(list)
        self._lock = RLock()

    @property
    def root(self) -> Path:
        return self._root

    # ── Lifecycle ─────────────────────────────────────────────
    def load(self) -> int:
        """Initial load. Returns the number of pages indexed."""
        return self.reload()

    def reload(self) -> int:
        """Re-scan the docs directory and rebuild the index."""
        pages = self._loader.load_directory(self._root)
        with self._lock:
            self._pages = {p.id: p for p in pages}
            self._by_help_context = defaultdict(list)
            self._categories = defaultdict(list)
            for page in pages:
                for ctx_key in page.help_contexts:
                    self._by_help_context[ctx_key].append(page)
                self._categories[page.category].append(page)
            self._search.rebuild(pages)
        log.info(
            "docs.indexed",
            count=len(pages),
            help_contexts=len(self._by_help_context),
            categories=len(self._categories),
        )
        return len(pages)

    # ── Reads ─────────────────────────────────────────────────
    def list_pages(self) -> list[DocPage]:
        with self._lock:
            return sorted(self._pages.values(), key=lambda p: p.title.lower())

    def get(self, page_id: str) -> DocPage | None:
        with self._lock:
            return self._pages.get(page_id)

    def by_help_context(self, key: str) -> list[DocPage]:
        with self._lock:
            return list(self._by_help_context.get(key, ()))

    def categories(self) -> dict[str, list[DocPage]]:
        with self._lock:
            return {cat: list(pages) for cat, pages in self._categories.items()}

    def search(self, query: str, *, limit: int = 10) -> list[DocSearchHit]:
        with self._lock:
            return self._search.search(
                query, pages_by_id=self._pages, limit=limit
            )

    @property
    def page_count(self) -> int:
        with self._lock:
            return len(self._pages)


_service: DocumentationService | None = None


def get_documentation_service() -> DocumentationService:
    """Return the process-wide documentation service singleton."""
    global _service
    if _service is None:
        from app.core.settings import get_settings

        _service = DocumentationService(get_settings().docs_dir)
    return _service


def reset_documentation_service() -> None:
    """Test helper — drop the cached singleton."""
    global _service
    _service = None
