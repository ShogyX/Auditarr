"""Markdown documentation loader.

Reads ``.md`` and ``.mdx`` files from the configured docs directory, parses
optional YAML frontmatter, renders Markdown to HTML, and yields immutable
:class:`DocPage` records.

Frontmatter schema (all fields optional)::

    ---
    id: rules.video-codec
    title: Video codec rule
    category: rules
    tags: [media, transcode]
    summary: One-sentence description shown in search results.
    help_context: [rules.conditions, rules.actions]
    related: [rules.audio-codec]
    ---

    # Body content here…
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt

from app.core.logging import get_logger
from app.documentation.types import DocPage

log = get_logger("auditarr.docs.loader", category="system")

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SUPPORTED_SUFFIXES = {".md", ".mdx"}


class MarkdownLoader:
    """Convert Markdown files on disk to :class:`DocPage` records."""

    def __init__(self) -> None:
        # ``commonmark`` is conservative — no raw HTML pass-through and no
        # plugin-specific syntax. The frontend renders the produced HTML
        # straight, so escaping here protects against accidental injection
        # from documentation contributions.
        self._md = (
            MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
            .enable(["table", "strikethrough"])
        )

    # ── Public API ────────────────────────────────────────────
    def load_directory(self, root: Path) -> list[DocPage]:
        """Scan *root* recursively and return all loadable pages.

        Loading errors on individual files are logged and skipped — one bad
        Markdown file should never prevent the whole index from building.
        """
        if not root.exists():
            log.info("docs.dir_missing", path=str(root))
            return []
        pages: list[DocPage] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                continue
            try:
                page = self.load_file(path, root)
            except DocLoadError as exc:
                log.warning("docs.load_failed", path=str(path), error=str(exc))
                continue
            pages.append(page)
        log.info("docs.loaded", count=len(pages), root=str(root))
        return pages

    def load_file(self, path: Path, root: Path) -> DocPage:
        """Load a single Markdown file into a :class:`DocPage`."""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DocLoadError(f"unreadable: {exc}") from exc

        meta, body_md = self._split_frontmatter(raw)
        body_html = self._md.render(body_md).strip()
        relpath = path.relative_to(root).with_suffix("")
        derived_id = str(relpath).replace("\\", "/").lstrip("/")

        page_id = _coerce_str(meta.get("id"), default=derived_id) or derived_id
        title = _coerce_str(meta.get("title"))
        if not title:
            title = self._extract_first_heading(body_md) or page_id

        return DocPage(
            id=page_id,
            title=title,
            body_markdown=body_md.strip(),
            body_html=body_html,
            category=_coerce_str(meta.get("category"), default="general") or "general",
            tags=_coerce_str_list(meta.get("tags")),
            summary=_coerce_str(meta.get("summary")) or self._derive_summary(body_md),
            help_contexts=_coerce_str_list(
                meta.get("help_context") or meta.get("help_contexts")
            ),
            related=_coerce_str_list(meta.get("related")),
            source_path=str(relpath) + path.suffix,
            last_modified=_mtime(path),
        )

    # ── Internals ─────────────────────────────────────────────
    def _split_frontmatter(self, raw: str) -> tuple[dict[str, Any], str]:
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            return {}, raw
        try:
            data = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise DocLoadError(f"invalid frontmatter: {exc}") from exc
        if not isinstance(data, dict):
            raise DocLoadError("frontmatter must be a mapping")
        return data, match.group(2)

    @staticmethod
    def _extract_first_heading(body: str) -> str | None:
        match = _HEADING_RE.search(body)
        return match.group(1).strip() if match else None

    @staticmethod
    def _derive_summary(body: str, limit: int = 200) -> str:
        """Strip headings/code/markup and take the first sentence-ish slice."""
        # Drop fenced code blocks (their content is rarely a useful summary).
        cleaned = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
        # Drop headings entirely.
        cleaned = _HEADING_RE.sub("", cleaned)
        # Strip remaining inline HTML / markdown emphasis.
        cleaned = _HTML_TAG_RE.sub("", cleaned)
        cleaned = re.sub(r"[*_`]+", "", cleaned)
        # Collapse whitespace.
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1].rstrip() + "…"


class DocLoadError(Exception):
    """Raised when a single doc file cannot be parsed."""


# ── helpers ──────────────────────────────────────────────────
def _coerce_str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _mtime(path: Path) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC)
    except OSError:
        return None
