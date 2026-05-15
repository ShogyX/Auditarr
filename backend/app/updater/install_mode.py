"""Install-environment detection (Stage 19).

The updater needs to know *how* Auditarr was installed so it can:

1. Show the operator the right UI copy ("Updating Docker container…"
   vs "Updating systemd services…").
2. Refuse to fire an apply when there's no helper script to consume
   the sentinel (the "unmanaged" install mode).

We don't trust the operator to set this correctly by hand — the
common case is they don't know. So the default is ``auto`` and we
detect by looking at signals that are essentially impossible to fake:

* **Docker** — the canonical signal is ``/.dockerenv`` (present in
  every Docker container regardless of the base image), or the
  ``container=docker`` env var set by some runtimes. We also accept
  cgroup matching as a fallback for OCI runtimes that don't drop the
  marker file.

* **Bare-metal** — we look at our own systemd unit name via
  ``INVOCATION_ID`` + the unit file existing in the standard location.
  This avoids false positives on developers running ``uvicorn`` by
  hand.

* **Unknown** — fall through. The UI shows "your install environment
  couldn't be auto-detected; set AUDITARR_UPDATE_INSTALL_MODE in your
  config to enable apply".

Detection runs once at startup and is cached for the lifetime of the
process. The operator can override the detection by setting
``AUDITARR_UPDATE_INSTALL_MODE`` to a non-``auto`` value.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

InstallMode = Literal["docker", "bare-metal", "unmanaged"]

# Path the bare-metal installer creates as its marker file. The
# installer touches this on install so we have something concrete
# to look for that's hard to confuse with another tool.
BARE_METAL_MARKER = Path("/etc/auditarr/auditarr.env")

# Path Docker creates inside every container. Hasn't changed across
# Docker versions; OCI runtimes that don't write it usually set
# ``container=`` in the environment instead.
DOCKER_MARKER = Path("/.dockerenv")


def _is_docker() -> bool:
    """Heuristic: are we running inside a Docker / OCI container?"""
    if DOCKER_MARKER.exists():
        return True
    if os.environ.get("container", "").lower() in {"docker", "oci", "podman"}:
        return True
    # cgroup-based detection as a last resort. Linux-only; on macOS /
    # Windows under Docker Desktop the marker file is reliable.
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as fh:
            cgroup = fh.read()
        return "docker" in cgroup or "/containerd/" in cgroup
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _is_bare_metal() -> bool:
    """Heuristic: are we running under systemd as installed by
    ``install-bare-metal.sh``?

    Two signals must both hold:
      * The env file the installer wrote exists at the standard path.
      * We were invoked by systemd (``INVOCATION_ID`` is set on every
        systemd-managed unit).

    Either alone produces false positives — a dev with a copy of the
    env file around, or anyone running the app under a generic systemd
    unit. Requiring both is precise enough in practice.
    """
    if not BARE_METAL_MARKER.exists():
        return False
    return bool(os.environ.get("INVOCATION_ID"))


@lru_cache(maxsize=1)
def detect_install_mode(configured: str = "auto") -> InstallMode:
    """Resolve the install mode.

    ``configured`` is whatever the operator put in
    ``AUDITARR_UPDATE_INSTALL_MODE`` — typically ``"auto"`` for the
    default detect-on-startup behavior. Any non-``auto`` value
    short-circuits detection and is returned verbatim (after
    validation).
    """
    configured = (configured or "auto").strip().lower()
    if configured in {"docker", "bare-metal", "unmanaged"}:
        return configured  # type: ignore[return-value]
    if configured != "auto":
        # Unknown explicit value — treat as unmanaged so we fail safe
        # rather than firing into the wrong helper.
        return "unmanaged"

    if _is_docker():
        return "docker"
    if _is_bare_metal():
        return "bare-metal"
    return "unmanaged"


def reset_cache_for_tests() -> None:
    """Clear the lru_cache so tests can re-detect under monkeypatched
    environments. Not for production use."""
    detect_install_mode.cache_clear()
