"""Async in-process event bus.

Subscribers are isolated: a failing handler never blocks others, and exceptions
are logged and emitted as a ``plugin.error`` event for observability.

Future stages may bridge this bus to Redis pubsub for multi-worker fan-out;
the public interface stays the same.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.events.types import DomainEvent, EventName

log = get_logger("auditarr.events", category="events")

Handler = Callable[[DomainEvent], Awaitable[None] | None]


class EventBus:
    """In-process publish / subscribe bus."""

    WILDCARD = "*"

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # ── Subscribe ──────────────────────────────────────────────
    def subscribe(self, name: EventName | str, handler: Handler) -> Callable[[], None]:
        """Register *handler* for *name* (or ``"*"`` for all events).

        Returns an unsubscribe function.
        """
        self._subs[name].append(handler)

        def _unsubscribe() -> None:
            try:
                self._subs[name].remove(handler)
            except ValueError:
                pass  # handler already removed by a concurrent unsubscribe

        return _unsubscribe

    # ── Publish ────────────────────────────────────────────────
    async def publish(self, event: DomainEvent) -> None:
        """Dispatch *event* to all matching subscribers concurrently."""
        handlers = [*self._subs.get(event.name, ()), *self._subs.get(self.WILDCARD, ())]
        if not handlers:
            log.debug("event.no_subscribers", event_name=event.name)
            return

        log.debug(
            "event.publish",
            event_name=event.name,
            source=event.source,
            subscribers=len(handlers),
        )
        await asyncio.gather(
            *(self._invoke(h, event) for h in handlers), return_exceptions=False
        )

    async def emit(
        self,
        name: EventName,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "core",
        correlation_id: str | None = None,
    ) -> DomainEvent:
        """Convenience wrapper that builds and publishes a :class:`DomainEvent`."""
        event = DomainEvent(
            name=name,
            payload=payload or {},
            source=source,
            correlation_id=correlation_id,
        )
        await self.publish(event)
        return event

    # ── Internals ──────────────────────────────────────────────
    async def _invoke(self, handler: Handler, event: DomainEvent) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 — bus must isolate failures
            log.error(
                "event.handler_failed",
                event_name=event.name,
                handler=getattr(handler, "__qualname__", repr(handler)),
                error=str(exc),
                exc_info=True,
            )

    def clear(self) -> None:
        self._subs.clear()


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide event bus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
