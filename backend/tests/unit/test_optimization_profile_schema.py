"""Optimization profile schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.optimization.profile_schema import ProfileDefinition


def _ok(doc: dict) -> ProfileDefinition:
    return ProfileDefinition.model_validate(doc)


def _bad(doc: dict) -> ValidationError:
    with pytest.raises(ValidationError) as exc:
        ProfileDefinition.model_validate(doc)
    return exc.value


def test_defaults_validate() -> None:
    profile = _ok({})
    assert profile.video.codec == "libx265"
    assert profile.audio.codec == "copy"
    assert profile.subtitles.handling == "copy"
    assert profile.output.container == "mkv"
    assert profile.output.replace_input is True
    assert profile.output.keep_backup is True


def test_full_profile_validates() -> None:
    profile = _ok(
        {
            "video": {
                "codec": "libx265",
                "crf": 23,
                "preset": "slow",
                "max_bitrate_kbps": 8000,
                "scale_height": 1080,
            },
            "audio": {
                "codec": "libopus",
                "bitrate_kbps": 192,
                "channels": 2,
            },
            "subtitles": {"handling": "drop"},
            "output": {
                "container": "mp4",
                "replace_input": False,
                "keep_backup": False,
            },
            "extra_args": ["-tag:v", "hvc1"],
            "skip_if_bitrate_below_kbps": 5000,
        }
    )
    assert profile.video.scale_height == 1080
    assert profile.subtitles.handling == "drop"
    assert profile.extra_args == ["-tag:v", "hvc1"]


def test_unsupported_video_codec_rejected() -> None:
    err = _bad({"video": {"codec": "rot13"}})
    assert "Unsupported video codec" in str(err)


def test_unsupported_audio_codec_rejected() -> None:
    err = _bad({"audio": {"codec": "8track"}})
    assert "Unsupported audio codec" in str(err)


def test_unsupported_container_rejected() -> None:
    err = _bad({"output": {"container": "tar"}})
    assert "Unsupported container" in str(err)


def test_crf_out_of_range_rejected() -> None:
    err = _bad({"video": {"crf": 100}})
    assert "crf" in str(err).lower()


def test_scale_height_too_small_rejected() -> None:
    err = _bad({"video": {"scale_height": 100}})
    assert "scale_height" in str(err).lower()


def test_extra_keys_rejected_on_video() -> None:
    err = _bad({"video": {"frobnicate": True}})
    assert "frobnicate" in str(err).lower()


def test_subtitle_handling_validates() -> None:
    _ok({"subtitles": {"handling": "drop"}})
    err = _bad({"subtitles": {"handling": "translate"}})
    assert "subtitles" in str(err).lower() or "handling" in str(err).lower()


def test_extra_args_must_be_list() -> None:
    err = _bad({"extra_args": "-y"})
    assert "list" in str(err).lower() or "iterable" in str(err).lower()
