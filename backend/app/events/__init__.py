"""Domain event bus.

Every important action emits a normalized event. Plugins, automations,
notifications, and the websocket fan-out subscribe through the bus — core
modules never import plugin modules directly.
"""

from app.events.bus import EventBus, get_event_bus
from app.events.types import DomainEvent, EventName

__all__ = ["DomainEvent", "EventBus", "EventName", "get_event_bus"]
