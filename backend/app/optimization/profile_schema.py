"""Optimization profile schema.

A profile's ``settings`` dict is validated by these Pydantic models on
every save and on every load (the worker re-validates so corrupt rows
can't silently misconfigure ffmpeg).

The shape is deliberately small. The fields below are what the worker
knows how to translate into ffmpeg argv; extending the vocabulary
requires a code change on both sides, by design. Free-form ``extra_args``
is the escape hatch for anything we haven't modeled — operators get
power, but it's clearly marked as escape-hatch territory and not
validated for safety.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SUPPORTED_VIDEO_CODECS = (
    "libx265",  # HEVC software
    "libx264",  # H.264 software
    "libaom-av1",  # AV1 software
    "copy",  # passthrough (re-mux only)
)

SUPPORTED_AUDIO_CODECS = (
    "libopus",
    "aac",
    "libmp3lame",
    "copy",
)

SUPPORTED_CONTAINERS = (
    "mkv",
    "mp4",
    "webm",
)


class VideoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codec: str = Field(default="libx265")
    # x265/x264 CRF; ignored for ``copy``. Lower = higher quality.
    crf: int | None = Field(default=22, ge=0, le=51)
    # ffmpeg ``-preset``: ultrafast/superfast/veryfast/faster/fast/medium/slow/slower/veryslow.
    preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] | None = "medium"
    # Optional max bitrate cap, kbps. None = unbounded.
    max_bitrate_kbps: int | None = Field(default=None, ge=64, le=200_000)
    # Optional scale target. ``None`` = original resolution.
    # Format: short side in pixels (e.g. 1080 -> 1920x1080 keeping AR).
    scale_height: int | None = Field(default=None, ge=144, le=4320)

    @field_validator("codec")
    @classmethod
    def _validate_codec(cls, v: str) -> str:
        if v not in SUPPORTED_VIDEO_CODECS:
            raise ValueError(
                f"Unsupported video codec {v!r}. "
                f"Supported: {SUPPORTED_VIDEO_CODECS}"
            )
        return v


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codec: str = Field(default="copy")
    bitrate_kbps: int | None = Field(default=128, ge=24, le=2048)
    channels: int | None = Field(default=None, ge=1, le=8)

    @field_validator("codec")
    @classmethod
    def _validate_codec(cls, v: str) -> str:
        if v not in SUPPORTED_AUDIO_CODECS:
            raise ValueError(
                f"Unsupported audio codec {v!r}. "
                f"Supported: {SUPPORTED_AUDIO_CODECS}"
            )
        return v


class SubtitleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``copy`` keeps all subtitle streams as-is; ``drop`` removes them.
    handling: Literal["copy", "drop"] = "copy"


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: str = Field(default="mkv")
    # Replace the input with the output (with backup) when finished.
    replace_input: bool = True
    # Keep a ``.bak`` alongside the original after a successful swap.
    keep_backup: bool = True

    @field_validator("container")
    @classmethod
    def _validate_container(cls, v: str) -> str:
        if v not in SUPPORTED_CONTAINERS:
            raise ValueError(
                f"Unsupported container {v!r}. "
                f"Supported: {SUPPORTED_CONTAINERS}"
            )
        return v


class ProfileDefinition(BaseModel):
    """The body of an OptimizationProfile.settings JSON column."""

    model_config = ConfigDict(extra="forbid")

    video: VideoSettings = Field(default_factory=VideoSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    # Free-form extra ffmpeg arguments inserted just before the output
    # path. Use sparingly — there's no validation here.
    extra_args: list[str] = Field(default_factory=list)
    # Refuse to run if the input's bitrate is already below this value.
    # Prevents shrinking already-small files into nothing useful.
    skip_if_bitrate_below_kbps: int | None = Field(
        default=None, ge=0, le=200_000
    )
