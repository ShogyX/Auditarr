"""Pin the route registration order for ``/api/v1/rules``.

FastAPI matches routes in declaration order. Literal segments
(e.g. ``/suggestions/stale``, ``/suggestions/ai-usage``) must be
declared BEFORE the wildcard ``/suggestions/{suggestion_id}`` or
the literal segment is swallowed as a path param value and the
endpoint 404s with "Suggestion not found".

This test asserts the order so a future PR that reshuffles the
file can't silently regress it.
"""

from __future__ import annotations

from app.api.v1.rules import router


LITERAL_GET_PATHS = (
    "/rules/suggestions/ai-usage",
    "/rules/suggestions/stale",
)
WILDCARD_PATH = "/rules/suggestions/{suggestion_id}"


def _index_of(path: str) -> int:
    for i, route in enumerate(router.routes):
        if getattr(route, "path", None) == path:
            return i
    raise AssertionError(f"route {path!r} not registered on the rules router")


def test_literal_suggestion_paths_precede_wildcard() -> None:
    wildcard_idx = _index_of(WILDCARD_PATH)
    for literal in LITERAL_GET_PATHS:
        literal_idx = _index_of(literal)
        assert literal_idx < wildcard_idx, (
            f"{literal!r} (idx {literal_idx}) must be declared before "
            f"{WILDCARD_PATH!r} (idx {wildcard_idx}) or FastAPI will "
            "match the literal as a suggestion_id wildcard."
        )
