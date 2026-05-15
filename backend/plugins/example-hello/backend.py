"""Reference plugin.

Demonstrates the full plugin SDK surface: route registration, capability
registration, lifecycle hooks, and event subscription.
"""

from __future__ import annotations

from app.plugins import Plugin, PluginContext
from app.events.types import DomainEvent


class HelloProvider:
    """A trivial capability provider."""

    name = "hello"

    def greet(self, who: str = "world") -> str:
        return f"hello, {who}"


class HelloPlugin(Plugin):
    async def on_load(self) -> None:
        self.context.logger().info("example.loaded", greeting="hello, auditarr")

    async def on_unload(self) -> None:
        self.context.logger().info("example.unloaded")


def register(context: PluginContext) -> Plugin:
    @context.router.get("/hello")
    async def hello(name: str = "world") -> dict[str, str]:
        return {"message": HelloProvider().greet(name)}

    context.register_capability("example.hello", HelloProvider())

    async def _on_startup(event: DomainEvent) -> None:
        context.logger().info("example.observed_startup", payload=event.payload)

    context.events.subscribe("system.startup", _on_startup)

    return HelloPlugin(context)
