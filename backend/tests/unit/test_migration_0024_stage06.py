"""Stage 06 (v1.7) — migration 0024 adds rule-engine extension schema.

Per plan §354/§358 + addendum B.4:

  1. ``media_files.vt_status`` String(16), nullable.
  2. ``ix_media_files_probe_failed`` index on the pre-existing column.
  3. ``ix_media_files_vt_status`` index on the new column.
  4. ``rule_notification_windows`` table with a unique constraint
     on ``(rule_id, window_start)``.

This file's tests don't dig into the migration body (which is a
SQLAlchemy/Alembic mechanic). Instead they run the full chain end-
to-end against a temp SQLite DB and assert the final schema is
what Stage 06 expects.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


def _run_full_chain_to_head(db_path: Path) -> None:
    """Apply every Alembic migration from base to head against
    ``db_path``. Use aiosqlite (the project's async driver) since
    ``migrations/env.py`` uses ``async_engine_from_config``."""
    from alembic import command
    from alembic.config import Config

    os.environ["AUDITARR_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ.setdefault(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    # Resolve alembic.ini relative to the backend dir — the test
    # may be run from any cwd.
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    cfg.set_main_option(
        "script_location", str(backend_dir / "migrations")
    )
    command.upgrade(cfg, "head")


@pytest.fixture
def fresh_db():
    """A throwaway SQLite DB with the full migration chain
    applied. Yields the path; deletes the file on teardown."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".db", delete=False, prefix="auditarr_migrate_"
    )
    tmp.close()
    db_path = Path(tmp.name)
    try:
        _run_full_chain_to_head(db_path)
        yield db_path
    finally:
        try:
            db_path.unlink()
        except FileNotFoundError:
            pass


def test_full_chain_reaches_head_without_errors(fresh_db: Path) -> None:
    """Sanity: every migration runs cleanly. If any UP method
    raises, the fixture would have failed to yield."""
    # The fixture already ran upgrade-to-head — the assertion is
    # implicit. Confirm by looking at alembic_version.
    conn = sqlite3.connect(fresh_db)
    try:
        row = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    # Stage 10 (v1.7) bumped the head from 0024_stage06_rule_engine
    # to 0025_stage10_vt_queue by adding the vt_queue table.
    # Stage 12 (v1.7) bumped again to 0026_stage12_must_change_pw
    # adding the must_change_password + must_change_on_use flags.
    # Stage 17 (v1.8.0) bumps to 0027_stage17_playback_sessions
    # for the SSE-driven session lifecycle table.
    # v1.9 Stage 4.4 bumps to 0028_stage4_4_rule_templates for
    # the new rule_templates reference table.
    # v1.9 Stage 9.1 bumps to 0029_stage9_1_playback_devices for
    # the device index table.
    # v1.9 OP-10 bumps to 0030_playback_session_rating_key for
    # the reconciliation column + composite index.
    # v1.9 Stage 1 (updater commit-based feed) bumps to
    # 0031_updater_commit_columns for the commit SHA / date
    # columns on update_checks.
    assert row[0] == "0031_updater_commit_columns"


def test_vt_status_column_exists(fresh_db: Path) -> None:
    """Stage 06 (per addendum B.4) adds ``vt_status`` as a real
    column on ``media_files``."""
    conn = sqlite3.connect(fresh_db)
    try:
        cols = conn.execute("PRAGMA table_info(media_files)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    assert "vt_status" in col_names


def test_probe_failed_column_exists(fresh_db: Path) -> None:
    """``probe_failed`` is pre-existing (Stage 19-era column);
    Stage 06 wires it into the rule engine."""
    conn = sqlite3.connect(fresh_db)
    try:
        cols = conn.execute("PRAGMA table_info(media_files)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    assert "probe_failed" in col_names


def test_indexes_present_for_rule_engine_predicates(fresh_db: Path) -> None:
    """Both new indexes — ``ix_media_files_probe_failed`` and
    ``ix_media_files_vt_status`` — exist. The rule engine's
    "Probe failed" and "VirusTotal non-clean" built-ins both
    scan every library row on each pass; indexes keep those
    predicates cheap."""
    conn = sqlite3.connect(fresh_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='media_files'"
        ).fetchall()
    finally:
        conn.close()
    index_names = {r[0] for r in rows}
    assert "ix_media_files_probe_failed" in index_names
    assert "ix_media_files_vt_status" in index_names


def test_rule_notification_windows_table_exists(fresh_db: Path) -> None:
    """The throttle counter table is in place. Per plan §358 the
    schema is ``(rule_id, window_start, count)`` plus window_end
    for housekeeping."""
    conn = sqlite3.connect(fresh_db)
    try:
        cols = conn.execute(
            "PRAGMA table_info(rule_notification_windows)"
        ).fetchall()
    finally:
        conn.close()
    assert cols, "rule_notification_windows table missing"
    col_names = {c[1] for c in cols}
    assert {"rule_id", "window_start", "window_end", "count"}.issubset(col_names)


def test_rule_notification_windows_unique_constraint(fresh_db: Path) -> None:
    """``(rule_id, window_start)`` is unique so the service layer's
    increment-or-create pattern can rely on conflict detection.

    SQLite reports unique indexes; the alembic migration declares
    ``UniqueConstraint`` which is materialised as a unique index
    in the SQLite backend.
    """
    conn = sqlite3.connect(fresh_db)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type IN ('index', 'table') "
            "AND tbl_name = 'rule_notification_windows'"
        ).fetchall()
    finally:
        conn.close()
    combined = " ".join((r[0] or "") for r in rows).lower()
    # Either a UNIQUE constraint or a UNIQUE INDEX shows the
    # constraint name OR both columns paired with UNIQUE keyword.
    assert "uq_rule_notification_window_rule_start" in combined or (
        "unique" in combined and "rule_id" in combined
    )


def test_quarantine_columns_are_gone(fresh_db: Path) -> None:
    """Migration 0023 (Stage 05) drops the quarantine columns
    before Stage 06's 0024 adds vt_status. Regression guard for
    the chain ordering."""
    conn = sqlite3.connect(fresh_db)
    try:
        cols = conn.execute("PRAGMA table_info(media_files)").fetchall()
    finally:
        conn.close()
    col_names = {c[1] for c in cols}
    for forbidden in ("quarantined", "quarantined_at", "quarantined_reason"):
        assert forbidden not in col_names, (
            f"column {forbidden!r} still present after 0023 migration"
        )
