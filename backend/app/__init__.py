"""Auditarr backend package."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.9.0"


def _git(args: list[str], *, cwd: Path) -> str | None:
    """Run a short read-only ``git`` command, returning stdout or ``None``.

    Used at import time to resolve the current commit. Failures are
    swallowed — non-git deployments (Docker images that don't ship the
    ``.git`` directory, source tarballs, etc.) must still import the
    package without raising.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _resolve_installed_commit() -> tuple[str, datetime | None]:
    """Resolve the commit SHA and committer date of the running build.

    Resolution order:

    1. ``AUDITARR_COMMIT_SHA`` env var (and ``AUDITARR_COMMIT_DATE`` for
       the timestamp) — production-image builds bake these in.
    2. ``git rev-parse HEAD`` against the repo root.
    3. ``"unknown"`` SHA with ``None`` date — the updater treats this as
       "always look like an update is available" (the dev-sentinel
       behaviour the version flow already had for ``0.0.0-dev``).

    Returns ``(sha_or_unknown, committer_date_or_None)``.
    """
    env_sha = os.environ.get("AUDITARR_COMMIT_SHA", "").strip()
    env_date_raw = os.environ.get("AUDITARR_COMMIT_DATE", "").strip()
    env_date: datetime | None = None
    if env_date_raw:
        try:
            env_date = datetime.fromisoformat(env_date_raw.replace("Z", "+00:00"))
            if env_date.tzinfo is None:
                env_date = env_date.replace(tzinfo=timezone.utc)
        except ValueError:
            env_date = None
    if env_sha:
        return env_sha, env_date

    # Walk up from this file to find the repo root. ``backend/app/__init__.py``
    # → backend/app → backend → repo root.
    repo_root = Path(__file__).resolve().parents[2]
    sha = _git(["rev-parse", "HEAD"], cwd=repo_root)
    if sha is None:
        return "unknown", None

    iso = _git(["show", "-s", "--format=%cI", "HEAD"], cwd=repo_root)
    parsed: datetime | None = None
    if iso:
        try:
            parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = None
    return sha, parsed


__commit__, __commit_date__ = _resolve_installed_commit()
