"""Stage 06 (v1.7) — VirusTotal non-clean built-in rule.

Plan §375:
    The new built-in validates and matches a fixture row where
    ``vt_status="malicious"``.

This file pins the contract of the new built-in rule:
  1. It validates against the Stage 06 schema (vt_status field
     + ``in`` op on a string field).
  2. It matches a fixture EvaluationInput where ``vt_status =
     "malicious"`` (and "suspicious").
  3. It does NOT match for "clean", "not_found", "error", or NULL.
  4. The match produces severity=crit + the canonical tag.
"""

from __future__ import annotations


from app.rules.builtin import BUILTIN_RULES
from app.rules.evaluator import EvaluationInput, evaluate
from app.rules.schema import RuleDefinition


def _vt_rule_definition() -> RuleDefinition:
    """Pull the "VirusTotal non-clean" rule body from the builtin
    set and parse it. If the rule has been renamed or removed,
    this test should fail loudly so the regression is caught."""
    for spec in BUILTIN_RULES:
        if spec.name == "VirusTotal non-clean":
            return RuleDefinition.model_validate(spec.definition)
    raise AssertionError(
        "'VirusTotal non-clean' builtin rule missing from BUILTIN_RULES"
    )


def _input(*, vt_status: str | None) -> EvaluationInput:
    return EvaluationInput(
        media_file_id="m1",
        path="/lib/movie.mkv",
        filename="movie.mkv",
        extension="mkv",
        category="media",
        size_bytes=1_000_000,
        vt_status=vt_status,
    )


# ── Contract: the rule exists + parses ─────────────────────────


def test_virustotal_rule_is_in_builtin_set() -> None:
    """The new rule must be in BUILTIN_RULES so the seeding pass
    picks it up."""
    names = {r.name for r in BUILTIN_RULES}
    assert "VirusTotal non-clean" in names


def test_virustotal_rule_body_validates_against_stage_06_schema() -> None:
    """The rule body parses without error — relies on Stage 06's
    ``vt_status`` field + ``in`` op + literal value validation."""
    defn = _vt_rule_definition()
    # No exception = pass; also confirm it's well-shaped.
    assert defn.actions
    assert any(
        getattr(a, "type", None) == "set_severity" for a in defn.actions
    )


def test_virustotal_rule_has_no_destructive_action() -> None:
    """The Stage 06 VT rule is intentionally tag-and-severity-only.
    Per addendum A.0.4: built-in rules MUST NOT ship a delete
    action; the operator must opt into auto-delete by duplicating
    the rule and acknowledging destructive intent themselves.

    Regression guard: if a future stage adds a delete to this
    rule, the schema would also force the test author to set
    ``acknowledged_destructive: true`` — which would be Auditarr
    making the acknowledgement on the operator's behalf. Refuse."""
    defn = _vt_rule_definition()
    has_delete = any(
        getattr(a, "type", None) == "delete" for a in defn.actions
    )
    assert has_delete is False
    assert defn.acknowledged_destructive is False


# ── Matching ───────────────────────────────────────────────────


def test_virustotal_rule_matches_malicious() -> None:
    """Plan §375: a fixture row with ``vt_status="malicious"``
    matches and the rule produces a crit severity."""
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status="malicious"))
    assert result.matched is True
    assert result.severity == "crit"
    assert "virustotal-non-clean" in result.add_tags


def test_virustotal_rule_matches_suspicious() -> None:
    """Both literals in the rule's ``in`` list should match."""
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status="suspicious"))
    assert result.matched is True
    assert result.severity == "crit"


# ── Non-matching ───────────────────────────────────────────────


def test_virustotal_rule_does_not_match_clean() -> None:
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status="clean"))
    assert result.matched is False


def test_virustotal_rule_does_not_match_not_found() -> None:
    """``not_found`` is a real VT verdict (the file's hash isn't
    in the VT database). Not a security concern, so don't fire."""
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status="not_found"))
    assert result.matched is False


def test_virustotal_rule_does_not_match_error() -> None:
    """An ``error`` verdict means VT itself failed — not a
    security signal. Don't escalate, the operator's UI will
    surface the VT integration health separately."""
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status="error"))
    assert result.matched is False


def test_virustotal_rule_does_not_match_null_vt_status() -> None:
    """A NULL vt_status means "never looked up". Until Stage 10
    wires the VT plugin, every row has NULL — the rule must not
    fire for those (operator would see false positives)."""
    defn = _vt_rule_definition()
    result = evaluate(defn, _input(vt_status=None))
    assert result.matched is False
