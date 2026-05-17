"""Stage 06 (v1.7) — Rule DSL extensions.

Schema-level contract tests for the four DSL additions Stage 06
introduces:

  1. ``probe_failed`` field (bool). Added to ``SUPPORTED_FIELDS``
     and ``BOOL_FIELDS``. A rule body using it validates; the
     evaluator-side wiring is exercised in
     ``test_probe_failed_dsl_stage06.py`` once the column exists.

  2. ``vt_status`` field (literal string). Added to
     ``SUPPORTED_FIELDS``. Values restricted to
     ``VT_STATUS_VALUES`` (clean/malicious/suspicious/not_found/
     error). Out-of-set values rejected at validation. Both ``eq``
     and ``in`` ops are valid.

  3. ``Notify.throttle`` optional block. ``window_seconds >= 60``
     and ``max_per_window >= 1`` enforced.

  4. ``RuleDefinition.acknowledged_destructive`` — required True
     when any action is ``delete``; forbidden True otherwise.
     The defensive layer that replaced the pre-Stage-05
     ``Delete.confirm`` flag (per addendum A.0.1).
"""

from __future__ import annotations

import pytest
import pydantic

from app.rules.schema import (
    BOOL_FIELDS,
    NotifyThrottle,
    RuleDefinition,
    SUPPORTED_FIELDS,
    VT_STATUS_VALUES,
)


# ── SUPPORTED_FIELDS / BOOL_FIELDS membership ──────────────────


def test_probe_failed_in_supported_fields() -> None:
    assert "probe_failed" in SUPPORTED_FIELDS


def test_probe_failed_in_bool_fields() -> None:
    """``probe_failed`` is a bool field — eq/ne ops only."""
    assert "probe_failed" in BOOL_FIELDS


def test_vt_status_in_supported_fields() -> None:
    assert "vt_status" in SUPPORTED_FIELDS


def test_vt_status_values_match_addendum_B4() -> None:
    """Per addendum B.4: the canonical set is exactly five strings."""
    assert VT_STATUS_VALUES == frozenset(
        {"clean", "malicious", "suspicious", "not_found", "error"}
    )


# ── probe_failed validates with bool ops ───────────────────────


def test_probe_failed_eq_true_validates() -> None:
    RuleDefinition.model_validate(
        {
            "match": {"field": "probe_failed", "op": "eq", "value": True},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )


def test_probe_failed_with_string_op_rejected() -> None:
    """``regex`` is a string-op; bool fields only accept eq/ne."""
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


# ── vt_status enforces literal values ──────────────────────────


def test_vt_status_eq_valid_value_accepted() -> None:
    RuleDefinition.model_validate(
        {
            "match": {
                "field": "vt_status",
                "op": "eq",
                "value": "malicious",
            },
            "actions": [{"type": "set_severity", "severity": "crit"}],
        }
    )


def test_vt_status_eq_invalid_value_rejected() -> None:
    with pytest.raises(pydantic.ValidationError) as exc_info:
        RuleDefinition.model_validate(
            {
                "match": {
                    "field": "vt_status",
                    "op": "eq",
                    "value": "not-a-real-status",
                },
                "actions": [{"type": "set_severity", "severity": "crit"}],
            }
        )
    assert "vt_status" in str(exc_info.value)


def test_vt_status_in_with_list_accepted() -> None:
    """``in`` op accepts a list of allowed values."""
    RuleDefinition.model_validate(
        {
            "match": {
                "field": "vt_status",
                "op": "in",
                "value": ["malicious", "suspicious"],
            },
            "actions": [{"type": "set_severity", "severity": "crit"}],
        }
    )


def test_vt_status_in_with_bad_list_element_rejected() -> None:
    """One bad element in the list fails the whole rule."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {
                    "field": "vt_status",
                    "op": "in",
                    "value": ["malicious", "not-real"],
                },
                "actions": [{"type": "set_severity", "severity": "crit"}],
            }
        )


def test_vt_status_in_with_empty_list_rejected() -> None:
    """An empty ``in`` list is a degenerate case — every value
    would fail to match — so the validator rejects it as a
    likely operator mistake rather than silently accepting an
    always-false rule."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {
                    "field": "vt_status",
                    "op": "in",
                    "value": [],
                },
                "actions": [{"type": "set_severity", "severity": "crit"}],
            }
        )


# ── Notify.throttle ────────────────────────────────────────────


def test_notify_without_throttle_defaults_none() -> None:
    """The throttle field is optional; rules without it work
    exactly as in Stage 9."""
    r = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "notify", "channel": "email"}],
        }
    )
    notify = r.actions[0]
    assert notify.type == "notify"
    assert notify.throttle is None  # type: ignore[union-attr]


def test_notify_with_valid_throttle_accepted() -> None:
    r = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "email",
                    "throttle": {
                        "window_seconds": 300,
                        "max_per_window": 5,
                    },
                }
            ],
        }
    )
    notify = r.actions[0]
    assert notify.throttle == NotifyThrottle(  # type: ignore[union-attr]
        window_seconds=300, max_per_window=5
    )


def test_notify_throttle_window_below_60_rejected() -> None:
    """``window_seconds >= 60`` per plan §352."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [
                    {
                        "type": "notify",
                        "channel": "email",
                        "throttle": {
                            "window_seconds": 30,
                            "max_per_window": 5,
                        },
                    }
                ],
            }
        )


def test_notify_throttle_max_per_window_below_1_rejected() -> None:
    """``max_per_window >= 1`` per plan §352. Zero would mean
    "never send" — operators should disable the rule instead."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [
                    {
                        "type": "notify",
                        "channel": "email",
                        "throttle": {
                            "window_seconds": 60,
                            "max_per_window": 0,
                        },
                    }
                ],
            }
        )


def test_notify_throttle_extra_fields_rejected() -> None:
    """``extra="forbid"`` keeps the throttle shape closed."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [
                    {
                        "type": "notify",
                        "channel": "email",
                        "throttle": {
                            "window_seconds": 60,
                            "max_per_window": 5,
                            "burst_allowance": 10,  # not a real field
                        },
                    }
                ],
            }
        )


# ── acknowledged_destructive ───────────────────────────────────


def test_delete_action_without_ack_rejected() -> None:
    """Per addendum A.0.1 — a rule with a delete action requires
    ``acknowledged_destructive: true`` at the rule level."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "junk"},
                "actions": [{"type": "delete", "reason": "auto-purge"}],
            }
        )
    assert "acknowledged_destructive" in str(exc_info.value)


def test_delete_action_with_ack_accepted() -> None:
    r = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "junk"},
            "actions": [{"type": "delete", "reason": "auto-purge"}],
            "acknowledged_destructive": True,
        }
    )
    assert r.acknowledged_destructive is True


def test_non_delete_rule_with_ack_rejected() -> None:
    """Setting the ack flag on a non-deleting rule is forbidden —
    misleading to a reader who'd assume the rule deletes."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "add_tag", "tag": "foo"}],
                "acknowledged_destructive": True,
            }
        )


def test_non_delete_rule_without_ack_accepted() -> None:
    """The common case: a regular non-destructive rule with the
    default ``acknowledged_destructive: False``."""
    r = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "add_tag", "tag": "foo"}],
        }
    )
    assert r.acknowledged_destructive is False


def test_mixed_actions_with_delete_require_ack() -> None:
    """Any delete in the actions list triggers the ack
    requirement — even when other actions are also present."""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "junk"},
                "actions": [
                    {"type": "set_severity", "severity": "warn"},
                    {"type": "add_tag", "tag": "for-deletion"},
                    {"type": "delete", "reason": "junk extension"},
                ],
            }
        )


def test_acknowledged_destructive_false_with_delete_rejected() -> None:
    """Explicit False is still rejected — the operator must
    actively set True. (Default of False is what the validator
    intercepts in the no-ack-supplied case.)"""
    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "junk"},
                "actions": [{"type": "delete"}],
                "acknowledged_destructive": False,
            }
        )
