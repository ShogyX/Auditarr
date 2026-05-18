"""v1.9 Stage 4.7 — Plex/Jellyfin compat built-in rules.

Pins:
  1. The three new rules validate against the schema.
  2. Each one matches the kinds of files PLAN.md §4.7 calls out.
  3. None of them match an uncontroversial 1080p h264 file (which
     would mean an over-broad rule and tons of false positives).

The rules are seeded as plain ``BuiltinRule`` entries for now;
when Stage 4.4 lands they'll migrate to ``rule_templates`` with no
definition change.
"""

from __future__ import annotations

import pytest

from app.rules.builtin import BUILTIN_RULES
from app.rules.evaluator import EvaluationInput, evaluate
from app.rules.schema import RuleDefinition


def _spec(name: str) -> dict:
    for r in BUILTIN_RULES:
        if r.name == name:
            return r.definition
    raise KeyError(name)


def _file(**overrides) -> EvaluationInput:
    base = {
        "media_file_id": "f-1",
        "path": "/lib/x.mkv",
        "filename": "x.mkv",
        "extension": "mkv",
        "category": "media",
    }
    base.update(overrides)
    return EvaluationInput(**base)


@pytest.fixture
def likely() -> RuleDefinition:
    return RuleDefinition.model_validate(_spec("Likely transcode (Plex/Jellyfin)"))


@pytest.fixture
def always() -> RuleDefinition:
    return RuleDefinition.model_validate(_spec("Always transcode (Plex/Jellyfin)"))


@pytest.fixture
def unplayable() -> RuleDefinition:
    return RuleDefinition.model_validate(_spec("Unplayable / Unsupported (Plex/Jellyfin)"))


# ── Likely transcode ────────────────────────────────────────────


def test_likely_matches_1080p_hevc(likely) -> None:
    file = _file(video_codec="hevc", height=1080, audio_codec="aac")
    result = evaluate(likely, file)
    assert result.matched
    assert result.severity == "warn"
    assert "likely-transcode" in result.add_tags


def test_likely_matches_ac3_audio_at_any_resolution(likely) -> None:
    file = _file(video_codec="h264", height=720, audio_codec="ac3")
    result = evaluate(likely, file)
    assert result.matched


def test_likely_does_not_match_clean_1080p_h264_aac(likely) -> None:
    """The most common "this should just work" combo — h264 1080p
    AAC — must NOT trip the likely-transcode flag."""
    file = _file(video_codec="h264", height=1080, audio_codec="aac")
    result = evaluate(likely, file)
    assert not result.matched


# ── Always transcode ────────────────────────────────────────────


def test_always_matches_4k_hevc(always) -> None:
    file = _file(video_codec="hevc", width=3840, height=2160, audio_codec="aac")
    result = evaluate(always, file)
    assert result.matched
    assert result.severity == "high"
    assert "always-transcode" in result.add_tags


def test_always_matches_dts(always) -> None:
    file = _file(video_codec="h264", height=1080, audio_codec="dts")
    result = evaluate(always, file)
    assert result.matched


def test_always_does_not_match_1080p_hevc_aac(always) -> None:
    """1080p HEVC is "likely transcode", not "always transcode" —
    different severity, different rule, must not overlap."""
    file = _file(video_codec="hevc", width=1920, height=1080, audio_codec="aac")
    result = evaluate(always, file)
    assert not result.matched


# ── Unplayable ──────────────────────────────────────────────────


def test_unplayable_matches_mpeg2_in_mov(unplayable) -> None:
    file = _file(video_codec="mpeg2video", container="mov", audio_codec="aac")
    result = evaluate(unplayable, file)
    assert result.matched
    assert result.severity == "crit"
    assert "unplayable" in result.add_tags


def test_unplayable_matches_bink_video(unplayable) -> None:
    file = _file(video_codec="bink", container="avi", audio_codec="aac")
    result = evaluate(unplayable, file)
    assert result.matched


def test_unplayable_does_not_match_mpeg2_in_proper_container(
    unplayable,
) -> None:
    """MPEG-2 in an MPEG-TS or MPEG-PS container is fine — it's
    the MP4 muxer specifically that's the problem."""
    file = _file(video_codec="mpeg2video", container="mpegts", audio_codec="ac3")
    result = evaluate(unplayable, file)
    assert not result.matched
