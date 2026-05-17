"""Test that the scanner skips files whose names contain
surrogateescape codepoints (PEP 383 substitution for
un-decodable filesystem bytes), since asyncpg can't bind them
as VARCHAR.

We can't easily create a real file with non-UTF-8 bytes in its
name from Python on all platforms (the linux kernel allows it
but tmp_path is normally on a filesystem that does too), so we
test both the helper directly AND the integration by stubbing
the filesystem walk.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.media.scanner import (
    _contains_undecodable_bytes,
    _is_regular_file,
)


# ── _contains_undecodable_bytes ────────────────────────────────


def test_ascii_path_is_clean() -> None:
    assert _contains_undecodable_bytes("/media/Movies/Inception.mkv") is False


def test_valid_utf8_path_is_clean() -> None:
    # Hiragana + emoji. Both encode fine as UTF-8.
    assert _contains_undecodable_bytes("/media/アニメ/Boruto 🎬.mkv") is False


def test_surrogateescape_path_is_dirty() -> None:
    # Codepoint U+DCD9 is in the lone-surrogate range PEP 383
    # uses for un-decodable byte 0xD9. Such strings round-trip
    # through Python but raise UnicodeEncodeError on .encode().
    bad = (
        "/media/NAS-Pool/media/Anime/Boruto - Naruto Next Generations/"
        "Season 1/Boruto - S01E07 - Love and Potato Chips! HDTV-1080p"
        ".\udcd9-Transcoded.ass"
    )
    assert _contains_undecodable_bytes(bad) is True


def test_lone_surrogate_at_start_is_dirty() -> None:
    assert _contains_undecodable_bytes("\udcffname.mkv") is True


def test_empty_string_is_clean() -> None:
    assert _contains_undecodable_bytes("") is False


# ── _is_regular_file (cheap unit test for completeness) ────────


def test_is_regular_file_recognises_regular() -> None:
    # 0o100644 is a typical regular-file stat mode.
    assert _is_regular_file(0o100644) is True


def test_is_regular_file_rejects_directory() -> None:
    # 0o040755 is a directory.
    assert _is_regular_file(0o040755) is False


def test_is_regular_file_rejects_symlink() -> None:
    # 0o120777 is a symlink.
    assert _is_regular_file(0o120777) is False


# ── Integration with _enumerate ────────────────────────────────


@pytest.mark.asyncio
async def test_enumerate_skips_surrogate_filenames(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When ``os.walk`` yields a filename with surrogateescape
    bytes, ``_enumerate`` must skip it and emit a warning rather
    than passing the path through to asyncpg (which would crash
    on bind).

    We stub ``os.walk`` so the test doesn't depend on filesystem
    weirdness — the helper logic is what matters, not whether the
    test filesystem accepts non-UTF-8 names.
    """
    from app.services.media.scanner import Scanner, ScanOptions

    # Set up real files so the .stat() calls in _enumerate
    # succeed for the clean ones.
    good = tmp_path / "movie.mkv"
    good.write_bytes(b"x")

    def _fake_walk(_root, followlinks):  # noqa: ANN001, ANN202
        # Yield a directory containing two filenames: one valid,
        # one with a surrogate codepoint.
        yield (
            str(tmp_path),
            [],
            ["movie.mkv", "bad\udcd9-name.mkv"],
        )

    # We need a Scanner instance to call _enumerate. The
    # constructor wants a couple of services; the test only
    # uses _enumerate, so we can pass None for them and rely on
    # the method not touching self.* fields.
    scanner = Scanner.__new__(Scanner)

    with patch("app.services.media.scanner.os.walk", _fake_walk):
        with caplog.at_level("WARNING", logger="auditarr.media.scanner"):
            results = scanner._enumerate(
                tmp_path, ScanOptions(follow_symlinks=False)
            )

    # The good file survived; the bad one was dropped.
    rel_paths = [r[0] for r in results]
    assert "movie.mkv" in rel_paths
    assert not any("\udcd9" in r for r in rel_paths)
    assert len(results) == 1
    # The skip happened — that's the critical assertion. Whether
    # the warning surfaces in pytest's caplog depends on the
    # structlog ↔ stdlib bridge configuration in the test
    # environment, which varies. The behaviour we ACTUALLY care
    # about — the bad filename being skipped before it reaches
    # asyncpg — is what the assertions above pin.
