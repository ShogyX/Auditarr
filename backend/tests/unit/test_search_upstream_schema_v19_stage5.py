"""v1.9 Stage 5.1 — Rule action ``search_upstream`` — schema +
evaluator tests.

Pins:
  1. Schema accepts {sonarr, radarr, bazarr} as target,
     normalizes case + whitespace, rejects unknown targets.
  2. integration_id is required and min_length=1.
  3. ``extra="forbid"`` blocks unknown keys.
  4. Evaluator's _apply_action appends one entry per matched
     ``search_upstream`` action.
  5. merge_into extends search_upstream_requests across rules
     (deduplication is the service layer's job, not the
     evaluator's).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rules.evaluator import EvaluationInput, evaluate, EvaluationResult
from app.rules.schema import (
    SEARCH_UPSTREAM_TARGETS,
    RuleDefinition,
    SearchUpstream,
)


def _input(**overrides) -> EvaluationInput:
    defaults = dict(
        media_file_id="f1",
        path="/data/tv/Show/S01/ep.mkv",
        filename="ep.mkv",
        extension="mkv",
        category="media",
        is_orphaned=True,
    )
    defaults.update(overrides)
    return EvaluationInput(**defaults)


# ── Schema ──────────────────────────────────────────────────────


def test_schema_targets_constant() -> None:
    assert SEARCH_UPSTREAM_TARGETS == frozenset({"sonarr", "radarr", "bazarr"})


@pytest.mark.parametrize("target", ["sonarr", "radarr", "bazarr"])
def test_schema_accepts_all_targets(target: str) -> None:
    action = SearchUpstream(
        type="search_upstream", target=target, integration_id="i1"
    )
    assert action.target == target
    assert action.integration_id == "i1"


def test_schema_lowercases_target() -> None:
    """Targets are normalized — operator-typed 'Sonarr' should
    persist as 'sonarr'."""
    action = SearchUpstream(
        type="search_upstream", target="  Sonarr  ", integration_id="i"
    )
    assert action.target == "sonarr"


def test_schema_rejects_unknown_target() -> None:
    with pytest.raises(ValidationError) as exc:
        SearchUpstream(
            type="search_upstream", target="plex", integration_id="i"
        )
    # The error message includes the allowed set.
    assert "must be one of" in str(exc.value)


def test_schema_requires_integration_id() -> None:
    with pytest.raises(ValidationError):
        SearchUpstream(
            type="search_upstream", target="sonarr", integration_id=""
        )


def test_schema_forbids_extra_keys() -> None:
    """Reserves the action namespace for future fields without
    silent acceptance of typos."""
    with pytest.raises(ValidationError):
        SearchUpstream.model_validate(
            {
                "type": "search_upstream",
                "target": "sonarr",
                "integration_id": "i",
                "extra_field": "x",
            }
        )


def test_schema_dispatches_via_action_discriminator() -> None:
    """The full RuleDefinition path routes ``search_upstream``
    through the Action discriminated union."""
    d = RuleDefinition.model_validate(
        {
            "match": {"field": "is_orphaned", "op": "eq", "value": True},
            "actions": [
                {
                    "type": "search_upstream",
                    "target": "sonarr",
                    "integration_id": "abc",
                }
            ],
        }
    )
    assert len(d.actions) == 1
    assert isinstance(d.actions[0], SearchUpstream)


# ── Evaluator ───────────────────────────────────────────────────


def test_evaluator_appends_search_upstream_request() -> None:
    d = RuleDefinition.model_validate(
        {
            "match": {"field": "is_orphaned", "op": "eq", "value": True},
            "actions": [
                {
                    "type": "search_upstream",
                    "target": "sonarr",
                    "integration_id": "i1",
                }
            ],
        }
    )
    r = evaluate(d, _input(is_orphaned=True))
    assert r.matched is True
    assert r.search_upstream_requests == [
        {"target": "sonarr", "integration_id": "i1"}
    ]


def test_evaluator_no_request_when_rule_does_not_match() -> None:
    """If the match clause is false, no action fires — including
    search_upstream."""
    d = RuleDefinition.model_validate(
        {
            "match": {"field": "is_orphaned", "op": "eq", "value": True},
            "actions": [
                {
                    "type": "search_upstream",
                    "target": "sonarr",
                    "integration_id": "i1",
                }
            ],
        }
    )
    r = evaluate(d, _input(is_orphaned=False))
    assert r.matched is False
    assert r.search_upstream_requests == []


def test_evaluator_multiple_actions_in_one_rule() -> None:
    """Two ``search_upstream`` actions on the same rule both fire
    — they may target different integrations (e.g. Sonarr +
    Bazarr) for the same matched file."""
    d = RuleDefinition.model_validate(
        {
            "match": {"field": "is_orphaned", "op": "eq", "value": True},
            "actions": [
                {
                    "type": "search_upstream",
                    "target": "sonarr",
                    "integration_id": "snrr-1",
                },
                {
                    "type": "search_upstream",
                    "target": "bazarr",
                    "integration_id": "bzr-1",
                },
            ],
        }
    )
    r = evaluate(d, _input(is_orphaned=True))
    assert r.search_upstream_requests == [
        {"target": "sonarr", "integration_id": "snrr-1"},
        {"target": "bazarr", "integration_id": "bzr-1"},
    ]


def test_merge_into_extends_search_upstream_requests() -> None:
    """When the service merges per-rule results, the
    ``search_upstream_requests`` lists concatenate. Deduplication
    happens at the service layer (rules_service)."""
    a = EvaluationResult(matched=True)
    a.search_upstream_requests.append(
        {"target": "sonarr", "integration_id": "s1"}
    )
    b = EvaluationResult(matched=True)
    b.search_upstream_requests.append(
        {"target": "radarr", "integration_id": "r1"}
    )
    # Even a duplicate at the evaluator level merges through —
    # the service is responsible for de-duplication.
    b.search_upstream_requests.append(
        {"target": "sonarr", "integration_id": "s1"}
    )
    a.merge_into(b)
    assert b.search_upstream_requests == [
        {"target": "radarr", "integration_id": "r1"},
        {"target": "sonarr", "integration_id": "s1"},
        {"target": "sonarr", "integration_id": "s1"},
    ]
