"""HTTP middleware: request id, structured access logging, security headers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.logging import get_logger
from app.core.settings import Settings

log = get_logger("auditarr.api.access", category="api")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request id to every log line emitted during the request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            structlog.contextvars.unbind_contextvars(
                "request_id", "method", "path"
            )

        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{duration_ms:.2f}"
        log.info(
            "api.request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set defensive HTTP headers on every response.

    Stage 13 hardening:

    * **HSTS** — only in production (avoids breaking ``http://localhost``
      development).
    * **CSP** — restrictive default but allows the Vite-bundled inline
      scripts the SPA needs. The frontend ships hashes for its inline
      bootstrap; live-updating CSP at runtime is out of scope.
    * **X-Frame-Options DENY** — clickjacking defence; ``frame-ancestors``
      in the CSP duplicates this for modern browsers.
    * **X-Content-Type-Options nosniff** — prevent IE-style MIME sniffing.
    * **Referrer-Policy no-referrer** — outgoing links never leak the
      Auditarr URL.
    * **Permissions-Policy** — explicitly disable powerful features the
      app doesn't use.

    The headers use ``setdefault`` so handlers can override per-route
    (e.g. an artifact-download endpoint loosening CSP for blob URIs).
    """

    def __init__(self, app, *, is_production: bool) -> None:
        super().__init__(app)
        self._is_production = is_production

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        h = response.headers
        h.setdefault("x-content-type-options", "nosniff")
        h.setdefault("x-frame-options", "DENY")
        h.setdefault("referrer-policy", "no-referrer")
        h.setdefault(
            "permissions-policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()",
        )
        # CSP: same-origin everything, plus ``unsafe-inline`` styles for
        # Vite's CSS-in-JS at build time. Inline scripts are NOT allowed
        # — the SPA's bootstrap script is served from /assets.
        h.setdefault(
            "content-security-policy",
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        if self._is_production:
            # 1-year max-age, includeSubDomains, preload-ready. Only set
            # in production so localhost development isn't pinned to HTTPS
            # forever by an accidental dev-mode visit.
            h.setdefault(
                "strict-transport-security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """Attach the standard middleware stack."""
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware, is_production=settings.is_production
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id", "x-response-time-ms"],
    )
