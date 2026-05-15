"""In-process rate limiter for auth endpoints.

A sliding-window counter keyed by ``(ip, route)`` lives in
``RateLimiter._buckets`` and gets pruned lazily. The limiter is
deliberately in-process and not Redis-backed: rate-limiting per-process
is enough to slow a brute forcer to a crawl against argon2id, and
multi-replica deployments are out of scope for the self-hosted target.

Configured via :attr:`Settings.auth_rate_limit_attempts` /
``auth_rate_limit_window_seconds``. Operators who want to disable rate
limiting entirely can set ``auth_rate_limit_attempts=0``.

Usage from a route::

    @router.post("/login")
    async def login(..., request: Request) -> ...:
        await get_rate_limiter().check(request, "login")
        ...

The ``check`` raises :class:`RateLimitedError` (a 429) when over the
limit and records the attempt otherwise.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Final

from fastapi import Request

from app.core.exceptions import AuditarrError
from app.core.logging import get_logger
from app.core.settings import get_settings

log = get_logger("auditarr.security.ratelimit", category="security")


class RateLimitedError(AuditarrError):
    """Raised when a caller has exceeded the auth rate limit."""

    status_code: Final[int] = 429
    code: Final[str] = "rate_limited"


class RateLimiter:
    def __init__(self) -> None:
        # ``_buckets[(ip, route)]`` -> deque of monotonic timestamps.
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        # ``request.client`` is populated by uvicorn; the test client
        # may leave it None. Reverse proxies should rewrite the upstream
        # IP into the connection itself (X-Forwarded-For is fragile and
        # we don't trust it here).
        if request.client is None:
            return "unknown"
        return request.client.host or "unknown"

    async def check(self, request: Request, route: str) -> None:
        """Record an attempt and raise if the bucket is over the limit."""
        settings = get_settings()
        attempts = settings.auth_rate_limit_attempts
        window = settings.auth_rate_limit_window_seconds
        if attempts <= 0:
            return  # disabled

        ip = self._client_ip(request)
        key = (ip, route)
        now = time.monotonic()
        bucket = self._buckets[key]

        # Evict timestamps that fell out of the window.
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= attempts:
            # Compute the seconds until the oldest entry ages out, so the
            # caller can show a "try again in N seconds" message.
            retry_after = max(1, int(bucket[0] + window - now))
            log.warning(
                "security.rate_limited",
                ip=ip,
                route=route,
                attempts=len(bucket),
                retry_after=retry_after,
            )
            raise RateLimitedError(
                f"Too many attempts. Try again in {retry_after}s.",
                details={"retry_after": retry_after, "route": route},
            )

        bucket.append(now)

    def reset(self) -> None:
        """Test helper: clear all buckets."""
        self._buckets.clear()


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    """Test helper: drop the singleton."""
    global _limiter
    _limiter = None


__all__ = [
    "RateLimitedError",
    "RateLimiter",
    "get_rate_limiter",
    "reset_rate_limiter",
]
