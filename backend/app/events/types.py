"""Domain event types and canonical event names."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# The canonical, dotted-lowercase event vocabulary. Plugins MUST use names
# from here or register their own under a plugin-prefixed namespace.
EVENT_NAMES: Final[tuple[str, ...]] = (
    # media
    "media.detected",
    "media.added",
    "media.updated",
    "media.deleted",
    # scans
    "scan.started",
    "scan.completed",
    "scan.failed",
    # rules
    "rule.matched",
    "rule.triggered",
    "rule.failed",
    # optimization
    "optimization.started",
    "optimization.progress",
    "optimization.completed",
    "optimization.failed",
    # notifications
    "notification.sent",
    "notification.failed",
    # integrations
    "integration.sync.completed",
    "integration.unhealthy",
    # updates
    "update.available",
    "update.installed",
    "update.failed",
    # system
    "system.startup",
    "system.shutdown",
    # plugins
    "plugin.loaded",
    "plugin.unloaded",
    "plugin.error",
)

EventName = str  # alias kept for typed signatures; validated at publish time


class DomainEvent(BaseModel):
    """A normalized event emitted by any subsystem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: EventName
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="core", description="emitter identifier")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: _dt.datetime = Field(
        default_factory=lambda: _dt.datetime.now(_dt.UTC)
    )
    correlation_id: str | None = None

    def is_known(self) -> bool:
        return self.name in EVENT_NAMES or "." in self.name
