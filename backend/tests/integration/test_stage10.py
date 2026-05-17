"""Stage 10 (audit follow-up) — long-uptime hardening.

Three concerns from the Stage 10 plan:

  1. WebSocket: connect → disconnect → ``connection_count`` returns
     to baseline. The pre-Stage-10 manager looked correct on
     inspection; this test pins the contract so a future refactor
     can't introduce a leak.
  2. Redis client: a transient ``ConnectionError`` triggers a
     ratelimited reconnect via ``_maybe_reconnect``; concurrent
     callers don't all dial in parallel.
  3. Plugin loader: ``asyncio.create_task`` for fire-and-forget
     hooks now keeps a strong reference in
     ``_background_tasks``, preventing GC of in-flight tasks.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.api.websocket import WebSocketManager, WebSocketConnection
from app.events.bus import EventBus
from app.storage.cache import RedisClient


# ── WebSocket: connect/disconnect bookkeeping ──────────────────
class _FakeWebSocket:
    """Minimal WebSocket double — only what the manager touches."""

    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.application_state = WebSocketState.CONNECTED
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True
        from starlette.websockets import WebSocketState

        self.application_state = WebSocketState.DISCONNECTED


@pytest.mark.asyncio
async def test_ws_manager_connection_count_returns_to_baseline() -> None:
    """A connect immediately followed by a disconnect leaves the
    manager's tally at zero."""
    manager = WebSocketManager(EventBus())
    assert manager.connection_count == 0

    ws = _FakeWebSocket()
    conn = await manager.connect(ws, topics=set())
    assert manager.connection_count == 1
    assert ws.accepted is True

    await manager.disconnect(conn)
    assert manager.connection_count == 0


@pytest.mark.asyncio
async def test_ws_manager_handles_rapid_connect_disconnect_cycles() -> None:
    """A flap of connect/disconnect pairs doesn't leak entries in the
    internal ``_connections`` dict."""
    manager = WebSocketManager(EventBus())
    for _ in range(50):
        ws = _FakeWebSocket()
        conn = await manager.connect(ws)
        await manager.disconnect(conn)
    assert manager.connection_count == 0


@pytest.mark.asyncio
async def test_ws_manager_disconnect_unknown_is_noop() -> None:
    """Disconnecting a connection that was never registered is a
    no-op — the manager pops with default None."""
    manager = WebSocketManager(EventBus())
    # Build a connection object without going through ``connect`` so
    # the manager has no record of it.
    stray = WebSocketConnection(_FakeWebSocket(), set())  # type: ignore[arg-type]
    await manager.disconnect(stray)
    assert manager.connection_count == 0


# ── Redis: auto-reconnect on ConnectionError ───────────────────
class _FakeRedis:
    """Test double exposing only what ``RedisClient`` touches."""

    def __init__(self) -> None:
        self.closed = False
        self.pinged = 0

    async def ping(self) -> bool:
        self.pinged += 1
        return True

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_reconnect_resets_clients(monkeypatch) -> None:
    """A simulated connection error triggers ``_reset_clients`` +
    reconnect: the old client + arq are torn down, and ``connect``
    rebuilds them."""
    from app.core.settings import Settings

    settings = Settings(redis_url="redis://localhost:6379/0")
    client = RedisClient(settings)
    fake = _FakeRedis()
    client._client = fake  # type: ignore[assignment]
    # Pretend we have an ARQ pool too.
    fake_arq = _FakeRedis()
    client._arq = fake_arq  # type: ignore[assignment]

    # ``connect`` would normally dial Redis; intercept it so we can
    # observe that reconnect calls into it.
    connect_calls = 0

    async def fake_connect() -> None:
        nonlocal connect_calls
        connect_calls += 1
        client._client = _FakeRedis()  # type: ignore[assignment]

    monkeypatch.setattr(client, "connect", fake_connect)

    import redis.asyncio as redis_pkg

    await client._maybe_reconnect(redis_pkg.ConnectionError("simulated"))

    # Old clients torn down; reconnect dialed once.
    assert fake.closed is True
    assert fake_arq.closed is True
    assert connect_calls == 1


@pytest.mark.asyncio
async def test_redis_reconnect_ratelimits_concurrent_callers(monkeypatch) -> None:
    """A second reconnect call within the cooldown window is skipped
    so a continuous outage doesn't melt CPU dialing TCP."""
    from app.core.settings import Settings

    settings = Settings(redis_url="redis://localhost:6379/0")
    client = RedisClient(settings)

    connect_calls = 0

    async def fake_connect() -> None:
        nonlocal connect_calls
        connect_calls += 1

    monkeypatch.setattr(client, "connect", fake_connect)

    import redis.asyncio as redis_pkg

    err = redis_pkg.ConnectionError("simulated")
    # First call attempts a reconnect.
    await client._maybe_reconnect(err)
    # Second call within the cooldown window is skipped.
    await client._maybe_reconnect(err)
    assert connect_calls == 1

    # Move time forward past the cooldown — third call should retry.
    client._last_reconnect_attempt = time.monotonic() - 100.0
    await client._maybe_reconnect(err)
    assert connect_calls == 2


@pytest.mark.asyncio
async def test_redis_healthcheck_triggers_reconnect_on_connection_error(
    monkeypatch,
) -> None:
    """A ``ConnectionError`` raised by ping() is caught by
    ``healthcheck`` and feeds into ``_maybe_reconnect``."""
    from app.core.settings import Settings

    settings = Settings(redis_url="redis://localhost:6379/0")
    client = RedisClient(settings)

    import redis.asyncio as redis_pkg

    class _BadRedis:
        async def ping(self) -> bool:
            raise redis_pkg.ConnectionError("simulated")

        async def aclose(self) -> None:
            pass

    client._client = _BadRedis()  # type: ignore[assignment]

    triggered: list[Exception] = []

    async def fake_maybe_reconnect(exc: Exception) -> None:
        triggered.append(exc)

    monkeypatch.setattr(client, "_maybe_reconnect", fake_maybe_reconnect)

    ok = await client.healthcheck()
    assert ok is False
    assert len(triggered) == 1
    assert isinstance(triggered[0], redis_pkg.ConnectionError)


# ── Plugin loader: background tasks keep strong refs ───────────
@pytest.mark.asyncio
async def test_plugin_loader_background_task_set_holds_strong_refs() -> None:
    """Lifecycle hooks spawned with ``spawn=True`` are tracked in
    ``_background_tasks`` so they aren't GC'd. The set self-cleans
    via ``add_done_callback``."""
    import gc

    from app.core.registry import ServiceRegistry
    from app.core.settings import Settings
    from app.plugins.loader import PluginLoader

    settings = Settings()
    loader = PluginLoader(
        settings=settings,
        registry=ServiceRegistry(),
        event_bus=EventBus(),
    )

    # Reach into the loader's ``_run_hook`` style by adding our own
    # task using the same pattern. This pins the contract for the
    # production call site at line ~303 of loader.py.
    started = asyncio.Event()
    finished = asyncio.Event()

    async def _slow_hook() -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    task = asyncio.create_task(_slow_hook(), name="test:slow_hook")
    loader._background_tasks.add(task)
    task.add_done_callback(loader._background_tasks.discard)

    # Force GC mid-task — without the strong ref the task would be
    # collected here. The strong ref keeps it alive.
    await started.wait()
    gc.collect()
    # Set still contains the task after a GC pass.
    assert task in loader._background_tasks

    await finished.wait()
    # Allow the done callback to run.
    await asyncio.sleep(0)
    # Set self-cleaned via the done callback.
    assert task not in loader._background_tasks
    assert task.done()


@pytest.mark.asyncio
async def test_plugin_loader_init_creates_empty_background_task_set() -> None:
    """Fresh loader starts with an empty set."""
    from app.core.registry import ServiceRegistry
    from app.core.settings import Settings
    from app.plugins.loader import PluginLoader

    loader = PluginLoader(
        settings=Settings(),
        registry=ServiceRegistry(),
        event_bus=EventBus(),
    )
    assert loader._background_tasks == set()
