"""Documentation engine.

Stage 3 deliverable: file-based Markdown documentation with frontmatter,
in-memory search, and per-screen contextual help lookup.
"""

from app.documentation.loader import DocLoadError, MarkdownLoader
from app.documentation.search import SearchIndex
from app.documentation.service import (
    DocumentationService,
    get_documentation_service,
    reset_documentation_service,
)
from app.documentation.types import DocPage, DocSearchHit

__all__ = [
    "DocLoadError",
    "DocPage",
    "DocSearchHit",
    "DocumentationService",
    "MarkdownLoader",
    "SearchIndex",
    "get_documentation_service",
    "reset_documentation_service",
]
