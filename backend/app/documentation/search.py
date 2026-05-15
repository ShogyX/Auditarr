"""In-memory documentation search index.

A pragmatic implementation: tokenize titles + bodies + tags, build an
inverted index, and rank results by a simple combination of term-frequency
and field weights (titles weigh more than body text, tags more than body).

Trade-offs:
* Good enough for thousands of pages (the realistic upper bound for an
  application's own docs).
* Zero external dependencies, no daemon to coordinate, rebuilds on a docs
  reload in milliseconds.
* No stemming or fuzzy matching — but documentation is authored content with
  consistent vocabulary, and the help-context lookups don't need fuzziness.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.documentation.types import DocPage, DocSearchHit

# Field weights for ranking. Tuned to the doc set's character: titles short
# and authoritative, tags curated, body verbose but high-signal.
_TITLE_WEIGHT = 6.0
_TAG_WEIGHT = 4.0
_SUMMARY_WEIGHT = 3.0
_BODY_WEIGHT = 1.0

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
# Conservative English stopword set — small enough not to lose precision.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "has", "have", "he", "her", "his", "i", "if", "in", "into", "is", "it",
        "its", "of", "on", "or", "she", "that", "the", "their", "them", "they",
        "this", "to", "was", "we", "were", "will", "with", "you", "your",
    }
)


def _tokenize(text: str) -> list[str]:
    return [tok for tok in _TOKEN_RE.findall(text.lower()) if tok not in _STOPWORDS]


@dataclass(slots=True)
class _PageFields:
    title_tokens: list[str]
    tag_tokens: list[str]
    summary_tokens: list[str]
    body_tokens: list[str]


class SearchIndex:
    """Inverted index over a corpus of :class:`DocPage` objects."""

    def __init__(self) -> None:
        self._pages: dict[str, _PageFields] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)

    # ── Build ─────────────────────────────────────────────────
    def rebuild(self, pages: list[DocPage]) -> None:
        self._pages.clear()
        self._postings.clear()
        for page in pages:
            self._index_page(page)

    def _index_page(self, page: DocPage) -> None:
        fields = _PageFields(
            title_tokens=_tokenize(page.title),
            tag_tokens=[t.lower() for t in page.tags],
            summary_tokens=_tokenize(page.summary),
            body_tokens=_tokenize(_strip_html(page.body_html)),
        )
        self._pages[page.id] = fields
        for tok in {
            *fields.title_tokens,
            *fields.tag_tokens,
            *fields.summary_tokens,
            *fields.body_tokens,
        }:
            self._postings[tok].add(page.id)

    # ── Query ─────────────────────────────────────────────────
    def search(
        self,
        query: str,
        *,
        pages_by_id: dict[str, DocPage],
        limit: int = 10,
    ) -> list[DocSearchHit]:
        terms = _tokenize(query)
        if not terms:
            return []

        # Candidate set = union of pages containing at least one term.
        # Filter further to AND-style intent by boosting pages that match
        # more terms.
        candidates: set[str] = set()
        for term in terms:
            candidates.update(self._postings.get(term, ()))
        if not candidates:
            return []

        scored: list[DocSearchHit] = []
        for page_id in candidates:
            fields = self._pages.get(page_id)
            page = pages_by_id.get(page_id)
            if fields is None or page is None:
                continue
            score = self._score(fields, terms)
            if score <= 0:
                continue
            scored.append(
                DocSearchHit(
                    page_id=page.id,
                    title=page.title,
                    category=page.category,
                    score=score,
                    excerpt=_excerpt(page, terms),
                )
            )
        scored.sort(key=lambda h: (-h.score, h.title.lower()))
        return scored[:limit]

    @staticmethod
    def _score(fields: _PageFields, terms: list[str]) -> float:
        total = 0.0
        matched_terms = 0
        for term in terms:
            tf = (
                fields.title_tokens.count(term) * _TITLE_WEIGHT
                + fields.tag_tokens.count(term) * _TAG_WEIGHT
                + fields.summary_tokens.count(term) * _SUMMARY_WEIGHT
                + fields.body_tokens.count(term) * _BODY_WEIGHT
            )
            if tf > 0:
                matched_terms += 1
                total += tf
        # Reward queries where multiple terms hit the same page (poor man's
        # AND): a page with 3/3 terms beats one with 1/3 even if the latter
        # has a higher single-term TF.
        if matched_terms == 0:
            return 0.0
        return total * (1.0 + 0.4 * (matched_terms - 1))


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _excerpt(page: DocPage, terms: list[str], radius: int = 80) -> str:
    """Pull a short text excerpt around the first matching term."""
    haystack = _strip_html(page.body_html) or page.summary
    haystack_lc = haystack.lower()
    for term in terms:
        idx = haystack_lc.find(term)
        if idx == -1:
            continue
        start = max(0, idx - radius)
        end = min(len(haystack), idx + len(term) + radius)
        snippet = haystack[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(haystack):
            snippet = snippet + "…"
        return re.sub(r"\s+", " ", snippet)
    return page.summary
