"""Plugin API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Settings ─────────────────────────────────────────────────────
class PluginSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plugin_id: str
    values: dict[str, Any]
    notes: str | None
    created_at: _dt.datetime
    updated_at: _dt.datetime


class PluginSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=2000)


class PluginSettingsSchema(BaseModel):
    """The Pydantic JSON Schema a plugin declares, or ``None`` if it didn't."""

    plugin_id: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    # Defaults the plugin's settings_schema would produce — used by the
    # UI to pre-fill the form for plugins that haven't been configured.
    defaults: dict[str, Any] | None = None


# ── Gallery ──────────────────────────────────────────────────────
class GalleryPluginEntry(BaseModel):
    """One row in the operator-facing plugin directory."""

    id: str
    name: str
    description: str | None = None
    author: str | None = None
    version: str | None = None
    source_url: str | None = None
    install_url: str | None = None
    install_instructions: str | None = None
    categories: list[str] = Field(default_factory=list)
    installed: bool = False


class GalleryFetchResult(BaseModel):
    """Combined gallery response: feed contents + ``installed`` annotation."""

    ok: bool
    feed_url: str
    plugins: list[GalleryPluginEntry] = Field(default_factory=list)
    detail: str | None = None
