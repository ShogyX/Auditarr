"""Unit tests for :mod:`app.integrations.path_mapping`."""

from __future__ import annotations

from app.integrations.path_mapping import (
    DriftReport,
    PathMapping,
    parse_mappings,
    remap_path,
)


class TestParseMappings:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_mappings([]) == []
        assert parse_mappings(None) == []
        assert parse_mappings("not a list") == []
        assert parse_mappings({"from": "/a", "to": "/b"}) == []

    def test_accepts_from_to_shape(self) -> None:
        out = parse_mappings([{"from": "/data/movies", "to": "/mnt/media/Movies"}])
        assert out == [PathMapping("/data/movies", "/mnt/media/Movies")]

    def test_accepts_src_prefix_shape(self) -> None:
        """Forward-compat shape with the typed names."""
        out = parse_mappings(
            [{"src_prefix": "/data/movies", "dst_prefix": "/mnt/media/Movies"}]
        )
        assert out == [PathMapping("/data/movies", "/mnt/media/Movies")]

    def test_strips_trailing_slashes(self) -> None:
        out = parse_mappings([{"from": "/data/movies/", "to": "/mnt/media/Movies/"}])
        assert out == [PathMapping("/data/movies", "/mnt/media/Movies")]

    def test_drops_malformed_entries(self) -> None:
        out = parse_mappings(
            [
                {"from": "/a", "to": "/b"},
                "not a dict",
                {"from": "/c"},  # missing 'to'
                {"to": "/d"},  # missing 'from'
                {"from": "", "to": "/empty"},  # empty src
                {"from": "/e", "to": ""},  # empty dst
                {"from": 12, "to": "/f"},  # non-string src
                {"from": "/g", "to": "/h"},
            ]
        )
        assert out == [PathMapping("/a", "/b"), PathMapping("/g", "/h")]

    def test_sorts_longest_prefix_first(self) -> None:
        """The longest src_prefix must win when multiple mappings could
        match — so ``/data/tv/shows`` should be rewritten by the
        ``/data/tv/shows`` mapping, not the shorter ``/data`` one."""
        out = parse_mappings(
            [
                {"from": "/data", "to": "/short"},
                {"from": "/data/tv/shows", "to": "/long"},
                {"from": "/data/tv", "to": "/med"},
            ]
        )
        # Verify they're sorted longest-prefix-first.
        assert [m.src_prefix for m in out] == [
            "/data/tv/shows",
            "/data/tv",
            "/data",
        ]


class TestRemapPath:
    def test_unmatched_path_returned_unchanged(self) -> None:
        mappings = parse_mappings([{"from": "/data", "to": "/mnt"}])
        assert remap_path("/elsewhere/file.mkv", mappings) == "/elsewhere/file.mkv"

    def test_no_mappings_returns_original(self) -> None:
        assert remap_path("/anywhere/file.mkv", []) == "/anywhere/file.mkv"

    def test_simple_rewrite(self) -> None:
        mappings = parse_mappings(
            [{"from": "/data/movies", "to": "/mnt/media/Movies"}]
        )
        assert (
            remap_path("/data/movies/Dune (2024).mkv", mappings)
            == "/mnt/media/Movies/Dune (2024).mkv"
        )

    def test_respects_directory_boundaries(self) -> None:
        """A mapping of ``/data/tv`` must not match ``/data/tvshows`` —
        the boundary is a literal ``/``."""
        mappings = parse_mappings([{"from": "/data/tv", "to": "/mnt/TV"}])
        # Exact match → rewrite to bare dst.
        assert remap_path("/data/tv", mappings) == "/mnt/TV"
        # Child path → rewrite preserves the suffix.
        assert (
            remap_path("/data/tv/Severance/s01e01.mkv", mappings)
            == "/mnt/TV/Severance/s01e01.mkv"
        )
        # Sibling that *starts with* the prefix → must NOT be rewritten.
        assert remap_path("/data/tvshows/x.mkv", mappings) == "/data/tvshows/x.mkv"

    def test_longest_prefix_wins(self) -> None:
        mappings = parse_mappings(
            [
                {"from": "/data", "to": "/short"},
                {"from": "/data/tv/shows", "to": "/long"},
                {"from": "/data/tv", "to": "/med"},
            ]
        )
        assert remap_path("/data/tv/shows/a.mkv", mappings) == "/long/a.mkv"
        assert remap_path("/data/tv/movies/a.mkv", mappings) == "/med/movies/a.mkv"
        assert remap_path("/data/other/a.mkv", mappings) == "/short/other/a.mkv"


class TestDriftReport:
    def test_no_drift_when_all_resolved(self) -> None:
        r = DriftReport(seen=10, resolved=10)
        assert not r.drift_suspected
        assert r.resolution_rate == 1.0
        assert r.detail() == ""

    def test_no_drift_under_sample_threshold(self) -> None:
        """Fewer than 5 samples is not enough to call drift."""
        r = DriftReport(seen=4, resolved=0)
        assert not r.drift_suspected

    def test_drift_suspected_when_majority_unresolved(self) -> None:
        r = DriftReport(seen=20, resolved=5)
        assert r.drift_suspected
        assert r.resolution_rate == 0.25
        msg = r.detail()
        assert "15 of 20" in msg
        # No mappings configured → suggest configuring them.
        assert "Configure path mappings" in msg

    def test_drift_message_changes_when_mappings_configured(self) -> None:
        r = DriftReport(
            seen=20, resolved=5, has_mappings_configured=True
        )
        msg = r.detail()
        assert "even with configured mappings" in msg

    def test_resolution_rate_handles_empty_batch(self) -> None:
        assert DriftReport().resolution_rate == 1.0
