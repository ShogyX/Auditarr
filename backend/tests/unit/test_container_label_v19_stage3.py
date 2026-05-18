"""Unit tests for ``app.utils.container_label`` (v1.9 Stage 3.4)."""

from __future__ import annotations

import pytest

from app.utils.container_label import container_label


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Matroska family
        ("matroska", "MKV"),
        ("matroska,webm", "MKV"),
        ("MKV", "MKV"),
        # WebM is distinct (different label even though same demuxer).
        ("webm", "WEBM"),
        # MP4 / QuickTime family — the scanner often hands us the
        # post-split ``mov`` value, but the raw comma string should
        # also resolve.
        ("mov", "MP4"),
        ("mp4", "MP4"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "MP4"),
        ("m4a", "MP4"),
        # MPEG TS / PS
        ("mpegts", "TS"),
        ("ts", "TS"),
        # AVI / FLV / OGG
        ("avi", "AVI"),
        ("flv", "FLV"),
        ("ogg", "OGG"),
    ],
)
def test_container_label_known_values(raw: str, expected: str) -> None:
    assert container_label(raw) == expected


def test_container_label_handles_none_and_empty() -> None:
    assert container_label(None) is None
    assert container_label("") is None
    assert container_label("   ") is None


def test_container_label_case_insensitive_input() -> None:
    assert container_label("MaTrOsKa") == "MKV"
    assert container_label("MOV") == "MP4"


def test_container_label_unknown_input_returns_uppercased() -> None:
    """A future ffprobe version reporting a container we haven't
    catalogued still gives the operator a readable label rather
    than ``None``."""
    assert container_label("brand_new_demuxer") == "BRAND_NEW_DEMUXER"


def test_container_label_first_token_fallback() -> None:
    """If the raw comma-string isn't in the table verbatim but
    its first token is, we fall back on the first token."""
    # ``matroska,future_tag`` isn't in the table but its first
    # token IS — operator sees MKV instead of the upper-case raw.
    assert container_label("matroska,future_tag") == "MKV"
