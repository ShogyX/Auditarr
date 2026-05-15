"""Plugin settings service.

Sits between the API layer and the :class:`PluginSettingsRepository`,
applying the plugin's declared :attr:`Plugin.settings_schema` (if any)
on every write. Reads are returned as-is so a schema upgrade in the
plugin's code does not lock the operator out of their existing values
— the API can render whatever shape is currently on disk and let the
operator fix mismatches manually.

For plugins that don't declare a schema, writes accept any dict shape.
This keeps the SDK opt-in: a quick prototype plugin can persist
configuration without writing a Pydantic model first.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.plugin_settings import PluginSettings
from app.plugins.loader import get_plugin_loader
from app.services.repositories import PluginSettingsRepository


class PluginSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PluginSettingsRepository(session)
        self._loader = get_plugin_loader()

    # ── Schema introspection ────────────────────────────────────
    def schema_for(self, plugin_id: str) -> dict[str, Any] | None:
        """Return the JSON Schema for a plugin's settings, if declared."""
        loaded = self._loader.plugins.get(plugin_id)
        if loaded is None or loaded.instance is None:
            return None
        schema_cls = getattr(loaded.instance, "settings_schema", None)
        if schema_cls is None or not isinstance(schema_cls, type):
            return None
        if not issubclass(schema_cls, BaseModel):
            return None
        # Pydantic v2's ``model_json_schema`` is enough for the UI.
        return schema_cls.model_json_schema()

    # ── Reads ───────────────────────────────────────────────────
    async def get(self, plugin_id: str) -> PluginSettings | None:
        if self._loader.plugins.get(plugin_id) is None:
            raise NotFoundError(f"Unknown plugin {plugin_id!r}")
        return await self._repo.get_by_plugin(plugin_id)

    # ── Writes ──────────────────────────────────────────────────
    async def upsert(
        self,
        *,
        plugin_id: str,
        values: dict[str, Any],
        notes: str | None = None,
    ) -> PluginSettings:
        if self._loader.plugins.get(plugin_id) is None:
            raise NotFoundError(f"Unknown plugin {plugin_id!r}")
        validated = self._validate(plugin_id, values)
        return await self._repo.upsert(
            plugin_id=plugin_id, values=validated, notes=notes
        )

    def _validate(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        loaded = self._loader.plugins[plugin_id]
        if loaded.instance is None:
            # Plugin loaded but didn't return an instance; treat as no
            # schema declared.
            return values
        schema_cls = getattr(loaded.instance, "settings_schema", None)
        if schema_cls is None:
            return values
        try:
            model = schema_cls.model_validate(values)
        except PydanticValidationError as exc:
            errors = []
            for err in exc.errors(include_url=False):
                entry = dict(err)
                if "ctx" in entry and isinstance(entry["ctx"], dict):
                    entry["ctx"] = {
                        k: str(v) if isinstance(v, BaseException) else v
                        for k, v in entry["ctx"].items()
                    }
                errors.append(entry)
            raise ValidationError(
                f"Plugin {plugin_id!r} settings are invalid",
                details={"errors": errors},
            ) from exc
        return model.model_dump()

    # ── Convenience for plugins ─────────────────────────────────
    async def values_or_defaults(self, plugin_id: str) -> dict[str, Any]:
        """Fetch values; fill in defaults from the schema when missing.

        Plugins call this from their own code: ``settings = await
        get_settings_service(session).values_or_defaults(self.manifest.id)``
        — giving them a dict that's guaranteed to have every key the
        schema declares with a sensible default.
        """
        row = await self._repo.get_by_plugin(plugin_id)
        loaded = self._loader.plugins.get(plugin_id)
        schema_cls = None
        if loaded and loaded.instance is not None:
            schema_cls = getattr(loaded.instance, "settings_schema", None)
        if schema_cls is None:
            return dict(row.values) if row else {}
        # Defaults via a fresh model + merge persisted values on top.
        try:
            defaults = schema_cls().model_dump()
        except PydanticValidationError:
            # The schema has required fields with no defaults; can't
            # fabricate them. Return the persisted dict as-is.
            return dict(row.values) if row else {}
        if row:
            defaults.update(row.values)
        return defaults
