"""Centralized httpx.AsyncClient factory.

Wraps :func:`app.core.ssl_bundle.resolve_ca_bundle` so every
outgoing HTTPS call gets the same verified TLS context. Pre-1.7.2
each integration plugin constructed its own ``httpx.AsyncClient``;
when the host's CA bundle was missing or ``certifi`` was uninstalled,
every plugin failed independently with an opaque
``FileNotFoundError``.

Use ``async_client(...)`` instead of ``httpx.AsyncClient(...)`` in
new code. The factory's defaults are httpx-compatible — it accepts
the same kwargs and returns the same type.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.ssl_bundle import (
    CABundleMissingError,
    resolve_ca_bundle,
)

log = get_logger("auditarr.http", category="system")


def async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` with verified TLS.

    Verify chain:
      * If the caller passes ``verify=False`` we honour it (used by
        a small number of integration tests and explicit-opt-in
        cases where the operator has accepted a self-signed cert
        on the upstream).
      * If the caller passes ``verify=<path>`` or ``verify=<ssl.SSLContext>``
        we honour it.
      * Otherwise we resolve the host CA bundle once via
        :func:`resolve_ca_bundle` and pass that path. This avoids
        httpx's lazy ``certifi`` import (which raises an opaque
        ``FileNotFoundError`` on hosts where certifi isn't
        installed).

    If the CA bundle can't be resolved, we log a structured warning
    AND fall back to ``verify=False``. **This weakens TLS** —
    the operator must fix their deployment to restore full
    certificate verification. The fallback exists because the
    alternative is silent total breakage of every outbound
    integration, which is the failure mode we're trying to escape.

    Operators on healthy hosts pay zero overhead — the bundle path
    is cached after the first call.
    """
    if "verify" not in kwargs:
        try:
            kwargs["verify"] = resolve_ca_bundle()
        except CABundleMissingError as exc:
            log.warning(
                "http.client_verify_disabled",
                detail=(
                    "Falling back to verify=False because no CA "
                    "bundle could be located. Outbound HTTPS will "
                    "still work but certificates will NOT be "
                    "verified — fix the host CA bundle to restore "
                    "verification."
                ),
                error=str(exc),
            )
            kwargs["verify"] = False
    return httpx.AsyncClient(*args, **kwargs)
