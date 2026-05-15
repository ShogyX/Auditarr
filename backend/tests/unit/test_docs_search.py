"""Search index unit tests."""

from __future__ import annotations

from app.documentation.search import SearchIndex
from app.documentation.types import DocPage


def _page(
    *,
    id: str,
    title: str,
    body: str,
    category: str = "general",
    tags: list[str] | None = None,
    summary: str = "",
) -> DocPage:
    return DocPage(
        id=id,
        title=title,
        body_markdown=body,
        body_html=f"<p>{body}</p>",
        category=category,
        tags=tags or [],
        summary=summary,
    )


def test_returns_empty_for_blank_query() -> None:
    idx = SearchIndex()
    idx.rebuild([_page(id="a", title="Codec rules", body="hevc av1")])
    assert idx.search("", pages_by_id={"a": _page(id="a", title="Codec rules", body="x")}) == []


def test_title_outranks_body() -> None:
    a = _page(id="a", title="Codec", body="word word word")
    b = _page(id="b", title="Other", body="codec mention buried in body")
    idx = SearchIndex()
    idx.rebuild([a, b])
    hits = idx.search("codec", pages_by_id={"a": a, "b": b})
    assert hits[0].page_id == "a"
    assert len(hits) == 2


def test_multi_term_match_is_boosted() -> None:
    a = _page(id="a", title="Audio settings", body="bitrate guidance")
    b = _page(id="b", title="Video", body="codec bitrate streams elsewhere")
    idx = SearchIndex()
    idx.rebuild([a, b])
    hits = idx.search("audio bitrate", pages_by_id={"a": a, "b": b})
    # Page A matches both "audio" and "bitrate"; page B only "bitrate".
    assert hits[0].page_id == "a"


def test_tag_matches() -> None:
    a = _page(id="a", title="X", body="nothing relevant", tags=["plex"])
    b = _page(id="b", title="Y", body="lengthy description here without the magic")
    idx = SearchIndex()
    idx.rebuild([a, b])
    hits = idx.search("plex", pages_by_id={"a": a, "b": b})
    assert [h.page_id for h in hits] == ["a"]


def test_excerpt_contains_match_neighborhood() -> None:
    page = _page(
        id="x",
        title="Long doc",
        body="Some preamble. The codec is hevc which is fine. More content after.",
    )
    idx = SearchIndex()
    idx.rebuild([page])
    hits = idx.search("hevc", pages_by_id={"x": page})
    assert "hevc" in hits[0].excerpt.lower()


def test_stopwords_ignored() -> None:
    a = _page(id="a", title="The codec is great", body="x")
    b = _page(id="b", title="Codec choices", body="x")
    idx = SearchIndex()
    idx.rebuild([a, b])
    # "the" alone produces nothing; "codec" matches both.
    assert idx.search("the", pages_by_id={"a": a, "b": b}) == []
    assert len(idx.search("codec", pages_by_id={"a": a, "b": b})) == 2


def test_limit_caps_results() -> None:
    pages = [
        _page(id=f"p{i}", title=f"codec page {i}", body="x") for i in range(20)
    ]
    idx = SearchIndex()
    idx.rebuild(pages)
    hits = idx.search("codec", pages_by_id={p.id: p for p in pages}, limit=5)
    assert len(hits) == 5
