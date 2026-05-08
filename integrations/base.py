"""
integrations/base.py — Base class for all third-party integrations.

Each plugin subclass implements:
  - test_connection() -> (ok: bool, message: str)
  - sync()            -> (linked_count: int, message: str)   [optional]
  - parse_webhook()   -> {kind, event_type, file_paths}      [optional]

Plugins register themselves in the REGISTRY dict.
"""
from abc import ABC, abstractmethod


class Integration(ABC):
    """Abstract base for all integration plugins."""

    KIND: str = ""           # 'sonarr' | 'radarr' | 'plex' | 'jellyfin' | 'tdarr' | 'bazarr'
    DISPLAY_NAME: str = ""   # 'Sonarr' | etc.
    SUPPORTS_SYNC: bool = False
    SUPPORTS_WEBHOOK: bool = False
    SUPPORTS_AUTOMATION: bool = False
    DESCRIPTION: str = ""

    def __init__(self, server: dict):
        self.server = server
        self.id = server.get("id")
        self.name = server.get("name", "")
        self.base_url = (server.get("base_url") or "").rstrip("/")
        self.api_key = server.get("api_key", "")
        self.options = server.get("options") or {}

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Return (ok, message) — used by the Settings UI 'Test' button."""
        ...

    def sync(self) -> tuple[int, str]:
        """Pull library state and link to existing files. Default: not implemented."""
        return 0, "Sync not implemented for this integration"

    def parse_webhook(self, payload: dict) -> dict:
        """Default — return a minimal envelope."""
        return {"kind": self.KIND, "event_type": payload.get("eventType", "Unknown"), "file_paths": []}


# ─── Plugin registry ──────────────────────────────────────────────────────────
REGISTRY: dict[str, type[Integration]] = {}


def register(cls: type[Integration]):
    """Decorator to register an integration class."""
    REGISTRY[cls.KIND] = cls
    return cls


def get_plugin(kind: str) -> type[Integration] | None:
    return REGISTRY.get(kind)


def all_plugins() -> list[dict]:
    """Return the list of available integration plugins for the UI."""
    return [
        {
            "kind": cls.KIND,
            "display_name": cls.DISPLAY_NAME,
            "supports_sync": cls.SUPPORTS_SYNC,
            "supports_webhook": cls.SUPPORTS_WEBHOOK,
            "supports_automation": cls.SUPPORTS_AUTOMATION,
            "description": cls.DESCRIPTION,
        }
        for cls in REGISTRY.values()
    ]
