"""Documentation domain types.

A documentation index is a collection of :class:`DocPage` items, each loaded
from a single Markdown file under the docs root. Pages carry frontmatter
metadata: id, title, category, tags, and an optional ``help_context`` array
that lets UI screens pull contextual help by key.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field


@dataclass(slots=True)
class DocPage:
    """A single rendered documentation page."""

    # Stable identifier (slug) — derived from the relative file path or the
    # ``id`` frontmatter field. Used in URLs and as the primary index key.
    id: str
    title: str
    body_markdown: str
    body_html: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    help_contexts: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    source_path: str = ""
    last_modified: _dt.datetime | None = None

    def to_summary(self) -> dict[str, object]:
        """Compact representation for listings."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "tags": list(self.tags),
            "summary": self.summary,
            "help_contexts": list(self.help_contexts),
        }

    def to_full(self) -> dict[str, object]:
        return {
            **self.to_summary(),
            "body_html": self.body_html,
            "body_markdown": self.body_markdown,
            "related": list(self.related),
            "source_path": self.source_path,
            "last_modified": (
                self.last_modified.isoformat() if self.last_modified else None
            ),
        }


@dataclass(slots=True)
class DocSearchHit:
    page_id: str
    title: str
    category: str
    score: float
    excerpt: str

    def to_dict(self) -> dict[str, object]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "category": self.category,
            "score": round(self.score, 4),
            "excerpt": self.excerpt,
        }
