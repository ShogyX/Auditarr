"""Stage 19 (audit follow-up) — file hash service unit tests.

Pins:
  1. ``compute_sha256`` returns the canonical SHA-256 of a file.
  2. ``should_rehash`` returns True for never-hashed or
     mtime-newer-than-hash rows.
  3. ``should_rehash`` returns False when the hash is current.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.media import MediaFile
from app.services.file_hash import compute_sha256, should_rehash


@pytest.mark.asyncio
async def test_compute_sha256_matches_hashlib(tmp_path: Path) -> None:
    p = tmp_path / "blob.bin"
    payload = b"hello world from stage 19" * 1000
    p.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()
    actual = await compute_sha256(p)
    assert actual == expected


def test_should_rehash_true_when_no_hash() -> None:
    mf = MediaFile(
        id="x",
        library_id="l",
        path="/a",
        relative_path="a",
        filename="a",
        extension="mkv",
        size_bytes=0,
        mtime=datetime.now(UTC),
    )
    assert should_rehash(mf) is True


def test_should_rehash_true_when_mtime_newer_than_hash() -> None:
    now = datetime.now(UTC)
    mf = MediaFile(
        id="x",
        library_id="l",
        path="/a",
        relative_path="a",
        filename="a",
        extension="mkv",
        size_bytes=0,
        mtime=now,  # file modified now
        hash_sha256="deadbeef" * 8,
        hash_computed_at=now - timedelta(hours=1),  # hashed an hour ago
    )
    assert should_rehash(mf) is True


def test_should_rehash_false_when_hash_current() -> None:
    now = datetime.now(UTC)
    mf = MediaFile(
        id="x",
        library_id="l",
        path="/a",
        relative_path="a",
        filename="a",
        extension="mkv",
        size_bytes=0,
        mtime=now - timedelta(hours=2),  # file last modified 2h ago
        hash_sha256="deadbeef" * 8,
        hash_computed_at=now - timedelta(hours=1),  # hashed 1h ago, newer than mtime
    )
    assert should_rehash(mf) is False
