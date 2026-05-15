"""ffprobe parser tests.

The parser is deliberately split out from the subprocess wrapper so every
edge case can be exercised against synthetic payloads.
"""

from __future__ import annotations

from app.services.media.ffprobe import parse_ffprobe


def _payload(**overrides: object) -> dict:
    base = {
        "format": {
            "format_name": "matroska,webm",
            "duration": "1234.5",
            "bit_rate": "5000000",
        },
        "streams": [],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_parse_minimal() -> None:
    result = parse_ffprobe(_payload())
    assert result.ok is True
    assert result.container == "matroska"
    assert result.duration_seconds == 1234.5
    assert result.bitrate_kbps == 5000  # 5_000_000 / 1000 rounded
    assert result.video_codec is None
    assert result.audio_codec is None
    assert result.has_subtitles is False


def test_parse_video_audio_subtitle() -> None:
    payload = _payload(
        streams=[
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24000/1001",
            },
            {
                "codec_type": "audio",
                "codec_name": "eac3",
                "tags": {"language": "eng"},
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "tags": {"language": "JPN"},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng"},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "fre"},
            },
        ]
    )
    result = parse_ffprobe(payload)
    assert result.video_codec == "hevc"
    assert result.width == 3840 and result.height == 2160
    assert result.framerate == round(24000 / 1001, 4)
    assert result.audio_codec == "eac3"
    assert result.subtitle_codec == "subrip"
    assert result.has_subtitles is True
    assert result.audio_languages == ["eng", "jpn"]
    assert result.subtitle_languages == ["eng", "fre"]


def test_unknown_language_omitted() -> None:
    payload = _payload(
        streams=[
            {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "und"}}
        ]
    )
    assert parse_ffprobe(payload).audio_languages == []


def test_zero_framerate_handled() -> None:
    payload = _payload(
        streams=[
            {
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "0/0",
            }
        ]
    )
    assert parse_ffprobe(payload).framerate is None


def test_string_framerate_handled() -> None:
    payload = _payload(
        streams=[
            {"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "29.97"}
        ]
    )
    assert parse_ffprobe(payload).framerate == 29.97


def test_missing_format_fields_safe() -> None:
    payload = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
    result = parse_ffprobe(payload)
    assert result.ok is True
    assert result.container is None
    assert result.duration_seconds is None
    assert result.bitrate_kbps is None
    assert result.video_codec == "h264"


def test_raw_payload_preserved() -> None:
    payload = _payload(streams=[{"codec_type": "video", "codec_name": "h264"}])
    result = parse_ffprobe(payload)
    assert result.raw is payload
