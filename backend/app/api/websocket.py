"""Realtime WebSocket connection manager.

Acts as the bridge between the in-process :class:`EventBus` and connected
browser clients. Subscribers can be tagged with topic filters; a message is
fanned out to every connection whose filter matches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logging import get_logger
from app.events.bus import EventBus, get_event_bus
from app.events.types import DomainEvent

log = get_logger("auditarr.ws", category="api")


class WebSocketConnection:
    """A single connected client."""

    def __init__(self, ws: WebSocket, topics: set[str]) -> None:
        self.id = uuid4().hex
        self.ws = ws
        self.topics = topics

    def matches(self, event_name: str) -> bool:
        if not self.topics or "*" in self.topics:
            return True
        # Topic filters are prefix-matches: "media." matches "media.added".
        return any(event_name == t or event_name.startswith(t) for t in self.topics)

    async def send(self, message: dict[str, Any]) -> None:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return
        await self.ws.send_json(message)


class WebSocketManager:
    """Tracks connections and bridges domain events to clients."""

    def __init__(self, event_bus: EventBus) -> None:
        self._connections: dict[str, WebSocketConnection] = {}
        self._lock = asyncio.Lock()
        self._bus = event_bus
        self._unsubscribe: Any = None

    async def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._bus.subscribe(
                EventBus.WILDCARD, self._on_event
            )
            log.info("ws.bus_bridge_started")

    async def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        async with self._lock:
            for conn in list(self._connections.values()):
                try:
                    await conn.ws.close()
                except Exception:  # noqa: BLE001
                    pass
            self._connections.clear()

    async def connect(
        self, websocket: WebSocket, topics: Iterable[str] | None = None
    ) -> WebSocketConnection:
        await websocket.accept()
        conn = WebSocketConnection(websocket, set(topics or ()))
        async with self._lock:
            self._connections[conn.id] = conn
        log.debug("ws.connected", id=conn.id, topics=list(conn.topics))
        return conn

    async def disconnect(self, conn: WebSocketConnection) -> None:
        async with self._lock:
            self._connections.pop(conn.id, None)
        log.debug("ws.disconnected", id=conn.id)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._connections.values())
        for c in targets:
            try:
                await c.send(message)
            except Exception as exc:  # noqa: BLE001
                log.warning("ws.send_failed", id=c.id, error=str(exc))

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def _on_event(self, event: DomainEvent) -> None:
        message = {
            "type": "event",
            "name": event.name,
            "source": event.source,
            "payload": event.payload,
            "occurred_at": event.occurred_at.isoformat(),
            "event_id": event.event_id,
        }
        async with self._lock:
            targets = [c for c in self._connections.values() if c.matches(event.name)]
        for c in targets:
            try:
                await c.send(message)
            except Exception as exc:  # noqa: BLE001
                log.warning("ws.bridge_send_failed", id=c.id, error=str(exc))


_manager: WebSocketManager | None = None


def get_ws_manager() -> WebSocketManager:
    """Return the process-wide websocket manager."""
    global _manager
    if _manager is None:
        _manager = WebSocketManager(get_event_bus())
    return _manager
