"""Settings module tests."""

from __future__ import annotations

import os

import pytest

from app.core.settings import Settings


def test_defaults_have_safe_dev_values() -> None:
    s = Settings()
    assert s.api_root == "/api/v1"
    assert s.is_production is False
    assert "5173" in s.allowed_origins[0]


def test_origins_split_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDITARR_ALLOWED_ORIGINS", "http://a.example,http://b.example")
    s = Settings()
    assert s.allowed_origins == ["http://a.example", "http://b.example"]


def test_sqlite_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDITARR_DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    s = Settings()
    assert s.is_sqlite is True


def test_paths_resolved(tmp_path) -> None:
    os.environ["AUDITARR_DATA_DIR"] = str(tmp_path / "data")
    try:
        s = Settings()
        assert s.data_dir.is_absolute()
    finally:
        del os.environ["AUDITARR_DATA_DIR"]


# ── v1.8.2: app_version derives from app.__version__ ─────────


def test_app_version_default_tracks_package_version() -> None:
    """v1.8.2: ``Settings.app_version`` defaults to the actual
    ``app.__version__`` so the updater never compares against a
    stale hardcoded "1.6.0" again.

    Pre-1.8.2 the default was hardcoded and every release forgot to
    bump it, with the result that the updater always thought the
    installed version was 1.6.0 and reported "update available" for
    any feed value >= 1.6.0.
    """
    from app import __version__

    s = Settings()
    # The default factory should produce the package version verbatim.
    assert s.app_version == __version__


def test_app_version_can_still_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators with non-standard deployment tooling (e.g. staging
    builds tagged with the git SHA) can override via
    AUDITARR_APP_VERSION. The factory is only the default."""
    monkeypatch.setenv("AUDITARR_APP_VERSION", "9.9.9-staging")
    s = Settings()
    assert s.app_version == "9.9.9-staging"


def test_update_feed_url_default_points_at_shogyx_auditarr() -> None:
    """v1.8.2: the default feed URL points at the real upstream
    repo (ShogyX/Auditarr). Pre-1.8.2 it pointed at a non-existent
    ``auditarr/auditarr`` so any fresh install hit a 404 on
    check-for-updates until the operator overrode it via the UI."""
    s = Settings()
    assert s.update_feed_url == (
        "https://api.github.com/repos/ShogyX/Auditarr/releases/latest"
    )
