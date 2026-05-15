"""Stage 25 — Plugin loader enrichment + reload.

Covers the additions made for the Plugins page modernization:

  - ``list_summary()`` now carries description, author, status,
    last_error, has_settings — and surfaces failed-to-load entries
    alongside loaded ones.
  - ``reload_one()`` tears down an existing instance, drops the
    module from ``sys.modules``, re-reads the manifest, and
    re-runs the load pipeline.

The structural plugin-loader behavior (discovery, topo sort, dedupe)
is covered in ``test_plugin_loader.py``; this file pins only the
Stage 25 contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.registry import ServiceRegistry
from app.core.settings import Settings
from app.events.bus import EventBus
from app.plugins.loader import PluginLoader


def _write_plugin(
    root: Path,
    plugin_id: str,
    *,
    description: str = "",
    author: str = "",
    on_load_raises: bool = False,
    settings: bool = False,
) -> Path:
    pdir = root / plugin_id
    pdir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "0.2.0",
        "type": "generic",
        "description": description,
        "author": author,
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": settings,
        "permissions": [],
        "capabilities": [],
        "requires": [],
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest))
    if on_load_raises:
        body = (
            "from app.plugins import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    async def on_load(self):\n"
            "        raise RuntimeError('intentional on_load failure')\n"
            "def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        )
    else:
        body = (
            "from app.plugins import Plugin, PluginContext\n"
            "class P(Plugin):\n"
            "    pass\n"
            "def register(ctx: PluginContext):\n"
            "    return P(ctx)\n"
        )
    (pdir / "backend.py").write_text(body)
    return pdir


def _write_bad_plugin(root: Path, plugin_id: str) -> Path:
    """Write a plugin whose backend.py raises at import time."""
    pdir = root / plugin_id
    pdir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "0.1.0",
        "type": "generic",
        "description": "intentionally broken",
        "author": "test",
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": False,
        "permissions": [],
        "capabilities": [],
        "requires": [],
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest))
    (pdir / "backend.py").write_text("raise RuntimeError('bang')\n")
    return pdir


def _loader_for(tmp_path: Path) -> PluginLoader:
    settings = Settings(plugin_dir=tmp_path, builtin_plugin_dir=tmp_path)
    return PluginLoader(
        settings=settings,
        registry=ServiceRegistry(),
        event_bus=EventBus(),
    )


# ── list_summary enrichment ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_summary_carries_description_and_author(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "alpha",
        description="The first plugin",
        author="ACME Co.",
    )
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    summary = loader.list_summary()
    assert len(summary) == 1
    entry = summary[0]
    assert entry["id"] == "alpha"
    assert entry["description"] == "The first plugin"
    assert entry["author"] == "ACME Co."
    assert entry["status"] == "loaded"
    assert entry["last_error"] is None
    assert entry["has_settings"] is False


@pytest.mark.asyncio
async def test_list_summary_marks_errored_when_on_load_raises(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path, "broken", on_load_raises=True)
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    summary = loader.list_summary()
    entry = next(e for e in summary if e["id"] == "broken")
    assert entry["status"] == "errored"
    assert entry["last_error"] is not None
    assert "on_load" in entry["last_error"]
    assert "intentional on_load failure" in entry["last_error"]


@pytest.mark.asyncio
async def test_list_summary_includes_failed_to_load_entries(
    tmp_path: Path,
) -> None:
    """A plugin whose backend.py raises at import time should NOT
    silently disappear from the operator's view. It should appear in
    list_summary with status='failed_to_load' and the error
    message, so the operator can debug without grepping logs."""
    _write_plugin(tmp_path, "ok-one")
    _write_bad_plugin(tmp_path, "broken")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    summary = loader.list_summary()
    ids_to_status = {e["id"]: e["status"] for e in summary}
    assert ids_to_status == {"ok-one": "loaded", "broken": "failed_to_load"}

    broken = next(e for e in summary if e["id"] == "broken")
    assert "bang" in broken["last_error"]


@pytest.mark.asyncio
async def test_list_summary_orders_loaded_before_failed(tmp_path: Path) -> None:
    """Failed-to-load entries should collect at the bottom of the list
    so the table reads cleanly. The exact within-group order isn't
    contractual, but the loaded → failed grouping is."""
    _write_plugin(tmp_path, "ok-a")
    _write_bad_plugin(tmp_path, "bad-a")
    _write_plugin(tmp_path, "ok-b")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    summary = loader.list_summary()
    statuses = [e["status"] for e in summary]
    # Every loaded entry comes before the first failed_to_load.
    last_loaded = max(
        i for i, s in enumerate(statuses) if s in ("loaded", "errored")
    )
    first_failed = next(
        i for i, s in enumerate(statuses) if s == "failed_to_load"
    )
    assert last_loaded < first_failed


# ── reload_one ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_one_picks_up_source_changes(tmp_path: Path) -> None:
    """The canonical use case: operator edits the plugin's backend.py,
    then asks the UI to reload. The new code should be live without
    a host restart."""
    pdir = _write_plugin(tmp_path, "edits-loaded")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    # Original plugin sets ``_marker`` to 1.
    (pdir / "backend.py").write_text(
        "from app.plugins import Plugin, PluginContext\n"
        "class P(Plugin):\n"
        "    _marker = 1\n"
        "def register(ctx: PluginContext):\n"
        "    return P(ctx)\n"
    )
    # Reload picks up the source change.
    new_summary = await loader.reload_one("edits-loaded")
    assert new_summary is not None
    assert new_summary["status"] == "loaded"

    instance = loader.plugins["edits-loaded"].instance
    assert getattr(type(instance), "_marker", None) == 1


@pytest.mark.asyncio
async def test_reload_one_recovers_from_failed_to_load(tmp_path: Path) -> None:
    """A plugin that failed to load on first try can be reloaded
    after the operator fixes the underlying file. The failed-load
    entry must move out of ``_failed_loads`` and into ``_plugins``."""
    _write_bad_plugin(tmp_path, "fixable")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    assert "fixable" not in loader.plugins
    summary_before = loader.list_summary()
    assert (
        next(e for e in summary_before if e["id"] == "fixable")["status"]
        == "failed_to_load"
    )

    # Operator fixes the file.
    (tmp_path / "fixable" / "backend.py").write_text(
        "from app.plugins import Plugin, PluginContext\n"
        "class P(Plugin):\n"
        "    pass\n"
        "def register(ctx: PluginContext):\n"
        "    return P(ctx)\n"
    )

    new_summary = await loader.reload_one("fixable")
    assert new_summary is not None
    assert new_summary["status"] == "loaded"
    assert new_summary["last_error"] is None
    assert "fixable" in loader.plugins


@pytest.mark.asyncio
async def test_reload_one_returns_none_for_unknown_plugin(
    tmp_path: Path,
) -> None:
    loader = _loader_for(tmp_path)
    result = await loader.reload_one("never-existed")
    assert result is None


@pytest.mark.asyncio
async def test_reload_one_records_new_failure(tmp_path: Path) -> None:
    """If the operator breaks the plugin and then reloads, the new
    failure should land in the summary — the old success shouldn't
    mask the new break."""
    pdir = _write_plugin(tmp_path, "now-broken")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()
    assert "now-broken" in loader.plugins

    # Operator edits the plugin to raise on import.
    (pdir / "backend.py").write_text("raise RuntimeError('oops')\n")

    new_summary = await loader.reload_one("now-broken")
    assert new_summary is not None
    assert new_summary["status"] == "failed_to_load"
    assert "oops" in new_summary["last_error"]
    assert "now-broken" not in loader.plugins


@pytest.mark.asyncio
async def test_reload_one_drops_module_from_sys_modules(tmp_path: Path) -> None:
    """The module-cache invalidation is the linchpin of the
    edit-and-reload workflow — without it, ``importlib`` would
    return the cached module and the operator's code edits would
    not be picked up. Pin this explicitly so a future refactor
    can't silently regress it."""
    _write_plugin(tmp_path, "cached")
    loader = _loader_for(tmp_path)
    await loader.discover_and_load()

    module_name = "auditarr_plugin_cached"
    assert module_name in sys.modules

    # Take a reference to the module object. After reload, the new
    # entry in sys.modules should be a DIFFERENT module instance.
    original = sys.modules[module_name]
    await loader.reload_one("cached")
    reloaded = sys.modules.get(module_name)
    assert reloaded is not None
    assert reloaded is not original
