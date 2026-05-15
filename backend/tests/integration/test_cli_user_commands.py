"""Tests for the user management CLI (Stage 18).

The bare-metal installer (``install-bare-metal.sh``) shells out to
these commands during install. We pin their contracts here so a
refactor can't silently break the installer:

- ``auditarr user count-admins`` prints exactly the admin count on
  stdout (no log noise), exits 0 in all normal cases.
- ``auditarr user bootstrap-admin`` creates a fresh admin from an
  env-supplied password, exits 0 on success, exits 3 on
  email/username conflict, exits 2 on missing/short password.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Isolated env: a fresh SQLite file, migrated to head, plus all
    the env vars the CLI needs to import settings + connect to the
    DB. Returned as a dict the test passes to ``subprocess.run``."""
    db_path = tmp_path / "cli.db"
    env = {
        **os.environ,
        "PYTHONPATH": ".",
        "AUDITARR_SECRET_KEY": "test-key-must-be-at-least-sixteen-chars-long",
        "AUDITARR_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "AUDITARR_REDIS_URL": "redis://localhost:6379/15",
        "AUDITARR_LOG_LEVEL": "info",
    }
    # Migrate to head before the CLI commands run.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )
    return env


def _run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Wrapper that invokes ``python -m app.cli <args...>`` with split
    stdout/stderr captures. Tests assert against these separately to
    confirm log lines go to stderr (so installer ``$(...)`` captures
    of stdout are clean)."""
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        env=env,
        capture_output=True,
        text=True,
    )


# ── count-admins ──────────────────────────────────────────────
def test_count_admins_on_empty_db_returns_zero(cli_env: dict[str, str]) -> None:
    r = _run_cli(cli_env, "user", "count-admins")
    assert r.returncode == 0
    assert r.stdout.strip() == "0"


def test_count_admins_stdout_is_only_the_number(cli_env: dict[str, str]) -> None:
    """The installer captures stdout via ``$(...)`` and feeds it into
    a numeric comparison. Any noise on stdout would break that."""
    r = _run_cli(cli_env, "user", "count-admins")
    # The entire stdout must parse cleanly as an int.
    assert r.stdout.strip().isdigit()
    # Log lines (info-level structlog output) must be on stderr.
    # We don't require *anything* on stderr, just that the count
    # is the sole stdout content.


# ── bootstrap-admin ───────────────────────────────────────────
def test_bootstrap_admin_creates_first_admin(cli_env: dict[str, str]) -> None:
    env = {**cli_env, "TEST_PW": "very-strong-password-123"}
    r = _run_cli(
        env,
        "user",
        "bootstrap-admin",
        "--email",
        "first@example.com",
        "--username",
        "firstadmin",
        "--password-from-env",
        "TEST_PW",
    )
    assert r.returncode == 0
    assert "created" in r.stdout

    # The user is now in the DB — count goes to 1.
    c = _run_cli(cli_env, "user", "count-admins")
    assert c.stdout.strip() == "1"


def test_bootstrap_admin_rejects_short_password(cli_env: dict[str, str]) -> None:
    env = {**cli_env, "TEST_PW": "short"}
    r = _run_cli(
        env,
        "user",
        "bootstrap-admin",
        "--email",
        "x@example.com",
        "--username",
        "shortpw",
        "--password-from-env",
        "TEST_PW",
    )
    assert r.returncode == 2
    assert "12 chars" in r.stderr


def test_bootstrap_admin_rejects_missing_env_var(cli_env: dict[str, str]) -> None:
    # The env var is missing entirely.
    r = _run_cli(
        cli_env,
        "user",
        "bootstrap-admin",
        "--email",
        "x@example.com",
        "--username",
        "nopw",
        "--password-from-env",
        "DEFINITELY_NOT_SET_5678",
    )
    assert r.returncode == 2


def test_bootstrap_admin_rejects_duplicate_email(cli_env: dict[str, str]) -> None:
    """Re-running the installer over an existing install must not
    silently clobber the first admin."""
    env = {**cli_env, "TEST_PW": "very-strong-password-123"}

    # First create succeeds.
    r1 = _run_cli(
        env,
        "user", "bootstrap-admin",
        "--email", "dup@example.com",
        "--username", "first",
        "--password-from-env", "TEST_PW",
    )
    assert r1.returncode == 0

    # Same email, different username — should fail with exit 3.
    r2 = _run_cli(
        env,
        "user", "bootstrap-admin",
        "--email", "dup@example.com",
        "--username", "second",
        "--password-from-env", "TEST_PW",
    )
    assert r2.returncode == 3
    assert "already exists" in r2.stderr


def test_bootstrap_admin_rejects_duplicate_username(cli_env: dict[str, str]) -> None:
    env = {**cli_env, "TEST_PW": "very-strong-password-123"}

    r1 = _run_cli(
        env,
        "user", "bootstrap-admin",
        "--email", "a@example.com",
        "--username", "samename",
        "--password-from-env", "TEST_PW",
    )
    assert r1.returncode == 0

    r2 = _run_cli(
        env,
        "user", "bootstrap-admin",
        "--email", "b@example.com",
        "--username", "samename",
        "--password-from-env", "TEST_PW",
    )
    assert r2.returncode == 3
    assert "already taken" in r2.stderr
