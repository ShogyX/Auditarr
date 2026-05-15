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
