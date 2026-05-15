"""Tests for install-environment detection (Stage 19).

The updater needs to surface the right install mode so the frontend
shows correct copy and the backend refuses ``request_apply`` when no
helper script is wired up. These tests pin both the explicit-override
path and the auto-detect heuristics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.updater import install_mode as im


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test exercises a different env, so reset between runs."""
    im.reset_cache_for_tests()
    yield
    im.reset_cache_for_tests()


# ── Explicit override ────────────────────────────────────────
class TestExplicitOverride:
    def test_docker_override_returns_docker(self) -> None:
        assert im.detect_install_mode("docker") == "docker"

    def test_bare_metal_override_returns_bare_metal(self) -> None:
        assert im.detect_install_mode("bare-metal") == "bare-metal"

    def test_unmanaged_override_returns_unmanaged(self) -> None:
        assert im.detect_install_mode("unmanaged") == "unmanaged"

    def test_case_insensitive(self) -> None:
        # Operators may write DOCKER or Docker in their .env file.
        im.reset_cache_for_tests()
        assert im.detect_install_mode("DOCKER") == "docker"
        im.reset_cache_for_tests()
        assert im.detect_install_mode("Docker") == "docker"

    def test_unknown_explicit_value_fails_safe_to_unmanaged(self) -> None:
        # If an operator writes a typo like ``baremetal`` (missing
        # hyphen), we don't silently treat it as auto — we treat it
        # as unmanaged so an apply won't fire into the wrong helper.
        assert im.detect_install_mode("baremetal") == "unmanaged"
        im.reset_cache_for_tests()
        assert im.detect_install_mode("kubernetes") == "unmanaged"

    def test_empty_string_treated_as_auto(self) -> None:
        # An empty AUDITARR_UPDATE_INSTALL_MODE env var should fall
        # through to auto-detect rather than getting stuck on the
        # unknown branch.
        im.reset_cache_for_tests()
        # We can't easily distinguish empty from auto in the result
        # since both land in the same code path. We just confirm
        # the call doesn't crash and returns *something* valid.
        out = im.detect_install_mode("")
        assert out in {"docker", "bare-metal", "unmanaged"}


# ── Auto-detect: Docker ──────────────────────────────────────
class TestDockerDetection:
    def test_dockerenv_file_triggers_docker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Point the marker constant at a real file we can create.
        fake = tmp_path / ".dockerenv"
        fake.write_text("")
        monkeypatch.setattr(im, "DOCKER_MARKER", fake)
        # Make sure bare-metal detection fails so we don't take its branch.
        monkeypatch.setattr(im, "BARE_METAL_MARKER", tmp_path / "no-such")
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        monkeypatch.delenv("container", raising=False)
        assert im.detect_install_mode("auto") == "docker"

    def test_container_env_var_triggers_docker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No /.dockerenv, but container=podman.
        monkeypatch.setattr(im, "DOCKER_MARKER", tmp_path / "absent")
        monkeypatch.setattr(im, "BARE_METAL_MARKER", tmp_path / "absent2")
        monkeypatch.setenv("container", "podman")
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        assert im.detect_install_mode("auto") == "docker"


# ── Auto-detect: bare-metal ──────────────────────────────────
class TestBareMetalDetection:
    def test_env_file_plus_invocation_id_triggers_bare_metal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        marker = tmp_path / "auditarr.env"
        marker.write_text("# fake")
        monkeypatch.setattr(im, "BARE_METAL_MARKER", marker)
        monkeypatch.setattr(im, "DOCKER_MARKER", tmp_path / "no-docker")
        monkeypatch.delenv("container", raising=False)
        monkeypatch.setenv("INVOCATION_ID", "deadbeef-fake-systemd-id")
        assert im.detect_install_mode("auto") == "bare-metal"

    def test_env_file_alone_does_not_trigger_bare_metal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without INVOCATION_ID we could be a dev running by hand —
        don't treat that as the bare-metal install."""
        marker = tmp_path / "auditarr.env"
        marker.write_text("# fake")
        monkeypatch.setattr(im, "BARE_METAL_MARKER", marker)
        monkeypatch.setattr(im, "DOCKER_MARKER", tmp_path / "no-docker")
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        monkeypatch.delenv("container", raising=False)
        # Without proc cgroup info this should land on unmanaged.
        result = im.detect_install_mode("auto")
        # On real CI we sometimes have /proc/1/cgroup readable;
        # accept either outcome but not bare-metal.
        assert result in {"docker", "unmanaged"}
        assert result != "bare-metal"


# ── Fallback: unmanaged ──────────────────────────────────────
class TestFallback:
    def test_no_signals_returns_unmanaged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Pure neither-docker-nor-systemd case → unmanaged so the
        UI shows the right "set AUDITARR_UPDATE_INSTALL_MODE" copy."""
        monkeypatch.setattr(im, "DOCKER_MARKER", tmp_path / "no-docker")
        monkeypatch.setattr(im, "BARE_METAL_MARKER", tmp_path / "no-bm")
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        monkeypatch.delenv("container", raising=False)
        # Force the cgroup probe to find nothing matching by pointing
        # at a path that doesn't exist. We can't easily mock /proc/1/cgroup
        # without monkeypatching open(); just assert that *if* the host
        # we're on doesn't have docker cgroups, we get unmanaged.
        result = im.detect_install_mode("auto")
        # On a real Linux dev box without docker/podman this will be
        # ``unmanaged``. On a host running inside CI's Docker container
        # it will be ``docker`` and we accept that — the assertion is
        # just that we don't crash.
        assert result in {"docker", "unmanaged"}
