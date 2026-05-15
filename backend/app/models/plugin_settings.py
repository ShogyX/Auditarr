"""Plugin settings.

Each plugin can declare a Pydantic settings schema via
:attr:`Plugin.settings_schema`. The host stores the validated config
in this table, namespaced by plugin id.

This is distinct from the :class:`Integration` model: integrations
configure a *running instance* of a connector (one Plex server, one
Sonarr), and a single plugin (e.g. ``plex``) may back many integrations.
Plugin settings, by contrast, are per-plugin (e.g. a media-fingerprint
plugin's "minimum length to bother fingerprinting").

The values column is JSON; encryption is not done here because plugin
settings should not carry secrets. Plugins needing secrets should
register an integration (which has encrypted secrets) or use environment
variables.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class PluginSettings(Base, TimestampMixin):
    __tablename__ = "plugin_settings"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Manifest id of the owning plugin. Plugin ids are validated as
    # ``[a-z][a-z0-9-]{1,47}`` by the SDK, so the column width is safe.
    plugin_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    # Validated against ``Plugin.settings_schema`` at write time. The
    # service code keeps reads cheap by skipping re-validation; if a
    # plugin ships an incompatible schema upgrade it's the operator's
    # responsibility to migrate the row.
    values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Free-text operator note. Visible in the admin UI; not used by the
    # plugin itself.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
