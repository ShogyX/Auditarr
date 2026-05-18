"""v1.9 Stage 8.2 — Tdarr handoff helpers.

Pins:
  1. ``score_stack`` returns 0 when no signals match.
  2. Codec keyword in name scores higher than in description.
  3. Hardware acceleration bonus only when ``prefer_hardware``.
  4. Stage bonus only when codec also matches.
  5. Codec aliases (h265 / hevc / x265) all hit.
  6. ``pick_best_plugin`` returns the highest-scoring plugin.
  7. ``pick_best_plugin`` returns None when nothing matches.
  8. ``pick_best_plugin`` returns None on empty list.
  9. ``build_output_name`` switches extension to .mkv.
 10. ``build_output_name`` appends the target codec.
 11. ``build_output_name`` is idempotent: re-running on an
     already-renamed path returns the same name.
 12. ``build_output_name`` preserves the directory part of the
     path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_tdarr():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "tdarr"
    spec = importlib.util.spec_from_file_location(
        "tdarr_plugin_backend_v19s8", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tdarr_plugin_backend_v19s8"] = module
    spec.loader.exec_module(module)
    return module


# ── score_stack ─────────────────────────────────────────────────


def test_score_stack_returns_zero_on_no_signals() -> None:
    mod = _load_tdarr()
    assert (
        mod.score_stack(
            {"Name": "Random", "Description": "nothing useful"},
            target_codec="hevc",
        )
        == 0
    )


def test_score_stack_name_beats_description() -> None:
    mod = _load_tdarr()
    in_name = mod.score_stack(
        {"Name": "Tdarr_Plugin_hevc_thing", "Description": ""},
        target_codec="hevc",
    )
    in_desc = mod.score_stack(
        {"Name": "Generic", "Description": "Transcodes to HEVC"},
        target_codec="hevc",
    )
    assert in_name > in_desc
    assert in_name >= 20  # the name-match weight


def test_score_stack_hardware_bonus_only_when_requested() -> None:
    mod = _load_tdarr()
    plugin = {"Name": "Tdarr_Plugin_hevc_nvenc", "Description": ""}
    with_hw = mod.score_stack(plugin, target_codec="hevc", prefer_hardware=True)
    without_hw = mod.score_stack(
        plugin, target_codec="hevc", prefer_hardware=False
    )
    assert with_hw > without_hw


def test_score_stack_stage_bonus_only_when_codec_matches() -> None:
    """A Pre-processing-stage plugin that doesn't match the
    codec gets 0 — the stage bonus is a tiebreaker, not a
    standalone signal. Otherwise every plugin in a typical
    Tdarr install gets +2 and the picker loses discrimination."""
    mod = _load_tdarr()
    no_match = mod.score_stack(
        {"Name": "Random", "Description": "x", "Stage": "Pre-processing"},
        target_codec="hevc",
    )
    match = mod.score_stack(
        {
            "Name": "Tdarr_Plugin_hevc_thing",
            "Description": "x",
            "Stage": "Pre-processing",
        },
        target_codec="hevc",
    )
    assert no_match == 0
    assert match >= 22  # name match + stage bonus


def test_score_stack_codec_aliases_all_match() -> None:
    """h265 / hevc / x265 are interchangeable in Tdarr plugin
    naming. The picker should treat them as aliases."""
    mod = _load_tdarr()
    for variant in ("hevc", "h265", "h.265", "x265"):
        plugin = {"Name": f"Tdarr_Plugin_{variant}_thing", "Description": ""}
        assert (
            mod.score_stack(plugin, target_codec="h265") > 0
        ), f"variant {variant!r} did not match"


# ── pick_best_plugin ────────────────────────────────────────────


def test_pick_best_plugin_returns_highest_scorer() -> None:
    mod = _load_tdarr()
    plugins = [
        {"id": "A", "Name": "Tdarr_Plugin_hevc", "Description": ""},
        {"id": "B", "Name": "Tdarr_Plugin_hevc_nvenc", "Description": ""},
        {"id": "C", "Name": "Generic", "Description": "HEVC notes"},
    ]
    pick = mod.pick_best_plugin(
        plugins, target_codec="hevc", prefer_hardware=True
    )
    assert pick is not None
    assert pick["id"] == "B"


def test_pick_best_plugin_returns_none_when_nothing_matches() -> None:
    """Avoid a random tiebreak when the heuristic is uncertain
    — operators should pick explicitly rather than have us
    guess."""
    mod = _load_tdarr()
    plugins = [
        {"id": "X", "Name": "Generic", "Description": "misc"},
        {"id": "Y", "Name": "Other", "Description": "stuff"},
    ]
    assert mod.pick_best_plugin(plugins, target_codec="av1") is None


def test_pick_best_plugin_returns_none_on_empty_list() -> None:
    mod = _load_tdarr()
    assert mod.pick_best_plugin([], target_codec="hevc") is None


def test_pick_best_plugin_ignores_non_dict_entries() -> None:
    """A malformed plugin row (e.g. None or a string from a
    bad Tdarr version) shouldn't blow up the picker."""
    mod = _load_tdarr()
    plugins = [
        None,
        "bad",
        {"id": "ok", "Name": "Tdarr_Plugin_hevc", "Description": ""},
    ]
    pick = mod.pick_best_plugin(plugins, target_codec="hevc")  # type: ignore[arg-type]
    assert pick is not None
    assert pick["id"] == "ok"


# ── build_output_name ───────────────────────────────────────────


def test_build_output_name_switches_extension_to_mkv() -> None:
    mod = _load_tdarr()
    assert (
        mod.build_output_name(input_path="/data/Movie.mp4") == "/data/Movie.mkv"
    )


def test_build_output_name_appends_target_codec() -> None:
    mod = _load_tdarr()
    assert (
        mod.build_output_name(input_path="/data/Movie.mp4", target_codec="hevc")
        == "/data/Movie.hevc.mkv"
    )


def test_build_output_name_appends_suffix_before_codec() -> None:
    mod = _load_tdarr()
    assert (
        mod.build_output_name(
            input_path="/data/Movie.mp4",
            target_codec="hevc",
            suffix="transcoded",
        )
        == "/data/Movie.transcoded.hevc.mkv"
    )


def test_build_output_name_is_idempotent_on_codec_token() -> None:
    """Running on an already-formatted name must NOT re-append
    the codec — operators re-routing the same file should see
    the same target name."""
    mod = _load_tdarr()
    assert (
        mod.build_output_name(
            input_path="/data/Movie.hevc.mkv", target_codec="hevc"
        )
        == "/data/Movie.hevc.mkv"
    )


def test_build_output_name_is_idempotent_on_suffix() -> None:
    mod = _load_tdarr()
    assert (
        mod.build_output_name(
            input_path="/data/Movie.transcoded.mkv", suffix="transcoded"
        )
        == "/data/Movie.transcoded.mkv"
    )


def test_build_output_name_preserves_directory_part() -> None:
    mod = _load_tdarr()
    assert (
        mod.build_output_name(
            input_path="/mnt/media/Movies/Some Film (2024)/film.mp4",
            target_codec="hevc",
        )
        == "/mnt/media/Movies/Some Film (2024)/film.hevc.mkv"
    )


def test_build_output_name_handles_bare_filename() -> None:
    """No directory component → relative output filename."""
    mod = _load_tdarr()
    assert (
        mod.build_output_name(input_path="film.mp4", target_codec="hevc")
        == "film.hevc.mkv"
    )


def test_build_output_name_handles_empty_input() -> None:
    mod = _load_tdarr()
    # Empty path passes through; caller's responsibility to
    # detect upstream.
    assert mod.build_output_name(input_path="") == ""
