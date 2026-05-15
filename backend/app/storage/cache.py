"""Redis client wrapper.

Used by:
* the ARQ queue (Stage 4+),
* websocket pubsub,
* rate limiting,
* cache layer.

Stage 10 (audit follow-up): the user reported "fails to fetch data
after some hours of uptime". A stale Redis TCP connection (idle for
hours, killed by an intermediate load balancer or NAT timeout) used
to leave the singleton in a broken-but-not-replaced state. The
client now auto-reconnects on a transient ``RedisConnectionError``
during ``healthcheck`` and ``enqueue``, with a guard so we don't
hammer the connect loop on a real outage. See ``_reset_clients`` +
``_maybe_reconnect``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Self

import redis.asyncio as redis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

log = get_logger("auditarr.cache", category="queue")

# Stage 10 (audit follow-up): minimum interval between reconnect
# attempts. A continuously-down Redis would otherwise let every
# request fire its own reconnect; this ratelimits the loop so the
# server doesn't melt CPU dialing TCP.
_RECONNECT_COOLDOWN_SECONDS = 5.0


class RedisClient:
    """Thin wrapper around an async Redis connection pool.

    Also exposes :meth:`enqueue` for putting work onto the ARQ job queue
    consumed by :mod:`app.worker`. The ARQ pool is created lazily — most
    requests don't need it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: redis.Redis | None = None
        self._arq: ArqRedis | None = None
        # Stage 10 (audit follow-up): reconnect bookkeeping.
        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_attempt: float = 0.0

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = redis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        log.info("redis.connected", url=self._settings.redis_url)

    async def disconnect(self) -> None:
        if self._arq is not None:
            await self._arq.aclose()
            self._arq = None
        if self._client is not None:
            await self._client.aclose()
            log.info("redis.disconnected")
        self._client = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise ServiceUnavailableError("Redis is not connected")
        return self._client

    async def healthcheck(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except redis.RedisError as exc:
            log.warning("redis.healthcheck_failed", error=str(exc))
            # Stage 10: connection error during healthcheck → try a
            # reconnect so the next call has a fresh pool.
            await self._maybe_reconnect(exc)
            return False

    async def _reset_clients(self) -> None:
        """Tear down the current redis + arq clients so the next call
        rebuilds them. Tolerates failures during teardown — the goal
        is to drop the broken state, not to gracefully close it."""
        if self._arq is not None:
            try:
                await self._arq.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._arq = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def _maybe_reconnect(self, exc: Exception) -> None:
        """Attempt to reconnect, ratelimited by ``_RECONNECT_COOLDOWN_SECONDS``.

        Called from request paths that observed a connection-level
        Redis error. The lock + cooldown prevents concurrent callers
        from dialing the server in parallel; the first call to enter
        the lock does the work, subsequent calls see the recent
        attempt timestamp and skip.
        """
        async with self._reconnect_lock:
            now = time.monotonic()
            if now - self._last_reconnect_attempt < _RECONNECT_COOLDOWN_SECONDS:
                return
            self._last_reconnect_attempt = now
            log.warning(
                "redis.reconnecting",
                trigger=type(exc).__name__,
                error=str(exc),
            )
            await self._reset_clients()
            try:
                await self.connect()
            except Exception as connect_exc:  # noqa: BLE001
                # Don't propagate — the next request will hit the
                # cooldown and try again. The original caller already
                # saw the original failure and acts accordingly.
                log.warning(
                    "redis.reconnect_failed",
                    error=str(connect_exc),
                )

    # ── ARQ queue ────────────────────────────────────────────
    async def _arq_pool(self) -> ArqRedis:
        if self._arq is None:
            self._arq = await create_pool(
                RedisSettings.from_dsn(self._settings.redis_url)
            )
        return self._arq

    async def enqueue(
        self, function: str, *args: Any, **kwargs: Any
    ) -> str | None:
        """Put a job onto the ARQ queue. Returns the job id."""
        # Stage 10 (audit follow-up): retry once on a transient
        # connection error after a forced reconnect. Anything other
        # than a connection error bubbles immediately — the caller
        # decides whether to retry application-level errors.
        try:
            pool = await self._arq_pool()
            job = await pool.enqueue_job(function, *args, **kwargs)
        except redis.ConnectionError as exc:
            await self._maybe_reconnect(exc)
            # Single retry: rebuild the ARQ pool through the fresh
            # client and try once more. If this also fails, raise.
            pool = await self._arq_pool()
            job = await pool.enqueue_job(function, *args, **kwargs)
        return job.job_id if job is not None else None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


_redis: RedisClient | None = None


def get_redis() -> RedisClient:
    """Return the process-wide Redis singleton."""
    global _redis
    if _redis is None:
        _redis = RedisClient(get_settings())
    return _redis


def reset_redis() -> None:
    """Test helper — drop the cached singleton."""
    global _redis
    _redis = None
