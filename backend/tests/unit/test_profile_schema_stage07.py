"""Stage 07 (v1.7) — ProfileDefinition extensions.

Plan §413:
    Each new field validates; invalid combinations rejected (e.g.
    ``audio_only`` with no audio codec).

This file pins the four new ProfileDefinition fields + the
``schedule_window_is_open`` helper + the per-stream
cross-validation logic.
"""

from __future__ import annotations

import datetime as _dt

import pytest
import pydantic

from app.optimization.profile_schema import (
    ROUTING_TARGET_VALUES,
    TRANSCODE_SCOPE_VALUES,
    ProfileDefinition,
    ScheduleWindow,
    _default_schedule_timezone,
    schedule_window_is_open,
)


# ── Defaults ───────────────────────────────────────────────────


def test_profile_default_transcode_scope() -> None:
    """No explicit field → default to ``video_and_audio``."""
    p = ProfileDefinition()
    assert p.transcode_scope == "video_and_audio"


def test_profile_default_routing_target() -> None:
    """No explicit field → in-process ffmpeg runner."""
    p = ProfileDefinition()
    assert p.routing_target == "in_process"


def test_profile_default_tag_scope_empty() -> None:
    p = ProfileDefinition()
    assert p.tag_scope == []


def test_profile_default_schedule_window_none() -> None:
    """No window = always-open (24/7)."""
    p = ProfileDefinition()
    assert p.schedule_window is None


# ── transcode_scope ────────────────────────────────────────────


def test_transcode_scope_values_match_literal() -> None:
    """The TRANSCODE_SCOPE_VALUES export matches the Literal."""
    assert TRANSCODE_SCOPE_VALUES == (
        "video_and_audio",
        "video_only",
        "audio_only",
    )


def test_transcode_scope_invalid_value_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate({"transcode_scope": "subtitles_only"})


def test_audio_only_with_copy_audio_rejected() -> None:
    """Plan §413: invalid combinations rejected. ``audio_only``
    with audio.codec=copy means no transcode work happens."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        ProfileDefinition.model_validate(
            {
                "transcode_scope": "audio_only",
                "audio": {"codec": "copy"},
            }
        )
    assert "audio_only" in str(exc_info.value)


def test_audio_only_with_real_audio_codec_accepted() -> None:
    """audio_only is valid when the audio codec is a re-encoder."""
    p = ProfileDefinition.model_validate(
        {
            "transcode_scope": "audio_only",
            "audio": {"codec": "libopus"},
        }
    )
    assert p.transcode_scope == "audio_only"
    assert p.audio.codec == "libopus"


def test_video_only_with_copy_video_rejected() -> None:
    """Mirror guard: ``video_only`` with video.codec=copy is
    nonsensical."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        ProfileDefinition.model_validate(
            {
                "transcode_scope": "video_only",
                "video": {"codec": "copy"},
            }
        )
    assert "video_only" in str(exc_info.value)


def test_video_only_with_real_video_codec_accepted() -> None:
    p = ProfileDefinition.model_validate(
        {
            "transcode_scope": "video_only",
            "video": {"codec": "libx264"},
        }
    )
    assert p.transcode_scope == "video_only"


# ── tag_scope ──────────────────────────────────────────────────


def test_tag_scope_accepts_list_of_strings() -> None:
    p = ProfileDefinition.model_validate(
        {"tag_scope": ["plex-incompatible-video", "4k"]}
    )
    assert p.tag_scope == ["plex-incompatible-video", "4k"]


def test_tag_scope_deduplicates() -> None:
    """Repeated entries collapse to one. The worker would just
    double-check otherwise."""
    p = ProfileDefinition.model_validate(
        {"tag_scope": ["a", "b", "a", "c", "b"]}
    )
    assert p.tag_scope == ["a", "b", "c"]


def test_tag_scope_rejects_empty_string() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate({"tag_scope": ["valid", ""]})


def test_tag_scope_rejects_whitespace_only() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate({"tag_scope": ["valid", "   "]})


def test_tag_scope_rejects_overlong_tag() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate(
            {"tag_scope": ["x" * 65]}  # 65 chars exceeds the 64 cap
        )


def test_tag_scope_rejects_non_string_entries() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate({"tag_scope": ["valid", 123]})


# ── routing_target ─────────────────────────────────────────────


def test_routing_target_values_match_literal() -> None:
    assert ROUTING_TARGET_VALUES == (
        "in_process",
        "plex",
        "jellyfin",
        "tdarr",
    )


def test_routing_target_accepts_each_known_target() -> None:
    # ``tdarr`` requires a provider_profile_id; ``jellyfin`` is the
    # Literal value but is rejected at validation time because the
    # provider always refuses at runtime.
    for target in ROUTING_TARGET_VALUES:
        if target == "jellyfin":
            continue
        payload: dict = {"routing_target": target}
        if target == "tdarr":
            payload["provider_metadata"] = {
                "provider_profile_id": "tdarr-plugin-h265"
            }
        p = ProfileDefinition.model_validate(payload)
        assert p.routing_target == target


def test_routing_target_rejects_unknown() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate({"routing_target": "handbrake"})


def test_routing_target_jellyfin_rejected_at_validation() -> None:
    """Plan §443: Jellyfin's API has no job-submission endpoint, so
    surfacing it as a valid routing_target only sets operators up to
    fail at the first queue tick. Reject at save time instead."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        ProfileDefinition.model_validate({"routing_target": "jellyfin"})
    assert "Jellyfin" in str(exc_info.value)


def test_routing_target_tdarr_requires_provider_profile_id() -> None:
    """Without provider_metadata.provider_profile_id the Tdarr provider
    rejects every routed item with the same error. Catch the missing
    hint at the profile boundary instead."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        ProfileDefinition.model_validate({"routing_target": "tdarr"})
    assert "provider_profile_id" in str(exc_info.value)

    # Empty string also rejected — operator may have left the input
    # blank by accident.
    with pytest.raises(pydantic.ValidationError):
        ProfileDefinition.model_validate(
            {
                "routing_target": "tdarr",
                "provider_metadata": {"provider_profile_id": "   "},
            }
        )


# ── schedule_window ────────────────────────────────────────────


def test_schedule_window_hours_must_be_0_to_23() -> None:
    with pytest.raises(pydantic.ValidationError):
        ScheduleWindow(start_hour=24, end_hour=2)
    with pytest.raises(pydantic.ValidationError):
        ScheduleWindow(start_hour=-1, end_hour=2)


def test_schedule_window_timezone_defaults_to_resolved_server_zone() -> None:
    """Per addendum B.5: defaults to the server's local zone or UTC."""
    sw = ScheduleWindow(start_hour=0, end_hour=0)
    # The default function returns a string; depends on env. The
    # invariant is that it's non-empty and storable.
    assert isinstance(sw.timezone, str)
    assert len(sw.timezone) >= 1


def test_default_schedule_timezone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``TZ`` env var is unset and ``time.tzname[0]`` would
    return a recognizable value, the function MAY return that.
    With ``TZ`` set to something unusual, we get that back."""
    monkeypatch.setenv("TZ", "America/Denver")
    assert _default_schedule_timezone() == "America/Denver"


def test_schedule_window_extra_fields_forbidden() -> None:
    with pytest.raises(pydantic.ValidationError):
        ScheduleWindow.model_validate(
            {
                "start_hour": 9,
                "end_hour": 17,
                "timezone": "UTC",
                "weekend_only": True,  # not a real field
            }
        )


# ── schedule_window_is_open helper ─────────────────────────────


def test_window_is_open_returns_true_for_none() -> None:
    """No window = always open."""
    assert schedule_window_is_open(None) is True


def test_window_is_open_degenerate_equal_hours_always_open() -> None:
    """``start_hour == end_hour`` = always open (toggle-off form)."""
    sw = ScheduleWindow(start_hour=5, end_hour=5, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 12, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_same_day_inside() -> None:
    sw = ScheduleWindow(start_hour=9, end_hour=17, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 10, 30, tzinfo=_dt.UTC)
    )


def test_window_is_open_same_day_at_start_inclusive() -> None:
    sw = ScheduleWindow(start_hour=9, end_hour=17, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 9, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_same_day_at_end_exclusive() -> None:
    """The end hour is exclusive — 17 means up to 16:59:59."""
    sw = ScheduleWindow(start_hour=9, end_hour=17, timezone="UTC")
    assert not schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 17, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_same_day_outside() -> None:
    sw = ScheduleWindow(start_hour=9, end_hour=17, timezone="UTC")
    assert not schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 3, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_wraps_midnight_late_evening() -> None:
    """22..2 means "after 22:00 OR before 02:00"."""
    sw = ScheduleWindow(start_hour=22, end_hour=2, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 23, 30, tzinfo=_dt.UTC)
    )


def test_window_is_open_wraps_midnight_early_morning() -> None:
    sw = ScheduleWindow(start_hour=22, end_hour=2, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 17, 1, 30, tzinfo=_dt.UTC)
    )


def test_window_is_open_wraps_midnight_outside() -> None:
    sw = ScheduleWindow(start_hour=22, end_hour=2, timezone="UTC")
    assert not schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 12, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_honours_timezone() -> None:
    """An UTC-equivalent of 5am NY time should be inside a
    NY-zoned 8..17 window? No — 5am NYT is in the off-hours.
    Verify the function correctly converts to the window's tz."""
    sw = ScheduleWindow(start_hour=8, end_hour=17, timezone="America/New_York")
    # UTC 09:00 = NY 04:00 in summer — outside window.
    assert not schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 9, 0, tzinfo=_dt.UTC)
    )
    # UTC 15:00 = NY 10:00/11:00 — inside window.
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 15, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_unknown_timezone_falls_back_to_utc() -> None:
    """An unresolvable timezone is treated as UTC. The worker
    additionally logs a warning."""
    sw = ScheduleWindow(
        start_hour=9, end_hour=17, timezone="Not/A/Real/Zone"
    )
    # 10:00 UTC should be inside the 9..17 window after fallback.
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 10, 0, tzinfo=_dt.UTC)
    )
    # 23:00 UTC should be outside.
    assert not schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 23, 0, tzinfo=_dt.UTC)
    )


def test_window_is_open_naive_now_treated_as_utc() -> None:
    """Pass a naive datetime; the function interprets it as UTC."""
    sw = ScheduleWindow(start_hour=9, end_hour=17, timezone="UTC")
    assert schedule_window_is_open(
        sw, _dt.datetime(2026, 5, 16, 10, 0)
    )
