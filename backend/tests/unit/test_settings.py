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


# ── v1.9.x: update sentinel paths derive from state_dir ────────


def test_apply_sentinel_defaults_to_state_dir_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """``AUDITARR_STATE_DIR`` set in auditarr.env (the bare-metal
    installer writes this to ``/var/lib/auditarr``) must steer the
    apply-request and apply-status sentinels to the same directory
    the host-side update watcher polls.

    Pre-fix the API ignored ``AUDITARR_STATE_DIR`` (no field in
    Settings), so the sentinel resolved against gunicorn's
    ``WorkingDirectory=/opt/auditarr/backend`` to
    ``/opt/auditarr/backend/data/updater/apply.request`` while the
    watcher polled ``/var/lib/auditarr/updater/apply.request``. Every
    update apply hit the 15-minute reaper with "host helper never
    reported back".
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AUDITARR_STATE_DIR", str(state))
    # Belt-and-suspenders: the operator may also have lingering
    # explicit sentinel overrides from an older auditarr.env. Clear
    # them so we exercise the derivation path.
    monkeypatch.delenv("AUDITARR_UPDATE_APPLY_SENTINEL", raising=False)
    monkeypatch.delenv("AUDITARR_UPDATE_APPLY_STATUS_PATH", raising=False)

    s = Settings()
    assert s.state_dir == state.resolve()
    assert s.update_apply_sentinel == (
        state / "updater" / "apply.request"
    ).resolve()
    assert s.update_apply_status_path == (
        state / "updater" / "apply.status"
    ).resolve()


def test_apply_sentinel_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Operators who pin ``AUDITARR_UPDATE_APPLY_SENTINEL`` (e.g. a
    custom host helper at a non-default location) keep that value;
    the after-validator only fills the None slot."""
    custom = tmp_path / "elsewhere" / "apply.request"
    monkeypatch.setenv("AUDITARR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AUDITARR_UPDATE_APPLY_SENTINEL", str(custom))

    s = Settings()
    assert s.update_apply_sentinel == custom.resolve()
    # The status path still defaults from state_dir.
    assert s.update_apply_status_path == (
        tmp_path / "state" / "updater" / "apply.status"
    ).resolve()


def test_apply_sentinel_defaults_to_data_dir_when_state_dir_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker installs that don't separate state_dir from data_dir
    keep working: with neither env var set, both default to ``./data``
    and the sentinel lands at ``./data/updater/apply.request`` —
    which is what the in-container helper sees on the bind-mounted
    volume."""
    monkeypatch.delenv("AUDITARR_STATE_DIR", raising=False)
    monkeypatch.delenv("AUDITARR_UPDATE_APPLY_SENTINEL", raising=False)
    monkeypatch.delenv("AUDITARR_UPDATE_APPLY_STATUS_PATH", raising=False)

    s = Settings()
    # Resolved against whatever CWD pytest is running from; we
    # check the relative tail to stay environment-agnostic.
    assert s.update_apply_sentinel is not None
    assert s.update_apply_sentinel.parts[-3:] == (
        "data",
        "updater",
        "apply.request",
    )
