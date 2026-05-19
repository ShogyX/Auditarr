"""v1.9 audit fix (OP-14) — built-in plugin manifests must
validate against the PluginManifest schema.

Pin: every ``manifest.json`` under ``backend/plugins/`` parses
cleanly. A typo / unsupported id character / missing field
silently makes the plugin invisible at runtime — the discovery
log emits a warning but the integrations directory just shows
"no provider found" with no operator-visible explanation.

The original ai_provider plugin shipped with id="ai_provider"
which the PluginManifest validator rejects (underscores not
allowed in plugin ids). Fixed by renaming to "ai-provider".
This test ensures the same class of issue doesn't recur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugins.contracts import PluginManifest


def _plugin_root() -> Path:
    """Resolve the backend/plugins directory regardless of where
    pytest is invoked from."""
    here = Path(__file__).resolve()
    # Walk up to find backend/plugins.
    for parent in [here, *here.parents]:
        candidate = parent / "backend" / "plugins"
        if candidate.is_dir():
            return candidate
        candidate = parent.parent / "plugins"
        if candidate.is_dir() and (candidate / "ai_provider").is_dir() is False:
            # Inside backend/tests/unit/ → plugins is at parent/parent/plugins
            candidate2 = parent.parent / "plugins"
            if candidate2.is_dir() and any(
                (p / "manifest.json").exists() for p in candidate2.iterdir() if p.is_dir()
            ):
                return candidate2
    pytest.skip("backend/plugins directory not found from test location")
    raise AssertionError("unreachable")  # pytest.skip raises; this keeps the return type honest


def _discover_manifests() -> list[tuple[str, Path]]:
    root = _plugin_root()
    out: list[tuple[str, Path]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        mf = child / "manifest.json"
        if mf.exists():
            out.append((child.name, mf))
    return out


def test_at_least_one_plugin_manifest_present() -> None:
    """Sanity: discovery walks SOMETHING. If this fails the
    test setup is wrong, not the plugins."""
    manifests = _discover_manifests()
    assert manifests, "no plugin manifests discovered under backend/plugins/"


@pytest.mark.parametrize(
    "plugin_name,manifest_path",
    _discover_manifests(),
    ids=[name for name, _ in _discover_manifests()],
)
def test_plugin_manifest_validates(
    plugin_name: str, manifest_path: Path
) -> None:
    """Each ``manifest.json`` parses against the schema. If this
    fails, the plugin will silently not load at runtime."""
    raw = json.loads(manifest_path.read_text())
    manifest = PluginManifest.model_validate(raw)
    # The id MUST match the directory name (the loader uses the
    # directory layout for discovery and the id for capability
    # registration — a mismatch produces a confusing "shadowed"
    # warning).
    assert manifest.id == plugin_name, (
        f"plugin directory {plugin_name!r} has manifest id "
        f"{manifest.id!r} — these must match"
    )


def test_ai_provider_plugin_is_present_and_loadable() -> None:
    """v1.9 OP-14 — specifically pin the AI provider plugin
    discoverability. The dashed form ``ai-provider`` is the
    canonical id; the underscore form ``ai_provider`` is invalid."""
    manifests = {name: path for name, path in _discover_manifests()}
    assert (
        "ai-provider" in manifests
    ), "ai-provider plugin missing from backend/plugins/"
    raw = json.loads(manifests["ai-provider"].read_text())
    manifest = PluginManifest.model_validate(raw)
    assert manifest.id == "ai-provider"
    assert manifest.type == "integration"
    assert "integration.ai-provider" in manifest.capabilities
