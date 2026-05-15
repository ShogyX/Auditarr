"""Plugin loader tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.registry import ServiceRegistry
from app.core.settings import Settings
from app.events.bus import EventBus
from app.plugins.loader import PluginLoader


def _write_plugin(root: Path, plugin_id: str, *, requires: list[str] | None = None) -> None:
    pdir = root / plugin_id
    pdir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "0.1.0",
        "type": "generic",
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": False,
        "permissions": [],
        "capabilities": ["test.cap"],
        "requires": requires or [],
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest))
    (pdir / "backend.py").write_text(
        "from app.plugins import Plugin, PluginContext\n"
        "class P(Plugin):\n"
        "    pass\n"
        "def register(ctx: PluginContext):\n"
        "    ctx.register_capability('test.cap', object())\n"
        "    return P(ctx)\n"
    )


@pytest.mark.asyncio
async def test_loader_discovers_and_loads(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "alpha")
    _write_plugin(tmp_path, "beta", requires=["alpha"])

    settings = Settings(plugin_dir=tmp_path, builtin_plugin_dir=tmp_path)
    registry = ServiceRegistry()
    bus = EventBus()
    loader = PluginLoader(settings=settings, registry=registry, event_bus=bus)

    loaded = await loader.discover_and_load()
    ids = [p.manifest.id for p in loaded]
    # Beta requires alpha → alpha must come first.
    assert ids == ["alpha", "beta"]
    # Two distinct plugins each registering one capability provider.
    assert len(registry.providers_for("test.cap")) == 2


@pytest.mark.asyncio
async def test_loader_handles_invalid_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not-json")
    (bad / "backend.py").write_text("def register(ctx): return None\n")

    settings = Settings(plugin_dir=tmp_path, builtin_plugin_dir=tmp_path)
    loader = PluginLoader(
        settings=settings, registry=ServiceRegistry(), event_bus=EventBus()
    )
    loaded = await loader.discover_and_load()
    assert loaded == []


@pytest.mark.asyncio
async def test_loader_dedupes_across_directories(tmp_path: Path) -> None:
    """If the same plugin id appears in builtin AND user dirs, only the first wins."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()

    # Same id in both — built-in must take precedence.
    _write_plugin(builtin, "shared")
    _write_plugin(user, "shared")
    _write_plugin(user, "user-only")

    settings = Settings(plugin_dir=user, builtin_plugin_dir=builtin)
    loader = PluginLoader(
        settings=settings, registry=ServiceRegistry(), event_bus=EventBus()
    )
    loaded = await loader.discover_and_load()
    ids = sorted(p.manifest.id for p in loaded)
    assert ids == ["shared", "user-only"]
    # The built-in version of "shared" was loaded — confirm by checking the
    # plugin context's directory.
    shared = next(p for p in loaded if p.manifest.id == "shared")
    assert shared.context.directory.parent == builtin
