"""Async file hashing (Stage 19 audit follow-up).

Provides :func:`compute_sha256` for chunked async hashing and
:func:`should_rehash` so callers can decide if the database's
``hash_computed_at`` is still valid for the current ``mtime``.

Hashes are expensive on huge files. The whole module is built
around the rule "compute once per (path, mtime); cache forever".
The webhook dispatcher runs ``compute_sha256`` in a background
task so the receive endpoint can respond inside its HTTP budget.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.models.media import MediaFile

# 1 MiB. Larger reads don't speed things up on most file systems
# because the per-syscall overhead is dwarfed by the actual read,
# and smaller reads spend more CPU in Python.
_CHUNK_SIZE = 1024 * 1024


def should_rehash(media_file: MediaFile) -> bool:
    """Decide whether a fresh hash is worth computing.

    Returns ``True`` when:
    * the row has no hash yet, OR
    * the row was hashed BEFORE the current mtime (file modified
      since we last hashed).

    ``False`` when the hash is current. Callers should treat that
    as "cache hit; skip the hash".
    """
    if media_file.hash_sha256 is None:
        return True
    if media_file.hash_computed_at is None:
        # Defensive: hash present but no timestamp. Should not
        # happen — the writer sets both — but cheaper to rehash than
        # to guess.
        return True
    # ``mtime`` is recorded as the filesystem-reported modification
    # time. If the file was modified after we hashed it, the hash
    # is stale.
    return media_file.mtime > media_file.hash_computed_at


async def compute_sha256(path: str | Path) -> str:
    """Async chunked SHA-256.

    Returns the hex digest. Reads the file in 1 MiB chunks via
    ``asyncio.to_thread`` so the event loop stays responsive on
    huge files. Raises :class:`FileNotFoundError` /
    :class:`PermissionError` exactly as :func:`open` would; the
    dispatcher catches and logs without surfacing to the
    webhook responder.
    """
    return await asyncio.to_thread(_sha256_sync, str(path))


def _sha256_sync(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def utcnow() -> datetime:
    """Local alias so callers don't reach into ``app.utils.datetime``
    just for this. Kept tiny on purpose."""
    return datetime.now(UTC)
