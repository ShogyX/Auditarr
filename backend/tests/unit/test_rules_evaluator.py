"""Rule evaluator tests — pure, no DB."""

from __future__ import annotations


from app.rules.evaluator import EvaluationInput, EvaluationResult, evaluate
from app.rules.schema import RuleDefinition


def _input(**overrides) -> EvaluationInput:
    base = dict(
        media_file_id="m1",
        path="/data/movies/Dune (2021)/movie.mkv",
        filename="movie.mkv",
        extension="mkv",
        category="media",
        container="matroska",
        video_codec="hevc",
        audio_codec="eac3",
        width=3840,
        height=2160,
        duration_seconds=9000.0,
        bitrate_kbps=18000,
        framerate=23.976,
        size_bytes=20_000_000_000,
        has_subtitles=True,
        is_orphaned=False,
        subtitle_languages=["eng"],
        audio_languages=["eng", "jpn"],
        tags=["4k", "remux"],
    )
    base.update(overrides)
    return EvaluationInput(**base)


def _eval(doc: dict, input_: EvaluationInput) -> EvaluationResult:
    return evaluate(RuleDefinition.model_validate(doc), input_)


# ── Basic matching ───────────────────────────────────────────
def test_simple_match_set_severity() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
        _input(),
    )
    assert result.matched is True
    assert result.severity == "info"


def test_simple_match_failure() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "h264"},
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
        _input(),
    )
    assert result.matched is False
    assert result.severity is None


def test_numeric_gt() -> None:
    result = _eval(
        {
            "match": {"field": "bitrate_kbps", "op": "gt", "value": 10000},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        },
        _input(bitrate_kbps=15000),
    )
    assert result.matched is True


def test_numeric_op_with_none_value_does_not_match() -> None:
    """A file without bitrate metadata shouldn't satisfy ``bitrate_kbps > X``."""
    result = _eval(
        {
            "match": {"field": "bitrate_kbps", "op": "gt", "value": 1000},
            "actions": [{"type": "set_severity", "severity": "warn"}],
        },
        _input(bitrate_kbps=None),
    )
    assert result.matched is False


def test_regex_match() -> None:
    result = _eval(
        {
            "match": {
                "field": "filename",
                "op": "regex",
                "value": r"\.mkv$",
            },
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
        _input(),
    )
    assert result.matched is True


def test_bad_regex_does_not_explode() -> None:
    result = _eval(
        {
            "match": {
                "field": "filename",
                "op": "regex",
                "value": "[unterminated",
            },
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
        _input(),
    )
    assert result.matched is False


# ── Combinators ──────────────────────────────────────────────
def test_all_combinator() -> None:
    doc = {
        "match": {
            "all": [
                {"field": "video_codec", "op": "eq", "value": "hevc"},
                {"field": "bitrate_kbps", "op": "gt", "value": 15000},
            ]
        },
        "actions": [{"type": "set_severity", "severity": "warn"}],
    }
    assert _eval(doc, _input()).matched
    assert not _eval(doc, _input(video_codec="h264")).matched
    assert not _eval(doc, _input(bitrate_kbps=5000)).matched


def test_any_combinator() -> None:
    doc = {
        "match": {
            "any": [
                {"field": "video_codec", "op": "eq", "value": "h264"},
                {"field": "bitrate_kbps", "op": "gt", "value": 15000},
            ]
        },
        "actions": [{"type": "set_severity", "severity": "warn"}],
    }
    assert _eval(doc, _input()).matched  # bitrate matches
    assert _eval(doc, _input(video_codec="h264", bitrate_kbps=2000)).matched
    assert not _eval(doc, _input(video_codec="vp9", bitrate_kbps=2000)).matched


# ── Tags ─────────────────────────────────────────────────────
def test_tags_contains() -> None:
    result = _eval(
        {
            "match": {"field": "tags", "op": "contains", "value": "4k"},
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
        _input(),
    )
    assert result.matched is True


def test_tags_any_of() -> None:
    result = _eval(
        {
            "match": {
                "field": "tags",
                "op": "any_of",
                "value": ["missing-subs:en", "remux"],
            },
            "actions": [{"type": "set_severity", "severity": "warn"}],
        },
        _input(),
    )
    assert result.matched is True


def test_tags_none_of() -> None:
    result = _eval(
        {
            "match": {
                "field": "tags",
                "op": "none_of",
                "value": ["missing-subs:en"],
            },
            "actions": [{"type": "set_severity", "severity": "ok"}],
        },
        _input(),
    )
    assert result.matched is True


# ── Actions ──────────────────────────────────────────────────
def test_add_tag_action() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [{"type": "add_tag", "tag": "hevc-flagged"}],
        },
        _input(),
    )
    assert result.add_tags == ["hevc-flagged"]


def test_queue_optimization_action() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [
                {"type": "queue_optimization", "profile": "x265-shrink"}
            ],
        },
        _input(),
    )
    assert result.queue_optimizations == ["x265-shrink"]


def test_notify_action() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "ops",
                    "message": "look at this",
                }
            ],
        },
        _input(),
    )
    # Stage 06 (v1.7): notify dicts now also carry the ``throttle``
    # key (``None`` when the Notify action has no throttle config).
    # The service layer reads this key to decide whether to gate
    # the dispatch through the rule_notification_windows table.
    assert result.notifications == [
        {"channel": "ops", "message": "look at this", "throttle": None}
    ]


# ── Severity monotonicity ────────────────────────────────────
def test_multiple_severity_actions_take_max() -> None:
    result = _eval(
        {
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [
                {"type": "set_severity", "severity": "info"},
                {"type": "set_severity", "severity": "high"},
                {"type": "set_severity", "severity": "warn"},
            ],
        },
        _input(),
    )
    assert result.severity == "high"


def test_merge_into_only_escalates() -> None:
    high = EvaluationResult(matched=True, severity="high", severity_rank=60)
    aggregate = EvaluationResult(matched=True, severity="info", severity_rank=20)
    high.merge_into(aggregate)
    assert aggregate.severity == "high"

    low = EvaluationResult(matched=True, severity="ok", severity_rank=10)
    low.merge_into(aggregate)
    # Lower-rank result must NOT lower the aggregate severity.
    assert aggregate.severity == "high"


def test_merge_into_dedupes_tags() -> None:
    a = EvaluationResult(matched=True, add_tags=["x", "y"])
    b = EvaluationResult(matched=True, add_tags=["y", "z"])
    a.merge_into(b)
    assert b.add_tags == ["y", "z", "x"]
