"""Classifier unit tests."""

from __future__ import annotations

import pytest

from app.services.media.classifier import classify, should_probe


@pytest.mark.parametrize(
    "name,expected_category",
    [
        ("show.S01E01.mkv", "media"),
        ("Movie (2019).mp4", "media"),
        ("track.mp3", "media"),
        ("audiobook.m4b", "media"),
        ("subs.eng.srt", "subtitle"),
        ("forced.ass", "subtitle"),
        ("poster.jpg", "image"),
        ("fanart.png", "image"),
        ("movie.nfo", "metadata"),
        ("season.json", "metadata"),
        (".DS_Store", "junk"),
        ("Thumbs.db", "junk"),
        ("desktop.ini", "junk"),
        ("._sidecar", "junk"),
        ("partial.part", "junk"),
        ("no_extension_file", "unknown"),
        ("README", "unknown"),
        ("script.sh", "unknown"),
    ],
)
def test_classify_by_extension(name: str, expected_category: str) -> None:
    assert classify(name).category == expected_category


def test_classify_returns_video_flag() -> None:
    assert classify("a.mkv").is_video is True
    assert classify("a.mp3").is_video is False
    assert classify("a.mp3").is_audio is True


def test_classify_falls_back_to_ffprobe_signal() -> None:
    # Unknown extension, but ffprobe says it has a video stream.
    result = classify("strange.bin", has_video_stream=True)
    assert result.category == "media"
    assert result.is_video is True


def test_should_probe_only_for_media() -> None:
    assert should_probe("show.mkv") is True
    assert should_probe("track.flac") is True
    assert should_probe("subs.srt") is False
    assert should_probe("poster.jpg") is False
    assert should_probe(".DS_Store") is False
