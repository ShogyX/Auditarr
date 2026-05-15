"""ffmpeg argv builder tests.

We don't spawn ffmpeg here — just verify the argv shape for various
profile combinations. The runner's actual subprocess + progress-pipe
behaviour is tested separately in test_optimization_worker.py with a
fake ffmpeg binary.
"""

from __future__ import annotations

from pathlib import Path

from app.optimization.ffmpeg_runner import (
    TranscodeRequest,
    build_ffmpeg_argv,
)
from app.optimization.profile_schema import ProfileDefinition


def _argv(settings: dict) -> list[str]:
    profile = ProfileDefinition.model_validate(settings)
    request = TranscodeRequest(
        input_path=Path("/in.mkv"),
        output_path=Path("/out.mkv"),
        profile=profile,
        input_duration_seconds=60.0,
    )
    return build_ffmpeg_argv(request, ffmpeg_bin="ffmpeg")


def test_baseline_argv_shape() -> None:
    argv = _argv({})
    # ffmpeg, global flags, -i input, mapping, video, audio, output
    assert argv[0] == "ffmpeg"
    assert "-y" in argv
    assert "-hide_banner" in argv
    assert "-progress" in argv
    assert "pipe:1" in argv
    assert "-i" in argv
    # Input + output paths preserved
    assert "/in.mkv" in argv
    assert "/out.mkv" == argv[-1]
    # Default video + audio + subtitle settings.
    assert "-c:v" in argv
    i = argv.index("-c:v")
    assert argv[i + 1] == "libx265"
    j = argv.index("-c:a")
    assert argv[j + 1] == "copy"


def test_copy_video_skips_encode_flags() -> None:
    argv = _argv({"video": {"codec": "copy"}})
    # No CRF/preset/maxrate when the video stream is copied.
    assert "-crf" not in argv
    assert "-preset" not in argv
    assert "-maxrate" not in argv
    assert "-vf" not in argv


def test_max_bitrate_adds_maxrate_and_bufsize() -> None:
    argv = _argv({"video": {"max_bitrate_kbps": 8000}})
    assert "-maxrate" in argv
    i = argv.index("-maxrate")
    assert argv[i + 1] == "8000k"
    # bufsize defaults to 2× maxrate.
    j = argv.index("-bufsize")
    assert argv[j + 1] == "16000k"


def test_scale_height_adds_vf_filter() -> None:
    argv = _argv({"video": {"scale_height": 720}})
    assert "-vf" in argv
    i = argv.index("-vf")
    assert "720" in argv[i + 1]
    # ``-1`` semantics: width is computed from aspect ratio + trunc to even.
    assert "trunc" in argv[i + 1]


def test_drop_subtitles_omits_stream_map_and_codec() -> None:
    argv = _argv({"subtitles": {"handling": "drop"}})
    # ``-map 0:s?`` must NOT be in argv when subtitles are dropped.
    map_args = [
        argv[i + 1]
        for i, a in enumerate(argv)
        if a == "-map" and i + 1 < len(argv)
    ]
    assert "0:s?" not in map_args
    # ``-c:s copy`` also omitted.
    assert not any(
        argv[i] == "-c:s" for i in range(len(argv) - 1)
    )


def test_audio_transcode_includes_bitrate_and_channels() -> None:
    argv = _argv(
        {"audio": {"codec": "libopus", "bitrate_kbps": 128, "channels": 2}}
    )
    i = argv.index("-c:a")
    assert argv[i + 1] == "libopus"
    j = argv.index("-b:a")
    assert argv[j + 1] == "128k"
    k = argv.index("-ac")
    assert argv[k + 1] == "2"


def test_audio_copy_omits_bitrate_and_channels() -> None:
    argv = _argv({"audio": {"codec": "copy", "bitrate_kbps": 128}})
    # ``-c:a copy`` is present but ``-b:a`` is not (would be invalid).
    assert "-b:a" not in argv
    assert "-ac" not in argv


def test_extra_args_appended_before_output() -> None:
    argv = _argv({"extra_args": ["-tag:v", "hvc1"]})
    # Extra args are right before the output path.
    out_index = argv.index("/out.mkv")
    assert argv[out_index - 2 : out_index] == ["-tag:v", "hvc1"]


def test_argv_uses_provided_ffmpeg_bin() -> None:
    profile = ProfileDefinition.model_validate({})
    argv = build_ffmpeg_argv(
        TranscodeRequest(
            input_path=Path("/in.mkv"),
            output_path=Path("/out.mkv"),
            profile=profile,
        ),
        ffmpeg_bin="/opt/bin/ffmpeg",
    )
    assert argv[0] == "/opt/bin/ffmpeg"
