"""Bazarr ``/api/system/languages`` response shape normalisation.

Bazarr <1.4 returns ``{"data": [{...}, ...]}``; Bazarr 1.4+ returns a
bare array. The integration upstream-tags endpoint used to call
``response.json().get("data")`` unconditionally and 500'd against the
new shape with ``AttributeError: 'list' object has no attribute 'get'``.
"""

from __future__ import annotations

from app.api.v1.integrations import _bazarr_language_tags


def test_legacy_wrapped_payload() -> None:
    payload = {
        "data": [
            {"code2": "en", "code3": "eng", "name": "English"},
            {"code2": "es", "code3": "spa", "name": "Spanish"},
        ]
    }
    assert _bazarr_language_tags(payload) == [
        "missing-subs:en",
        "missing-subs:es",
    ]


def test_bare_array_payload() -> None:
    payload = [
        {"code2": "en", "code3": "eng", "name": "English"},
        {"code2": "DE", "code3": "deu", "name": "German"},
    ]
    assert _bazarr_language_tags(payload) == [
        "missing-subs:en",
        "missing-subs:de",
    ]


def test_falls_back_to_code3() -> None:
    assert _bazarr_language_tags(
        [{"code2": None, "code3": "Eng"}]
    ) == ["missing-subs:eng"]


def test_skips_entries_without_codes() -> None:
    assert _bazarr_language_tags(
        [
            {"name": "Unknown"},
            {"code2": "en"},
            "not-a-dict",
        ]
    ) == ["missing-subs:en"]


def test_unexpected_payload_yields_empty() -> None:
    assert _bazarr_language_tags(None) == []
    assert _bazarr_language_tags("oops") == []
    assert _bazarr_language_tags(42) == []
