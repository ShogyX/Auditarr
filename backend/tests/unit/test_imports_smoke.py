"""v1.8.3 — Import-smoke test for every reachable app.* module.

Background: v1.8.0 shipped with a typo in ``app/worker_sse.py`` —
``from app.security.box import get_secret_box`` where the real
module path is ``app.security.secrets``. The bad import was never
caught by the existing test suite because no test exercised the
worker's startup function, and the SSE listener supervisor is
imported lazily inside ``app.worker.startup`` (line 312, inside the
async function). Result: the worker hit the crash loop on every
v1.8.x deploy from v1.8.0 onward, no test failed, no CI flag fired.

This test fixes the gap by walking every module under ``app.`` and
calling ``importlib.import_module`` on it. ImportError, ModuleNotFoundError,
AttributeError-during-import, etc. all surface as test failures.
A module that fails to import here is a module that nobody can use,
which is always a real bug.

Modules with side-effects that need expensive setup (DB connection,
HTTP servers) live behind functions and don't run at import time, so
this is fast — full sweep <2s.

What this test does NOT cover:
  * Modules under ``plugins/`` — those are loaded by the plugin
    discoverer at runtime and have their own test surface.
  * Conditional imports inside functions — ``app.worker.startup``'s
    ``from app.worker_sse import ...`` IS covered because we
    import ``app.worker_sse`` directly.
  * Modules that legitimately can't import in test contexts. There
    are currently none; if one appears, add it to ``_SKIP``.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import app

# Modules to skip. Empty for now; add entries with a comment
# explaining why if a future module legitimately can't import in
# tests.
_SKIP: frozenset[str] = frozenset()


def _walk_app_modules() -> list[str]:
    """Yield every importable module name under ``app.``."""
    found: list[str] = []
    app_path = Path(app.__file__).parent
    for module_info in pkgutil.walk_packages(
        path=[str(app_path)],
        prefix="app.",
        # We don't catch errors here so a syntax-error in a module
        # would still make this fail loudly at collection — which
        # is what we want.
        onerror=None,
    ):
        if module_info.name in _SKIP:
            continue
        found.append(module_info.name)
    return found


@pytest.mark.parametrize("module_name", _walk_app_modules())
def test_module_imports_cleanly(module_name: str) -> None:
    """Pin: every ``app.*`` module must be importable.

    A test failure here means the module has a bad import (wrong
    path, missing dependency, syntax error). Production code that
    can't be imported is unreachable code, and unreachable code
    is the precise failure mode v1.8.0 shipped with — the SSE
    listener supervisor crashed on every worker startup because
    ``app.worker_sse`` imported a non-existent path.
    """
    importlib.import_module(module_name)


def test_worker_sse_imports_specifically() -> None:
    """v1.8.3 explicit regression: ``app.worker_sse`` was broken
    in v1.8.0-1.8.2 and the gap wasn't caught by parametrized
    coverage above (which is the actual mitigation; this test
    just makes the specific failure mode obvious to the next
    reader of the test file).
    """
    import app.worker_sse

    # Sanity check that the entry-point symbol is callable.
    assert callable(app.worker_sse.spawn_plex_listeners)
