"""Tests for app.core.ssl_bundle — CA bundle fallback chain.

Pin the resolution order: env var → certifi → OS candidates →
CABundleMissingError. Reset the module-level cache between tests
so a stale resolution doesn't leak across cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core import ssl_bundle


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the resolver cache + scrub the CA env vars so each
    test starts from a clean slate."""
    ssl_bundle.reset_cache_for_tests()
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)


# ── Env var precedence ─────────────────────────────────────────


def test_env_var_takes_precedence_over_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``SSL_CERT_FILE`` points at an existing file, the
    resolver returns it WITHOUT consulting certifi or OS
    candidates. Operators with corporate CAs depend on this."""
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))

    # Even if certifi WOULD have resolved, env var wins.
    assert ssl_bundle.resolve_ca_bundle() == str(bundle)


def test_env_var_missing_file_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``SSL_CERT_FILE`` is set to a path that doesn't
    exist, the resolver falls through to the next layer rather
    than raising. This matches the OS behaviour with curl."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist.pem"))
    # Force fallback layers to fail too.
    monkeypatch.setattr(ssl_bundle, "_OS_BUNDLE_CANDIDATES", ())
    # Stub certifi missing.
    monkeypatch.setitem(sys.modules, "certifi", None)
    # The above doesn't actually make ``import certifi`` raise;
    # instead it makes attribute access fail. The resolver uses
    # ``importlib`` semantics through ``import certifi`` — to
    # truly hide certifi we delete it from sys.modules.
    sys.modules.pop("certifi", None)
    # Use a sentinel module that raises on import.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError("certifi forcibly hidden for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ssl_bundle.CABundleMissingError) as ei:
        ssl_bundle.resolve_ca_bundle()
    assert "SSL_CERT_FILE" in str(ei.value)


def test_requests_ca_bundle_var_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``REQUESTS_CA_BUNDLE`` is honoured too — many Python
    ecosystems use this name."""
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    assert ssl_bundle.resolve_ca_bundle() == str(bundle)


# ── OS candidate fallback ──────────────────────────────────────


def test_os_candidates_resolved_when_certifi_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no env var is set and certifi is unimportable, the
    resolver picks the first OS candidate that exists."""
    bundle = tmp_path / "etc-ssl-ca.pem"
    bundle.write_text("dummy")

    monkeypatch.setattr(
        ssl_bundle,
        "_OS_BUNDLE_CANDIDATES",
        (str(tmp_path / "nope.pem"), str(bundle)),
    )

    # Force certifi import failure.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError("hidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert ssl_bundle.resolve_ca_bundle() == str(bundle)


# ── Nothing found ──────────────────────────────────────────────


def test_raises_when_no_bundle_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env vars are unset, certifi is missing, and no OS
    candidate exists, the resolver raises with a diagnostic
    that names every layer it tried."""
    monkeypatch.setattr(ssl_bundle, "_OS_BUNDLE_CANDIDATES", ())

    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError("hidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ssl_bundle.CABundleMissingError) as ei:
        ssl_bundle.resolve_ca_bundle()
    msg = str(ei.value)
    # The diagnostic must list every layer the resolver tried,
    # so the operator can fix the deployment without reading
    # the source.
    assert "env vars" in msg
    assert "certifi" in msg
    assert "OS bundle candidates" in msg
    assert "ca-certificates" in msg.lower()


# ── Caching ────────────────────────────────────────────────────


def test_resolve_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated calls return the same path without re-walking.

    We verify by mutating the env after the first call: if the
    cache works, the second call returns the original path
    instead of the new one.
    """
    first = tmp_path / "first.pem"
    first.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", str(first))

    a = ssl_bundle.resolve_ca_bundle()

    second = tmp_path / "second.pem"
    second.write_text("y")
    monkeypatch.setenv("SSL_CERT_FILE", str(second))

    b = ssl_bundle.resolve_ca_bundle()
    assert a == b == str(first)


# ── Startup sanity check ───────────────────────────────────────


def test_startup_sanity_check_returns_true_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert ssl_bundle.startup_sanity_check(fatal=False) is True


def test_startup_sanity_check_returns_false_on_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-fatal mode logs but doesn't raise. The app keeps
    booting; outbound HTTPS falls back to verify=False."""
    monkeypatch.setattr(ssl_bundle, "_OS_BUNDLE_CANDIDATES", ())
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError("hidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert ssl_bundle.startup_sanity_check(fatal=False) is False


def test_startup_sanity_check_raises_when_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fatal=True re-raises — useful in test environments where
    we want loud failure."""
    monkeypatch.setattr(ssl_bundle, "_OS_BUNDLE_CANDIDATES", ())
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError("hidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ssl_bundle.CABundleMissingError):
        ssl_bundle.startup_sanity_check(fatal=True)
