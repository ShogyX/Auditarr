"""Stage 06 (v1.7) — ``probe_failed`` DSL field tests.

Plan §372:
    A rule with ``probe_failed eq true`` fires only on rows
    where the column is True.

The pre-Stage-06 evaluator didn't know about ``probe_failed`` as
a DSL field; the column existed (set by the scanner) but rules
couldn't reference it. Stage 06 added it to ``SUPPORTED_FIELDS``
+ ``BOOL_FIELDS`` (schema) AND to ``EvaluationInput`` (evaluator)
AND to ``RulesService.build_input`` (service layer). This file
pins all three together by running the rule through ``evaluate``.
"""

from __future__ import annotations

import pytest

from app.rules.evaluator import EvaluationInput, evaluate
from app.rules.schema import RuleDefinition


def _input(*, probe_failed: bool, path: str = "/lib/a.mkv") -> EvaluationInput:
    """Minimal EvaluationInput for ``probe_failed`` tests. Other
    fields default to "neutral" values that won't match anything
    accidentally."""
    return EvaluationInput(
        media_file_id="m1",
        path=path,
        filename=path.rsplit("/", 1)[-1],
        extension=path.rsplit(".", 1)[-1],
        category="media",
        size_bytes=100,
        probe_failed=probe_failed,
    )


# ── Basic eq semantics ─────────────────────────────────────────


def test_probe_failed_eq_true_matches_when_true() -> None:
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "probe_failed", "op": "eq", "value": True},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    result = evaluate(definition, _input(probe_failed=True))
    assert result.matched is True
    assert result.severity == "warn"


def test_probe_failed_eq_true_does_not_match_when_false() -> None:
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "probe_failed", "op": "eq", "value": True},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    result = evaluate(definition, _input(probe_failed=False))
    assert result.matched is False
    assert result.severity is None


def test_probe_failed_eq_false_matches_when_false() -> None:
    """Inverse rule: alert on files where the probe DID succeed
    (useful for confirming a scan covered the library)."""
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "probe_failed", "op": "eq", "value": False},
            "actions": [{"type": "add_tag", "tag": "probe-ok"}],
        }
    )
    result = evaluate(definition, _input(probe_failed=False))
    assert result.matched is True
    assert "probe-ok" in result.add_tags


def test_probe_failed_ne_inverts_the_match() -> None:
    """``ne true`` is the same as ``eq false`` — verify both
    operator forms work."""
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "probe_failed", "op": "ne", "value": True},
            "actions": [{"type": "add_tag", "tag": "scanned"}],
        }
    )
    # False row: matches.
    r1 = evaluate(definition, _input(probe_failed=False))
    assert r1.matched is True
    # True row: doesn't match.
    r2 = evaluate(definition, _input(probe_failed=True))
    assert r2.matched is False


# ── Composition with other fields ──────────────────────────────


def test_probe_failed_in_allof_with_extension() -> None:
    """Realistic composite rule: alert on probe failures in
    .mkv files only. Pins that the new field plays nicely with
    the existing combinator + condition machinery."""
    definition = RuleDefinition.model_validate(
        {
            "match": {
                "all": [
                    {"field": "probe_failed", "op": "eq", "value": True},
                    {"field": "extension", "op": "eq", "value": "mkv"},
                ],
            },
            "actions": [{"type": "set_severity", "severity": "high"}],
        }
    )
    # Probe failed + mkv → matches.
    r1 = evaluate(definition, _input(probe_failed=True, path="/lib/a.mkv"))
    assert r1.matched is True
    assert r1.severity == "high"
    # Probe failed but mp4 → doesn't match.
    r2 = evaluate(definition, _input(probe_failed=True, path="/lib/a.mp4"))
    assert r2.matched is False
    # mkv but probe ok → doesn't match.
    r3 = evaluate(definition, _input(probe_failed=False, path="/lib/a.mkv"))
    assert r3.matched is False


def test_probe_failed_in_anyof_with_orphaned() -> None:
    """Either a probe failure OR an orphaned file → flag.
    Pins that ``probe_failed`` and ``is_orphaned`` are separately
    addressable predicates (the docstring of MediaFile.is_orphaned
    explicitly distinguishes the two)."""
    definition = RuleDefinition.model_validate(
        {
            "match": {
                "any": [
                    {"field": "probe_failed", "op": "eq", "value": True},
                    {"field": "is_orphaned", "op": "eq", "value": True},
                ],
            },
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    # Probe failed → matches.
    r1 = evaluate(definition, _input(probe_failed=True))
    assert r1.matched is True
    # Both False → no match.
    inp_clean = EvaluationInput(
        media_file_id="m1",
        path="/lib/x.mkv",
        filename="x.mkv",
        extension="mkv",
        category="media",
        size_bytes=100,
        probe_failed=False,
        is_orphaned=False,
    )
    r2 = evaluate(definition, inp_clean)
    assert r2.matched is False


# ── Op constraints ─────────────────────────────────────────────


def test_probe_failed_rejects_non_bool_ops_at_validation() -> None:
    """``probe_failed`` is a BOOL field — only eq/ne are valid.
    A regex op should fail validation."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {
                    "field": "probe_failed",
                    "op": "regex",
                    "value": "true",
                },
                "actions": [{"type": "set_severity", "severity": "warn"}],
            }
        )


def test_probe_failed_with_numeric_op_rejected() -> None:
    """Numeric ops (lt, gt) are not valid for a bool field."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "probe_failed", "op": "gt", "value": 0},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            }
        )
