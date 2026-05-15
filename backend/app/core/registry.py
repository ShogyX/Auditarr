"""Service registry.

A typed container for singletons and capability-keyed services. Plugins and
integrations register implementations; core code resolves them by capability
key (never by integration name).
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import RLock
from typing import Any, TypeVar, cast

from app.core.exceptions import ConfigurationError

T = TypeVar("T")


class ServiceRegistry:
    """Thread-safe service container.

    Two namespaces are kept side-by-side:

    * ``services`` — singletons keyed by class or string token.
    * ``capabilities`` — many providers per capability key, useful for the
      plugin system (``media.scan``, ``optimization.execute``, etc.).
    """

    def __init__(self) -> None:
        self._services: dict[Any, Any] = {}
        self._capabilities: dict[str, list[Any]] = {}
        self._lock = RLock()

    # ── Services ────────────────────────────────────────────────
    def register(self, key: Any, instance: Any, *, replace: bool = False) -> None:
        with self._lock:
            if not replace and key in self._services:
                raise ConfigurationError(
                    f"Service already registered: {key!r}",
                    details={"key": str(key)},
                )
            self._services[key] = instance

    def get(self, key: type[T] | str) -> T:
        try:
            return cast(T, self._services[key])
        except KeyError as exc:
            raise ConfigurationError(
                f"Service not registered: {key!r}",
                details={"key": str(key)},
            ) from exc

    def try_get(self, key: type[T] | str) -> T | None:
        return cast("T | None", self._services.get(key))

    # ── Capabilities ────────────────────────────────────────────
    def register_capability(self, capability: str, provider: Any) -> None:
        with self._lock:
            self._capabilities.setdefault(capability, []).append(provider)

    def providers_for(self, capability: str) -> list[Any]:
        return list(self._capabilities.get(capability, ()))

    def capabilities(self) -> Iterator[str]:
        return iter(self._capabilities.keys())

    # ── Lifecycle ───────────────────────────────────────────────
    def clear(self) -> None:
        with self._lock:
            self._services.clear()
            self._capabilities.clear()


_registry = ServiceRegistry()


def get_registry() -> ServiceRegistry:
    """Return the process-wide service registry."""
    return _registry
