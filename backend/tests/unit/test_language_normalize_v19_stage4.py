"""Language normalization (v1.9 Stage 4.1).

Pins:
  1. ``normalize_language`` folds every common form to its ISO 639-1 code.
  2. Subtags (``en-US`` / ``zh-TW``) collapse to the primary tag.
  3. Unknown inputs fall through to the lower-cased trimmed string
     (so we don't silently lose information for a muxer using a
     spelling we haven't catalogued).
  4. ``normalize_languages`` preserves order, drops duplicates that
     collapse to the same canonical code, skips non-strings.
  5. The rules evaluator applies the same normalization to BOTH
     sides when comparing language fields, so a rule with ``"en"``
     matches a file tagged ``"eng"``, ``"English"``, ``"en-US"``.
"""

from __future__ import annotations

import pytest

from app.rules.evaluator import EvaluationInput, _eval_condition
from app.rules.language_normalize import (
    canonical_languages,
    normalize_language,
    normalize_languages,
)
from app.rules.schema import Condition


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("en", "en"),
        ("eng", "en"),
        ("English", "en"),
        ("ENGLISH", "en"),
        ("en-US", "en"),
        ("en_GB", "en"),
        ("en-au", "en"),
        # Spanish
        ("es", "es"),
        ("spa", "es"),
        ("Spanish", "es"),
        ("Español", "es"),
        ("es-MX", "es"),
        # French
        ("fr", "fr"),
        ("fre", "fr"),
        ("fra", "fr"),
        ("French", "fr"),
        ("Français", "fr"),
        # German aliases include the obsolete 3-letter code "ger".
        ("ger", "de"),
        ("deu", "de"),
        ("Deutsch", "de"),
        # Chinese family — all fold to "zh".
        ("zh-CN", "zh"),
        ("zh-Hant", "zh"),
        ("Mandarin", "zh"),
        ("Cantonese", "zh"),
        # Hebrew, legacy code "iw" should also map.
        ("iw", "he"),
        ("hebrew", "he"),
    ],
)
def test_normalize_language_known_aliases(raw: str, expected: str) -> None:
    assert normalize_language(raw) == expected


def test_normalize_language_handles_none_and_empty() -> None:
    assert normalize_language(None) is None
    assert normalize_language("") is None
    assert normalize_language("   ") is None


def test_normalize_language_unknown_falls_through_lowered() -> None:
    """Unknown input is preserved as a lower-cased string so the
    rule can still match identical-by-spelling values, just not
    benefit from canonical folding."""
    assert normalize_language("xyz123") == "xyz123"
    # A subtag we don't know — the primary tag also unknown, so
    # the full lower-cased string is returned.
    assert normalize_language("xyz-ABC") == "xyz-abc"


def test_normalize_languages_preserves_order_and_dedupes() -> None:
    """``["en", "eng", "English"]`` collapses to ``["en"]``;
    ``["en", "es", "en"]`` collapses to ``["en", "es"]``."""
    assert normalize_languages(["en", "eng", "English"]) == ["en"]
    assert normalize_languages(["en", "es", "en"]) == ["en", "es"]
    assert normalize_languages(None) == []
    assert normalize_languages([]) == []
    # Non-strings are skipped without exploding the list.
    assert normalize_languages(["en", 123, None, "fr"]) == ["en", "fr"]  # type: ignore[list-item]


def test_canonical_languages_returns_sorted_codes() -> None:
    langs = canonical_languages()
    assert "en" in langs
    assert "es" in langs
    assert "zh" in langs
    # Sorted alphabetically.
    assert langs == sorted(langs)


# ── Evaluator integration ──────────────────────────────────────


def _file(**overrides) -> EvaluationInput:
    """Build an EvaluationInput with sensible defaults for the
    five required identity fields. Tests override only the
    language fields (or whichever field they're asserting on)."""
    base = {
        "media_file_id": "f-1",
        "path": "/lib/x.mkv",
        "filename": "x.mkv",
        "extension": "mkv",
        "category": "media",
    }
    base.update(overrides)
    return EvaluationInput(**base)


def test_evaluator_audio_languages_contains_eng_matches_en() -> None:
    """The classic case: file tagged ``eng`` should match a rule
    written as ``audio_languages contains "en"``."""
    file = _file(audio_languages=["eng", "fre"])
    cond = Condition(field="audio_languages", op="contains", value="en")
    assert _eval_condition(cond, file) is True


def test_evaluator_audio_languages_contains_english_matches_en() -> None:
    """Mixed-case full-word form: ``["English"]`` matches ``"en"``."""
    file = _file(audio_languages=["English"])
    cond = Condition(field="audio_languages", op="contains", value="en")
    assert _eval_condition(cond, file) is True


def test_evaluator_audio_languages_contains_en_us_matches_en() -> None:
    """Subtag form: ``["en-US"]`` matches ``"en"``."""
    file = _file(audio_languages=["en-US"])
    cond = Condition(field="audio_languages", op="contains", value="en")
    assert _eval_condition(cond, file) is True


def test_evaluator_audio_languages_any_of_normalizes_both_sides() -> None:
    """Rule: ``audio_languages any_of ["English", "Spanish"]``.
    File: ``["eng"]``. Both sides should normalize and match."""
    file = _file(audio_languages=["eng"])
    cond = Condition(
        field="audio_languages",
        op="any_of",
        value=["English", "Spanish"],
    )
    assert _eval_condition(cond, file) is True


def test_evaluator_audio_languages_none_of_normalizes() -> None:
    """``none_of ["en"]`` matches a file with NO English-coded
    languages, whatever spelling the muxer used."""
    file_no_en = _file(audio_languages=["fre", "spa"])
    cond_no_en = Condition(field="audio_languages", op="none_of", value=["en"])
    assert _eval_condition(cond_no_en, file_no_en) is True

    file_has_en = _file(audio_languages=["English"])
    assert _eval_condition(cond_no_en, file_has_en) is False


def test_evaluator_subtitle_languages_also_normalized() -> None:
    """Same treatment for subtitle_languages."""
    file = _file(subtitle_languages=["English", "Japanese"])
    cond = Condition(field="subtitle_languages", op="contains", value="jpn")
    assert _eval_condition(cond, file) is True


def test_evaluator_non_language_fields_untouched() -> None:
    """``tags`` and other string fields stay case-sensitive and
    exact — only language fields normalize."""
    file = _file(tags=["English"])
    cond = Condition(field="tags", op="contains", value="english")
    # Strict equality — would have folded if we applied
    # normalization here.
    assert _eval_condition(cond, file) is False
