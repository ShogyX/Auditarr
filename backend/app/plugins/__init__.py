"""Plugin discovery, validation, and lifecycle management."""

from app.plugins.contracts import (
    Plugin,
    PluginContext,
    PluginManifest,
    PluginType,
)
from app.plugins.loader import PluginLoader, get_plugin_loader

__all__ = [
    "Plugin",
    "PluginContext",
    "PluginLoader",
    "PluginManifest",
    "PluginType",
    "get_plugin_loader",
]
