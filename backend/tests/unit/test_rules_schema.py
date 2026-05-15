"""Rules DSL schema validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rules.schema import RuleDefinition


def _ok(doc: dict) -> RuleDefinition:
    return RuleDefinition.model_validate(doc)


def _bad(doc: dict) -> ValidationError:
    with pytest.raises(ValidationError) as exc:
        RuleDefinition.model_validate(doc)
    return exc.value


def test_minimal_rule_validates() -> None:
    rule = _ok(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert rule.actions[0].severity == "warn"


def test_all_combinator() -> None:
    rule = _ok(
        {
            "match": {
                "all": [
                    {"field": "video_codec", "op": "eq", "value": "hevc"},
                    {"field": "bitrate_kbps", "op": "gt", "value": 25000},
                ]
            },
            "actions": [{"type": "add_tag", "tag": "fat-hevc"}],
        }
    )
    assert rule.actions[0].tag == "fat-hevc"


def test_nested_combinators() -> None:
    _ok(
        {
            "match": {
                "any": [
                    {
                        "all": [
                            {"field": "container", "op": "eq", "value": "mkv"},
                            {"field": "has_subtitles", "op": "eq", "value": False},
                        ]
                    },
                    {"field": "is_orphaned", "op": "eq", "value": True},
                ]
            },
            "actions": [{"type": "set_severity", "severity": "high"}],
        }
    )


def test_unknown_field_rejected() -> None:
    err = _bad(
        {
            "match": {"field": "nonsense", "op": "eq", "value": "x"},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert "Unsupported field" in str(err)


def test_wrong_op_for_numeric_field_rejected() -> None:
    err = _bad(
        {
            "match": {"field": "bitrate_kbps", "op": "regex", "value": "x"},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert "not valid for field" in str(err)


def test_wrong_op_for_array_field_rejected() -> None:
    err = _bad(
        {
            "match": {"field": "tags", "op": "regex", "value": "x"},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert "not valid for field" in str(err)


def test_unknown_severity_rejected() -> None:
    err = _bad(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "h264"},
            "actions": [{"type": "set_severity", "severity": "explosive"}],
        }
    )
    assert "severity" in str(err).lower()


def test_actions_required_non_empty() -> None:
    err = _bad(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "h264"},
            "actions": [],
        }
    )
    assert "actions" in str(err).lower()


def test_combinator_requires_at_least_one_child() -> None:
    err = _bad(
        {
            "match": {"all": []},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert "all" in str(err).lower()


def test_unknown_action_type_rejected() -> None:
    err = _bad(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "h264"},
            "actions": [{"type": "explode", "yield": "kaboom"}],
        }
    )
    assert "explode" in str(err).lower()


def test_extra_keys_rejected_on_condition() -> None:
    err = _bad(
        {
            "match": {
                "field": "video_codec",
                "op": "eq",
                "value": "h264",
                "extra": "nope",
            },
            "actions": [{"type": "set_severity", "severity": "warn"}],
        }
    )
    assert "extra" in str(err).lower()
