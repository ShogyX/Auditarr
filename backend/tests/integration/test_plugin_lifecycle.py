"""Plugin lifecycle isolation tests.

Drives the loader through a couple of plugins where one hook raises
and one doesn't, asserting the loader keeps running, marks the faulty
plugin's later hooks as skipped, and emits ``plugin.error`` on the bus.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.registry import ServiceRegistry
from app.events.bus import EventBus
from app.plugins.loader import PluginLoader


def _write_plugin(
    root: Path,
    *,
    plugin_id: str,
    body: str,
    plugin_type: str = "generic",
) -> Path:
    p = root / plugin_id
    p.mkdir(parents=True)
    (p / "manifest.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id,
                "version": "0.1.0",
                "type": plugin_type,
                "backend_entry": "__init__.py",
            }
        )
    )
    (p / "__init__.py").write_text(body)
    return p


@pytest.fixture
def plugin_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


@pytest.fixture
def loader(plugin_root: Path) -> PluginLoader:
    from app.core.settings import Settings

    settings = Settings(
        secret_key="test-key-must-be-at-least-sixteen-chars",
        plugin_dir=plugin_root,
        builtin_plugin_dir=plugin_root,
    )
    return PluginLoader(
        settings=settings,
        registry=ServiceRegistry(),
        event_bus=EventBus(),
    )


@pytest.mark.asyncio
async def test_load_succeeds_when_one_plugin_on_load_raises(
    plugin_root: Path, loader: PluginLoader
) -> None:
    _write_plugin(
        plugin_root,
        plugin_id="good",
        body=(
            "from app.plugins.contracts import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    async def on_load(self):\n"
            "        self.context.events  # touch SDK so call counts as a real on_load\n"
            "async def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        ),
    )
    _write_plugin(
        plugin_root,
        plugin_id="bad",
        body=(
            "from app.plugins.contracts import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    async def on_load(self):\n"
            "        raise RuntimeError('boom in on_load')\n"
            "async def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        ),
    )

    loaded = await loader.discover_and_load(app=None)
    ids = {p.manifest.id for p in loaded}
    # Both plugins should be in the loaded set — the bad one's
    # ``on_load`` failure no longer aborts the loader run.
    assert ids == {"good", "bad"}
    # The bad plugin is marked as lifecycle-failed.
    bad = loader.plugins["bad"]
    assert getattr(bad.instance, "_auditarr_lifecycle_failed", False) is True


@pytest.mark.asyncio
async def test_on_startup_skips_plugins_whose_on_load_failed(
    plugin_root: Path, loader: PluginLoader
) -> None:
    """A plugin whose ``on_load`` raised should not see ``on_startup`` fire."""
    _write_plugin(
        plugin_root,
        plugin_id="bad",
        body=(
            "from app.plugins.contracts import Plugin, PluginContext\n"
            "STARTUP_HIT = {'count': 0}\n"
            "class P(Plugin):\n"
            "    async def on_load(self):\n"
            "        raise RuntimeError('boom in on_load')\n"
            "    async def on_startup(self):\n"
            "        STARTUP_HIT['count'] += 1\n"
            "async def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        ),
    )
    await loader.discover_and_load(app=None)
    await loader.start()
    # Give the spawned background task a chance to run.
    await asyncio.sleep(0.05)

    import importlib

    # Pull the module out of sys.modules — the loader imports it by path.
    bad_module = next(
        (
            m
            for n, m in dict(importlib.sys.modules).items()
            if "auditarr_plugin_bad" in n
        ),
        None,
    )
    assert bad_module is not None
    assert bad_module.STARTUP_HIT == {"count": 0}


@pytest.mark.asyncio
async def test_on_startup_emits_plugin_error_on_failure(
    plugin_root: Path, loader: PluginLoader
) -> None:
    """A failing ``on_startup`` should produce a ``plugin.error`` event."""
    _write_plugin(
        plugin_root,
        plugin_id="startup-bad",
        body=(
            "from app.plugins.contracts import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    async def on_startup(self):\n"
            "        raise RuntimeError('boom in on_startup')\n"
            "async def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        ),
    )

    seen: list[tuple[str, dict]] = []

    async def listener(event) -> None:
        seen.append((event.name, dict(event.payload)))

    loader._bus.subscribe("plugin.error", listener)  # noqa: SLF001

    await loader.discover_and_load(app=None)
    await loader.start()
    # Let the spawned task surface its exception.
    await asyncio.sleep(0.05)

    matching = [e for e in seen if e[1].get("id") == "startup-bad"]
    assert matching, f"expected plugin.error from startup-bad; saw {seen!r}"
    assert matching[0][1]["phase"] == "on_startup"


@pytest.mark.asyncio
async def test_shutdown_runs_on_shutdown_then_on_unload(
    plugin_root: Path, loader: PluginLoader
) -> None:
    """on_shutdown fires before on_unload, both during ``loader.shutdown()``."""
    _write_plugin(
        plugin_root,
        plugin_id="lifecycle-trace",
        body=(
            "from app.plugins.contracts import Plugin, PluginContext\n"
            "TRACE = []\n"
            "class P(Plugin):\n"
            "    async def on_shutdown(self):\n"
            "        TRACE.append('shutdown')\n"
            "    async def on_unload(self):\n"
            "        TRACE.append('unload')\n"
            "async def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        ),
    )
    await loader.discover_and_load(app=None)
    await loader.shutdown()

    import importlib

    module = next(
        m
        for n, m in dict(importlib.sys.modules).items()
        if "auditarr_plugin_lifecycle_trace" in n
    )
    assert module.TRACE == ["shutdown", "unload"]
