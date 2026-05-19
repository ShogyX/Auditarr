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

Stage 07 (v1.7) added four cross-cutting fields:

  * ``transcode_scope`` — whether to re-encode video, audio, or both.
    When ``video_only``, the worker forces ``-c:a copy``; when
    ``audio_only``, ``-c:v copy``.
  * ``tag_scope`` — list of tag names a file must carry to be
    eligible. The rules ``queue_optimization`` action rejects items
    whose file doesn't carry every listed tag.
  * ``routing_target`` — which runner picks up jobs from this
    profile. ``in_process`` runs ffmpeg locally; ``plex|jellyfin|
    tdarr`` hand off to the integration provider's
    ``submit_transcode_job`` (Stage 08 wires the provider side).
  * ``schedule_window`` — optional time window during which the
    worker is allowed to pick up items for this profile. Outside
    the window, items are skipped (``optimization.skipped_window``
    emitted on the bus) and re-evaluated on the next tick.

Per addendum B.5: ``schedule_window.timezone`` defaults to the
server's local zone (resolved via ``TZ`` env var or
``time.tzname[0]``), falling back to UTC when neither is
available.
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

# Stage 07 (v1.7): transcode scope literals. Drives which stream
# (video/audio/both) the worker re-encodes. When the scope excludes
# a stream, the worker hard-overrides that stream's codec to
# ``copy`` regardless of what the profile's video/audio block
# says — the scope is the source of truth.
TRANSCODE_SCOPE_VALUES = ("video_and_audio", "video_only", "audio_only")

# Stage 07 (v1.7): routing target literals. The four values map to
# either the in-process ffmpeg runner or one of three integration
# providers that own remote transcode execution (Stage 08 wires
# the provider side).
ROUTING_TARGET_VALUES = ("in_process", "plex", "jellyfin", "tdarr")


def _default_schedule_timezone() -> str:
    """Resolve the server's local timezone for ``schedule_window``.

    Per addendum B.5: prefer the ``TZ`` env var; fall back to
    ``time.tzname[0]`` (which on most Linux systems returns
    something like ``"UTC"`` or ``"EST"``); fall back to ``"UTC"``
    when neither resolves to something usable.

    The string is stored as-is. The worker uses ``zoneinfo.ZoneInfo``
    to resolve it at evaluation time; if the stored name is bogus,
    the worker logs a warning and treats the window as UTC for
    that tick (better than refusing to run at all).
    """
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        names = time.tzname
        if names and names[0]:
            return names[0]
    except Exception:  # noqa: BLE001
        pass
    return "UTC"


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


class ScheduleWindow(BaseModel):
    """Stage 07 (v1.7) — time-of-day window for picking up jobs.

    Hours are 0..23 (24-hour clock). ``start_hour < end_hour``
    means "within a single day" (e.g. 22..2 wraps midnight).
    ``start_hour == end_hour`` is interpreted as "always allowed"
    so operators can disable the window without removing it.

    ``timezone`` is an IANA-ish zone name (e.g. ``"America/Denver"``).
    The worker resolves it via ``zoneinfo.ZoneInfo``; an unrecognised
    name falls back to UTC for that tick with a logged warning.
    """

    model_config = ConfigDict(extra="forbid")

    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)
    timezone: str = Field(default_factory=_default_schedule_timezone, min_length=1)


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
    # ── Stage 07 (v1.7) — cross-cutting profile fields ─────────────
    transcode_scope: Literal[
        "video_and_audio", "video_only", "audio_only"
    ] = Field(default="video_and_audio")
    # Files must carry EVERY listed tag to be eligible for the
    # profile. Empty list = no tag requirement. The rules
    # ``queue_optimization`` action enforces this at queue time
    # (rejected items get a clear error message).
    tag_scope: list[str] = Field(default_factory=list)
    routing_target: Literal[
        "in_process", "plex", "jellyfin", "tdarr"
    ] = Field(default="in_process")
    schedule_window: ScheduleWindow | None = Field(default=None)
    # Stage 08 (v1.7) — free-form per-provider hints. The profile
    # editor populates this dict when routing_target != in_process
    # (e.g. the operator-picked Tdarr plugin id, or a Plex
    # ratingKey override). The worker passes it through to the
    # integration provider's ``submit_transcode_job`` via the
    # job spec's ``metadata`` field. Empty dict = no hints.
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tag_scope")
    @classmethod
    def _validate_tag_scope(cls, v: list[str]) -> list[str]:
        # Tag names match MediaTag.name constraints — non-empty,
        # bounded length. Dedup at validation so the worker doesn't
        # double-check the same tag.
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in v:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError(
                    f"tag_scope entries must be non-empty strings; got {tag!r}"
                )
            if len(tag) > 64:
                raise ValueError(
                    f"tag_scope entry {tag!r} exceeds 64 characters"
                )
            if tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

    @model_validator(mode="after")
    def _validate_scope_codec_compat(self) -> "ProfileDefinition":
        """Cross-field guard: a scope that excludes a stream is
        nonsensical when that stream's codec is also ``copy`` AND
        no useful work is being done.

        More importantly, ``audio_only`` is meaningless unless the
        audio codec is something other than ``copy`` — there's
        literally no transcode work. Mirror for ``video_only``
        with video codec.

        The plan §413 calls this rule out explicitly: "invalid
        combinations rejected (e.g. ``audio_only`` with no audio
        codec)". We use codec=``copy`` as the proxy for "no audio
        codec configured" since ``copy`` means passthrough.
        """
        if self.transcode_scope == "audio_only" and self.audio.codec == "copy":
            raise ValueError(
                "transcode_scope='audio_only' requires the audio "
                "codec to be a re-encoder (not 'copy'); otherwise "
                "the profile does no work."
            )
        if self.transcode_scope == "video_only" and self.video.codec == "copy":
            raise ValueError(
                "transcode_scope='video_only' requires the video "
                "codec to be a re-encoder (not 'copy'); otherwise "
                "the profile does no work."
            )
        return self

    @model_validator(mode="after")
    def _validate_routing_target(self) -> "ProfileDefinition":
        """Reject routing targets that look configurable but always
        fail at worker time.

        * ``jellyfin`` — the Literal advertises it but
          ``JellyfinProvider.submit_transcode_job`` always returns
          ``status="rejected"`` because Jellyfin's API has no
          job-submission endpoint. Catching it here gives the
          operator a 400 at save time instead of a silent failure on
          the next queue tick.
        * ``tdarr`` — the Tdarr provider requires
          ``provider_metadata.provider_profile_id`` (the Tdarr plugin
          / flow id). Without it, every routed item fails with the
          same "Tdarr requires a provider profile id" message until
          the profile is edited.
        """
        if self.routing_target == "jellyfin":
            raise ValueError(
                "routing_target='jellyfin' is not supported: Jellyfin's "
                "API does not expose a job-submission endpoint. Use "
                "'tdarr' or 'in_process' instead."
            )
        if self.routing_target == "tdarr":
            profile_id = self.provider_metadata.get("provider_profile_id")
            if not isinstance(profile_id, str) or not profile_id.strip():
                raise ValueError(
                    "routing_target='tdarr' requires "
                    "provider_metadata.provider_profile_id to be set to "
                    "the Tdarr plugin (transcode flow) id. Pick one from "
                    "the integration's plugin list in the profile editor."
                )
        return self


def schedule_window_is_open(
    window: ScheduleWindow | None,
    now: _dt.datetime | None = None,
) -> bool:
    """Stage 07 (v1.7) — evaluate whether a schedule window is
    currently open.

    Pure function used by the worker (and the test suite) to gate
    job pickup. Returns True when:

      * the window is ``None`` (no schedule = always open).
      * ``start_hour == end_hour`` (degenerate "always-open" form).
      * the current hour in the window's timezone falls inside
        ``[start_hour, end_hour)`` for same-day windows or inside
        ``[start_hour, 24) | [0, end_hour)`` for windows that wrap
        midnight.

    ``now`` defaults to ``utcnow()``; the function does its own
    tz conversion. Passing a deterministic ``now`` makes the
    function trivial to test under ``freezegun`` or with a fixed
    datetime.

    An unresolvable ``window.timezone`` falls back to UTC and
    logs a warning at the worker layer (kept silent here so the
    function stays pure).
    """
    if window is None:
        return True
    if window.start_hour == window.end_hour:
        return True

    if now is None:
        now = _dt.datetime.now(tz=_dt.UTC)
    elif now.tzinfo is None:
        # Naive datetimes are interpreted as UTC.
        now = now.replace(tzinfo=_dt.UTC)

    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            tz = ZoneInfo(window.timezone)
        except ZoneInfoNotFoundError:
            tz = _dt.UTC
    except ImportError:
        # zoneinfo is stdlib on 3.9+; if unavailable, UTC fallback.
        tz = _dt.UTC

    local = now.astimezone(tz)
    h = local.hour

    if window.start_hour < window.end_hour:
        # Same-day window. e.g. 09..17 means 09:00..16:59.
        return window.start_hour <= h < window.end_hour
    # Wraps midnight. e.g. 22..2 means 22:00..01:59.
    return h >= window.start_hour or h < window.end_hour
