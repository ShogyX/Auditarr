"""Integration framework.

Stage 5 deliverable: pluggable connectors for upstream services (Plex,
Sonarr, Radarr, etc.) with healthcheck, library discovery, and tag sync.
"""

from app.integrations.manager import IntegrationManager
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)

__all__ = [
    "DiscoveredLibrary",
    "HealthReport",
    "IntegrationConfig",
    "IntegrationManager",
    "IntegrationProvider",
    "TagSync",
]
