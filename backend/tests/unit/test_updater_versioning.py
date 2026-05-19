"""Updater versioning tests."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.updater.versioning import (
    DEV_SENTINEL,
    UNKNOWN_COMMIT,
    is_newer,
    is_newer_commit,
    parse,
)


# ── parse ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.2.3", (1, 2, 3, None)),
        ("0.0.1", (0, 0, 1, None)),
        ("10.20.30", (10, 20, 30, None)),
        ("1.2.3-rc.1", (1, 2, 3, "rc.1")),
        ("1.0.0-alpha", (1, 0, 0, "alpha")),
        ("2.0.0-rc.10", (2, 0, 0, "rc.10")),
        ("  1.2.3  ", (1, 2, 3, None)),  # surrounding whitespace tolerated
    ],
)
def test_parse_valid_versions(version: str, expected: tuple) -> None:
    assert parse(version) == expected


@pytest.mark.parametrize(
    "version",
    [
        "v1.2.3",  # leading v
        "1.2",
        "1",
        "1.2.3.4",
        "",
        "release-2026.05.11",
        "not-a-version",
        "1.2.3-",  # empty prerelease
    ],
)
def test_parse_malformed_returns_none(version: str) -> None:
    assert parse(version) is None


# ── is_newer: dev sentinel ─────────────────────────────────────
def test_dev_sees_release_as_newer() -> None:
    assert is_newer("1.0.0", DEV_SENTINEL) is True


def test_dev_does_not_see_dev_as_newer() -> None:
    assert is_newer(DEV_SENTINEL, DEV_SENTINEL) is False


def test_release_does_not_see_dev_as_newer() -> None:
    """A weird config swap (release box pointed at a dev feed) shouldn't
    suggest "downgrade to dev" — the dev sentinel is older than every
    real release."""
    assert is_newer(DEV_SENTINEL, "1.0.0") is False


# ── is_newer: numeric trio ─────────────────────────────────────
@pytest.mark.parametrize(
    "candidate,installed,expected",
    [
        ("1.2.3", "1.2.2", True),
        ("1.3.0", "1.2.99", True),
        ("2.0.0", "1.99.99", True),
        ("1.2.3", "1.2.3", False),
        ("1.2.2", "1.2.3", False),
        ("1.2.0", "1.3.0", False),
        ("0.0.1", "0.0.0", True),
    ],
)
def test_numeric_comparison(
    candidate: str, installed: str, expected: bool
) -> None:
    assert is_newer(candidate, installed) is expected


# ── is_newer: prerelease ordering ──────────────────────────────
def test_release_beats_prerelease_at_same_trio() -> None:
    assert is_newer("1.2.3", "1.2.3-rc.1") is True


def test_prerelease_does_not_beat_release_at_same_trio() -> None:
    assert is_newer("1.2.3-rc.1", "1.2.3") is False


def test_prerelease_lexicographic() -> None:
    assert is_newer("1.2.3-rc.2", "1.2.3-rc.1") is True
    assert is_newer("1.2.3-rc.1", "1.2.3-rc.2") is False


def test_identical_prerelease_not_newer() -> None:
    assert is_newer("1.2.3-rc.1", "1.2.3-rc.1") is False


def test_higher_trio_beats_lower_even_with_prerelease() -> None:
    """1.3.0-rc.1 is still newer than 1.2.9 — the numeric trio wins."""
    assert is_newer("1.3.0-rc.1", "1.2.9") is True


# ── Malformed input ───────────────────────────────────────────
def test_malformed_candidate_falls_back_to_inequality() -> None:
    """When parsing fails, ``is_newer`` returns True iff the strings
    differ. This is conservative — operator pinned weird tags still get
    a "something changed" notification rather than silently missing it."""
    assert is_newer("nightly-2026-05-11", "nightly-2026-05-10") is True
    assert is_newer("nightly-2026-05-11", "nightly-2026-05-11") is False


# ── is_newer_commit (v1.9.x commit-based feed) ─────────────────


def _ts(year: int, month: int, day: int) -> _dt.datetime:
    return _dt.datetime(year, month, day, tzinfo=_dt.timezone.utc)


def test_commit_unknown_installed_always_newer() -> None:
    """The ``"unknown"`` SHA is the commit-equivalent of the
    ``0.0.0-dev`` version sentinel — any known remote commit wins."""
    assert is_newer_commit("abc123", UNKNOWN_COMMIT) is True
    assert is_newer_commit("abc123", "") is True


def test_commit_empty_candidate_not_newer() -> None:
    """Feed didn't return a SHA → nothing to compare against."""
    assert is_newer_commit("", "abc123") is False


def test_commit_same_sha_not_newer() -> None:
    """Identical SHAs short-circuit before the date comparison.
    Robust against clock skew between the install host and GitHub."""
    assert is_newer_commit(
        "abc123",
        "abc123",
        candidate_date=_ts(2026, 6, 1),
        installed_date=_ts(2026, 5, 1),
    ) is False


def test_commit_remote_strictly_later_date_is_newer() -> None:
    assert is_newer_commit(
        "deadbeef",
        "cafef00d",
        candidate_date=_ts(2026, 5, 18),
        installed_date=_ts(2026, 5, 17),
    ) is True


def test_commit_remote_equal_date_is_not_newer() -> None:
    """Equal dates between different SHAs is the genuine ambiguity
    case (think: side-branch commit with the same timestamp). The
    comparator refuses to claim "newer" so operators on a divergent
    branch don't see false "update available" prompts."""
    same = _ts(2026, 5, 17)
    assert is_newer_commit(
        "deadbeef", "cafef00d",
        candidate_date=same, installed_date=same,
    ) is False


def test_commit_remote_earlier_date_is_not_newer() -> None:
    """Operator is on a commit ahead of main shouldn't see an
    "update available" pointing at an older commit on main."""
    assert is_newer_commit(
        "olderaa",
        "newerbb",
        candidate_date=_ts(2026, 5, 1),
        installed_date=_ts(2026, 5, 17),
    ) is False


def test_commit_missing_one_date_falls_back_to_assume_newer() -> None:
    """One side has no date — different SHAs with missing temporal
    context surface as 'newer' (same conservative stance the
    version-string comparator takes for unknown shapes)."""
    assert is_newer_commit(
        "deadbeef", "cafef00d",
        candidate_date=_ts(2026, 5, 18),
        installed_date=None,
    ) is True
    assert is_newer_commit(
        "deadbeef", "cafef00d",
        candidate_date=None,
        installed_date=_ts(2026, 5, 18),
    ) is True


def test_commit_strips_whitespace() -> None:
    """Defensive — env-var-fed SHAs can come with newlines."""
    assert is_newer_commit("  abc123 \n", "abc123") is False
    assert is_newer_commit("abc123", "  abc123 \n") is False
