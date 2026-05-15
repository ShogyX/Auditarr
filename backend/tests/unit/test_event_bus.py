"""Event bus tests."""

from __future__ import annotations

import asyncio

import pytest

from app.events.bus import EventBus
from app.events.types import DomainEvent


@pytest.mark.asyncio
async def test_subscribe_and_publish_calls_handler() -> None:
    bus = EventBus()
    received: list[DomainEvent] = []

    async def h(event: DomainEvent) -> None:
        received.append(event)

    bus.subscribe("media.added", h)
    await bus.emit("media.added", {"id": 1})
    assert len(received) == 1
    assert received[0].payload == {"id": 1}


@pytest.mark.asyncio
async def test_wildcard_subscriber_sees_everything() -> None:
    bus = EventBus()
    seen: list[str] = []

    bus.subscribe(EventBus.WILDCARD, lambda e: seen.append(e.name))
    await bus.emit("scan.started")
    await bus.emit("scan.completed")
    assert seen == ["scan.started", "scan.completed"]


@pytest.mark.asyncio
async def test_handler_failure_does_not_block_others() -> None:
    bus = EventBus()
    success: list[int] = []

    async def good(_: DomainEvent) -> None:
        success.append(1)

    async def bad(_: DomainEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe("rule.matched", bad)
    bus.subscribe("rule.matched", good)
    await bus.emit("rule.matched")
    assert success == [1]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    seen = 0

    def h(_: DomainEvent) -> None:
        nonlocal seen
        seen += 1

    unsub = bus.subscribe("system.startup", h)
    await bus.emit("system.startup")
    unsub()
    await bus.emit("system.startup")
    # Allow scheduled callbacks to drain.
    await asyncio.sleep(0)
    assert seen == 1
