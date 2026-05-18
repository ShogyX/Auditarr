"""v1.9 Stage 4.6 — VT trigger as rule action + VT scope restriction.

Pins:
  1. ``vt_lookup`` action validates through the rule schema.
  2. The evaluator sets ``vt_lookup_requested`` when the action matches.
  3. ``merge_into`` ORs ``vt_lookup_requested`` across rules.
  4. ``file_passes_vt_scan_scope`` honors extension / category /
     required-tags filters with AND semantics.
"""

from __future__ import annotations

import pytest

from app.rules.evaluator import EvaluationInput, EvaluationResult, evaluate
from app.rules.schema import RuleDefinition
from plugins.virustotal.backend import file_passes_vt_scan_scope


def _file(**overrides) -> EvaluationInput:
    base = {
        "media_file_id": "f-1",
        "path": "/lib/x.iso",
        "filename": "x.iso",
        "extension": "iso",
        "category": "media",
    }
    base.update(overrides)
    return EvaluationInput(**base)


# ── vt_lookup action ────────────────────────────────────────────


def test_vt_lookup_action_validates() -> None:
    """The action type must validate against the strict schema."""
    defn = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "iso"},
            "actions": [{"type": "vt_lookup"}],
        }
    )
    # Action discriminator picked the right class.
    from app.rules.schema import VtLookup

    assert isinstance(defn.actions[0], VtLookup)


def test_vt_lookup_action_rejects_extra_fields() -> None:
    """``extra='forbid'`` on VtLookup means a typo'd param fails
    loudly. Future params would be a deliberate schema bump."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "iso"},
                "actions": [{"type": "vt_lookup", "priority": "high"}],
            }
        )


def test_evaluator_sets_vt_lookup_requested_on_match() -> None:
    defn = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "iso"},
            "actions": [{"type": "vt_lookup"}],
        }
    )
    result = evaluate(defn, _file(extension="iso"))
    assert result.matched is True
    assert result.vt_lookup_requested is True


def test_evaluator_leaves_vt_lookup_requested_false_when_no_match() -> None:
    defn = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "iso"},
            "actions": [{"type": "vt_lookup"}],
        }
    )
    result = evaluate(defn, _file(extension="mkv"))
    assert result.matched is False
    assert result.vt_lookup_requested is False


def test_merge_into_ors_vt_lookup_requested() -> None:
    """Two rules both with vt_lookup — aggregate ends True."""
    a = EvaluationResult(matched=True, vt_lookup_requested=True)
    b = EvaluationResult(matched=True, vt_lookup_requested=False)
    aggregate = EvaluationResult(matched=False)
    a.merge_into(aggregate)
    b.merge_into(aggregate)
    assert aggregate.vt_lookup_requested is True

    # Inverse: aggregate starts True and another false-ish merge
    # doesn't clear it.
    aggregate2 = EvaluationResult(matched=True, vt_lookup_requested=True)
    b.merge_into(aggregate2)
    assert aggregate2.vt_lookup_requested is True


# ── file_passes_vt_scan_scope ───────────────────────────────────


def test_scope_no_options_returns_true() -> None:
    """No options dict at all = pre-1.9 behavior, every file passes."""
    assert (
        file_passes_vt_scan_scope(
            extension="mkv",
            category="media",
            tags=[],
            vt_options=None,
        )
        is True
    )
    assert (
        file_passes_vt_scan_scope(
            extension="mkv",
            category="media",
            tags=[],
            vt_options={},
        )
        is True
    )


def test_scope_empty_lists_pass_everything() -> None:
    """Default config = empty allowlists = no scope filter."""
    opts = {
        "vt_scan_extensions": [],
        "vt_scan_categories": [],
        "vt_scan_required_tags": [],
    }
    assert (
        file_passes_vt_scan_scope(
            extension="mkv",
            category="media",
            tags=[],
            vt_options=opts,
        )
        is True
    )


def test_scope_extension_allowlist() -> None:
    opts = {"vt_scan_extensions": ["iso", "exe"]}
    assert (
        file_passes_vt_scan_scope(
            extension="iso", category="media", tags=[], vt_options=opts
        )
        is True
    )
    assert (
        file_passes_vt_scan_scope(
            extension="mkv", category="media", tags=[], vt_options=opts
        )
        is False
    )


def test_scope_extension_check_is_case_insensitive_and_dot_tolerant() -> None:
    """Operator might type ``.ISO`` instead of ``iso`` — both
    spellings should compare equal."""
    opts = {"vt_scan_extensions": [".ISO"]}
    assert (
        file_passes_vt_scan_scope(
            extension="iso", category="media", tags=[], vt_options=opts
        )
        is True
    )


def test_scope_category_allowlist_defaults_to_media() -> None:
    """The default ``vt_scan_categories=["media"]`` excludes
    sidecars."""
    opts = {"vt_scan_categories": ["media"]}
    assert (
        file_passes_vt_scan_scope(
            extension="mkv", category="media", tags=[], vt_options=opts
        )
        is True
    )
    # A subtitle sidecar is NOT in scope by default.
    assert (
        file_passes_vt_scan_scope(
            extension="srt",
            category="subtitle",
            tags=[],
            vt_options=opts,
        )
        is False
    )


def test_scope_required_tags_and_semantics() -> None:
    """EVERY required tag must be present (AND)."""
    opts = {"vt_scan_required_tags": ["downloaded", "untrusted"]}
    # Has both — passes.
    assert (
        file_passes_vt_scan_scope(
            extension="iso",
            category="media",
            tags=["downloaded", "untrusted", "other"],
            vt_options=opts,
        )
        is True
    )
    # Missing one — fails.
    assert (
        file_passes_vt_scan_scope(
            extension="iso",
            category="media",
            tags=["downloaded"],
            vt_options=opts,
        )
        is False
    )
    # No tags at all — fails.
    assert (
        file_passes_vt_scan_scope(
            extension="iso",
            category="media",
            tags=[],
            vt_options=opts,
        )
        is False
    )


def test_scope_all_three_must_pass() -> None:
    """The three rules form an AND filter."""
    opts = {
        "vt_scan_extensions": ["iso"],
        "vt_scan_categories": ["media"],
        "vt_scan_required_tags": ["downloaded"],
    }
    # All three pass.
    assert (
        file_passes_vt_scan_scope(
            extension="iso",
            category="media",
            tags=["downloaded"],
            vt_options=opts,
        )
        is True
    )
    # Extension fails.
    assert (
        file_passes_vt_scan_scope(
            extension="mkv",
            category="media",
            tags=["downloaded"],
            vt_options=opts,
        )
        is False
    )
    # Tag fails.
    assert (
        file_passes_vt_scan_scope(
            extension="iso",
            category="media",
            tags=[],
            vt_options=opts,
        )
        is False
    )
