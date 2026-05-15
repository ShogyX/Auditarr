"""Markdown loader unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.documentation.loader import DocLoadError, MarkdownLoader


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_minimal_file(tmp_path: Path) -> None:
    _write(tmp_path / "intro.md", "# Hello\n\nFirst paragraph.")
    loader = MarkdownLoader()
    pages = loader.load_directory(tmp_path)
    assert len(pages) == 1
    page = pages[0]
    assert page.id == "intro"
    assert page.title == "Hello"
    assert "<h1>Hello</h1>" in page.body_html
    assert page.summary.startswith("First paragraph")
    assert page.category == "general"


def test_load_with_frontmatter(tmp_path: Path) -> None:
    _write(
        tmp_path / "rules" / "conditions.md",
        """---
id: rules/conditions
title: Conditions
category: rules
tags: [rules, syntax]
summary: How to write rule conditions.
help_context: [rules.conditions]
related: [rules/actions]
---

# Body title

Some explanation goes here.
""",
    )
    loader = MarkdownLoader()
    pages = loader.load_directory(tmp_path)
    assert len(pages) == 1
    page = pages[0]
    assert page.id == "rules/conditions"
    assert page.title == "Conditions"
    assert page.category == "rules"
    assert page.tags == ["rules", "syntax"]
    assert page.summary == "How to write rule conditions."
    assert page.help_contexts == ["rules.conditions"]
    assert page.related == ["rules/actions"]


def test_invalid_frontmatter_logs_and_skips(tmp_path: Path) -> None:
    _write(tmp_path / "bad.md", "---\n: invalid yaml :\n---\n# x")
    loader = MarkdownLoader()
    # load_directory should not raise — it logs and skips.
    pages = loader.load_directory(tmp_path)
    assert pages == []


def test_unicode_safe(tmp_path: Path) -> None:
    _write(tmp_path / "u.md", "# Héllo\n\nBody — with em dash.")
    loader = MarkdownLoader()
    pages = loader.load_directory(tmp_path)
    assert pages[0].title == "Héllo"
    assert "—" in pages[0].body_html or "&mdash;" in pages[0].body_html


def test_html_input_is_escaped(tmp_path: Path) -> None:
    """Raw HTML in the markdown source must not be passed through."""
    _write(tmp_path / "x.md", "# Title\n\n<script>alert(1)</script>\n")
    loader = MarkdownLoader()
    page = loader.load_directory(tmp_path)[0]
    assert "<script>" not in page.body_html
    assert "&lt;script&gt;" in page.body_html


def test_summary_trims_long_content(tmp_path: Path) -> None:
    body = "Short intro. " + ("filler " * 60)
    _write(tmp_path / "x.md", f"# Title\n\n{body}")
    loader = MarkdownLoader()
    page = loader.load_directory(tmp_path)[0]
    assert page.summary.endswith("…")
    assert len(page.summary) <= 200


def test_load_file_directly_raises_on_unreadable(tmp_path: Path) -> None:
    loader = MarkdownLoader()
    with pytest.raises(DocLoadError):
        loader.load_file(tmp_path / "missing.md", tmp_path)


def test_recursive_discovery(tmp_path: Path) -> None:
    _write(tmp_path / "guide" / "a.md", "# A")
    _write(tmp_path / "guide" / "nested" / "b.md", "# B")
    _write(tmp_path / "rules" / "c.md", "# C")
    pages = MarkdownLoader().load_directory(tmp_path)
    ids = sorted(p.id for p in pages)
    assert ids == ["guide/a", "guide/nested/b", "rules/c"]


def test_stage11_new_docs_are_discoverable() -> None:
    """Stage 11 (audit follow-up): the four new doc pages plus the
    rewritten ``rules/actions`` page must all be discoverable from
    the real ``docs/`` tree with valid frontmatter (id, category,
    tags, help_context). Pins the loader against accidental removal
    or frontmatter rot.
    """
    repo_root = Path(__file__).resolve().parents[3]
    docs_root = repo_root / "docs"
    assert docs_root.exists(), f"docs/ missing at {docs_root}"

    pages = MarkdownLoader().load_directory(docs_root)
    by_id = {p.id: p for p in pages}

    required_pages = {
        "rules/actions",
        "dashboard/issues-threshold",
        "optimization/profile-editor",
        "account/profile",
        "settings/extension-rules",
    }
    missing = required_pages - by_id.keys()
    assert not missing, f"Stage 11 docs missing: {missing}"

    # Frontmatter completeness — each new page must have a category
    # (not the default "general") and at least one help_context so
    # the help drawer can find it.
    for page_id in required_pages:
        page = by_id[page_id]
        assert page.category != "general", (
            f"{page_id}: frontmatter is missing or has fallback category"
        )
        assert page.help_contexts, (
            f"{page_id}: frontmatter must declare at least one help_context"
        )
        assert page.title, f"{page_id}: title must be non-empty"

    # The ``rules/actions`` rewrite must mention the Stage 9 actions
    # (quarantine, delete) since that's the whole point of the update.
    actions_body = by_id["rules/actions"].body_markdown.lower()
    assert "quarantine" in actions_body
    assert "delete" in actions_body
    assert "confirm" in actions_body  # the soft-vs-hard-delete switch

