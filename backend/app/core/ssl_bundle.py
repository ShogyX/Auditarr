"""SSL CA-bundle resolution with deployment-friendly fallbacks.

Background
==========

A v1.7.0 production deployment surfaced ``FileNotFoundError: [Errno
2] No such file or directory`` from every outgoing HTTPS call made
by the worker process. The traceback originated in
``ssl.create_default_context().load_verify_locations()`` — Python's
stdlib was being told to load a CA bundle file that didn't exist.

Root cause: ``certifi`` was not installed in the venv, but
``httpx`` imports it lazily on first ``AsyncClient`` construction
to discover the default CA path. When ``certifi`` is missing AND
the OS bundle is in a non-default location, the system-wide
discovery falls back to a hardcoded path that doesn't exist on
this host, and SSL setup fails before any HTTP call leaves the
process.

This module makes the application resilient to that misconfig:

1. Try ``certifi.where()`` (the preferred path, used by httpx
   internally on healthy installs).
2. Fall back to a list of common OS CA-bundle paths (Debian/
   Ubuntu, RHEL/CentOS, Alpine, macOS Homebrew).
3. If nothing is found, raise ``CABundleMissingError`` with a
   diagnostic that names every path we tried and the env vars
   the operator should check.

The resolved path is cached for the lifetime of the process so
the discovery cost is paid exactly once.

Use ``resolve_ca_bundle()`` to get a path you can pass to
``httpx.AsyncClient(verify=...)``. Use ``build_ssl_context()`` for
``ssl.SSLContext`` directly. Use ``startup_sanity_check()`` at app
boot to fail loudly (or warn loudly) if no bundle is found, rather
than letting the failure surface later via opaque tracebacks.
"""

from __future__ import annotations

import os
import ssl
import threading
from pathlib import Path

from app.core.logging import get_logger

log = get_logger("auditarr.ssl", category="system")


class CABundleMissingError(RuntimeError):
    """Raised when no usable CA bundle can be located on the host.

    The exception message lists every path the resolver tried, so
    the operator can fix the deployment without spelunking the
    source.
    """


# Common OS bundle paths in priority order. The list is curated to
# cover the distros we ship installer scripts for plus a couple of
# others people reasonably ask about.
_OS_BUNDLE_CANDIDATES: tuple[str, ...] = (
    # Debian / Ubuntu (ca-certificates package).
    "/etc/ssl/certs/ca-certificates.crt",
    # RHEL / CentOS / Fedora / openSUSE.
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    # Alpine.
    "/etc/ssl/cert.pem",
    # FreeBSD (some operators run there).
    "/usr/local/share/certs/ca-root-nss.crt",
    # macOS via Homebrew (mostly dev environments).
    "/usr/local/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
)


# Module-level cache + lock so concurrent first-callers don't all
# do the filesystem walk.
_resolved_path: str | None = None
_resolved_lock = threading.Lock()
_resolve_attempted = False


def _try_certifi() -> str | None:
    """Return ``certifi.where()`` if certifi is importable AND the
    file it points at actually exists; otherwise None."""
    try:
        import certifi  # noqa: PLC0415  (deliberately lazy)
    except ImportError:
        log.info(
            "ssl.certifi_missing",
            detail=(
                "certifi is not installed. "
                "Add 'certifi>=2024.0' to your venv (pip install certifi) "
                "or fall back to the OS CA bundle."
            ),
        )
        return None
    where = certifi.where()
    if Path(where).is_file():
        return where
    log.warning(
        "ssl.certifi_path_missing",
        path=where,
        detail=(
            "certifi.where() returned a path that does not exist. "
            "This usually means the certifi package was reinstalled "
            "with --no-binary or the wheel was extracted partially. "
            "Falling back to the OS CA bundle."
        ),
    )
    return None


def _try_env_var() -> str | None:
    """Honour the standard CA-bundle env vars before guessing.

    Operators with custom corporate CAs typically set one of these
    in their service environment; we should pick them up the same
    way Python's ssl module would.
    """
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = os.environ.get(var)
        if value and Path(value).is_file():
            log.info("ssl.using_env_bundle", env_var=var, path=value)
            return value
        if value:
            log.warning(
                "ssl.env_bundle_missing",
                env_var=var,
                path=value,
                detail=(
                    f"{var} is set but the path does not exist. "
                    "Falling through to other candidates."
                ),
            )
    return None


def _try_os_candidates() -> str | None:
    for candidate in _OS_BUNDLE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def resolve_ca_bundle() -> str:
    """Return a path to a CA bundle that exists on the host.

    Resolution order:
      1. ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE``
         env var, if set and the path exists.
      2. ``certifi.where()``, if certifi is installed AND its
         bundle file exists.
      3. Common OS bundle paths in priority order.

    Raises:
        CABundleMissingError: when none of the above turn up a
            usable file. The exception message lists every path
            tried so the operator can fix their deployment.
    """
    global _resolved_path, _resolve_attempted

    if _resolved_path is not None:
        return _resolved_path

    with _resolved_lock:
        if _resolved_path is not None:
            return _resolved_path

        for resolver in (_try_env_var, _try_certifi, _try_os_candidates):
            path = resolver()
            if path is not None:
                _resolved_path = path
                _resolve_attempted = True
                log.info("ssl.ca_bundle_resolved", path=path)
                return path

        _resolve_attempted = True
        tried_env_vars = ", ".join(
            f"{v}={os.environ.get(v) or '<unset>'}"
            for v in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
        )
        tried_os = ", ".join(_OS_BUNDLE_CANDIDATES)
        msg = (
            "No CA bundle found on this host. Outbound HTTPS calls "
            "will fail until one of the following is fixed:\n"
            f"  - env vars: {tried_env_vars}\n"
            "  - certifi package: not installed OR cacert.pem missing "
            "(install with `pip install certifi` in the venv)\n"
            f"  - OS bundle candidates checked: {tried_os}\n"
            "Quick fix for Debian/Ubuntu: "
            "`sudo apt-get install --reinstall ca-certificates && "
            "sudo update-ca-certificates`."
        )
        log.error("ssl.ca_bundle_unresolvable", detail=msg)
        raise CABundleMissingError(msg)


def build_ssl_context() -> ssl.SSLContext:
    """Return an SSLContext suitable for verifying outgoing HTTPS.

    Uses the bundle resolved by :func:`resolve_ca_bundle`. Equivalent
    to ``ssl.create_default_context(cafile=resolve_ca_bundle())``
    but with a single resolution code path for the whole app.
    """
    path = resolve_ca_bundle()
    return ssl.create_default_context(cafile=path)


def startup_sanity_check(*, fatal: bool = False) -> bool:
    """Probe CA bundle availability at app boot.

    Call this from the API and worker startup hooks so the
    operator finds out about a broken deployment IMMEDIATELY,
    not when their first scheduled poll fires several minutes
    later.

    Args:
        fatal: If True, re-raise :class:`CABundleMissingError`
            after logging. If False (the default), the function
            returns ``False`` on failure so the app can keep
            running for non-HTTPS workflows.

    Returns:
        ``True`` when a bundle was located; ``False`` otherwise.
    """
    try:
        resolve_ca_bundle()
    except CABundleMissingError as exc:
        log.error(
            "ssl.startup_sanity_check_failed",
            detail=str(exc),
            fatal=fatal,
        )
        if fatal:
            raise
        return False
    return True


def reset_cache_for_tests() -> None:
    """Test hook — clears the resolved-path cache so a test can
    monkeypatch the filesystem / env and re-resolve."""
    global _resolved_path, _resolve_attempted
    with _resolved_lock:
        _resolved_path = None
        _resolve_attempted = False
