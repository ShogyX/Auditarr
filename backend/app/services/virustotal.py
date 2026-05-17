"""VirusTotal hash-lookup client — legacy compat shim (Stage 10).

Stage 10 moved the VirusTotal behaviour onto a proper plugin
under ``backend/plugins/virustotal/backend.py`` so VT lives on
the Integrations page rather than the Plugins page. This module
remains as a thin compat shim because the Stage 19 audit
follow-up's scanner integration imports
``lookup_by_hash`` and ``reset_quota_for_tests`` from here.

The actual logic — three-window quota state (per-minute /
per-day / per-month per addendum B.7), the canonical
``vt_status`` strings (addendum B.4), the
``virustotal.result`` / ``virustotal.quota_exhausted`` bus
events — lives in the plugin module. This file re-exports the
public surface so existing call sites keep working.

What's preserved:
    * ``lookup_by_hash(api_key, sha256, daily_quota, timeout)``
      — same signature as Stage 19 but now delegates to the
      plugin's three-window-aware implementation.
    * ``reset_quota_for_tests()`` — same name; clears the
      plugin's quota state.
    * ``_check_and_increment_quota(cap)`` — legacy single-cap
      wrapper kept for the Stage 19 test that exercises it
      directly. Internally delegates to the new three-window
      function with the minute/month caps set so they don't
      affect the legacy daily-only contract.
"""

from __future__ import annotations

from typing import Any

# Re-export the plugin's canonical types + helpers so existing
# callers don't need to know about the move.
from plugins.virustotal.backend import (
    VT_MINUTE_CEILING,
    VT_MONTHLY_CEILING_DEFAULT,
    VT_STATUS_CLEAN,
    VT_STATUS_ERROR,
    VT_STATUS_MALICIOUS,
    VT_STATUS_NOT_FOUND,
    VT_STATUS_SUSPICIOUS,
    _check_and_increment_quota as _plugin_check_and_increment,
    lookup_by_hash as _plugin_lookup_by_hash,
    quota_snapshot,
    reset_quota_for_tests,
)

__all__ = [
    "lookup_by_hash",
    "reset_quota_for_tests",
    "_check_and_increment_quota",
    "quota_snapshot",
    "VT_STATUS_CLEAN",
    "VT_STATUS_MALICIOUS",
    "VT_STATUS_SUSPICIOUS",
    "VT_STATUS_NOT_FOUND",
    "VT_STATUS_ERROR",
]


async def lookup_by_hash(
    *,
    api_key: str,
    sha256: str,
    daily_quota: int,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Legacy daily-cap-only wrapper.

    The Stage 19 callers pass only ``daily_quota``; we
    forward to the new three-window implementation with the
    monthly cap set to the free-tier ceiling (which doesn't
    affect those callers' behaviour because they're nowhere
    near 15500 lookups in a test run).
    """
    return await _plugin_lookup_by_hash(
        api_key=api_key,
        sha256=sha256,
        daily_quota=daily_quota,
        monthly_quota=VT_MONTHLY_CEILING_DEFAULT,
        timeout=timeout,
        event_bus=None,
    )


async def _check_and_increment_quota(cap: int) -> bool:
    """Legacy single-cap wrapper kept for the existing Stage 19
    test that exercises this helper directly.

    Forwards to the three-window helper with the daily cap set
    to the caller's value and minute/month caps set to safely
    high values so they don't interfere. Returns just the
    boolean (the new helper returns ``(bool, window | None)``).
    """
    allowed, _ = await _plugin_check_and_increment(
        minute_cap=VT_MINUTE_CEILING,
        daily_cap=cap,
        monthly_cap=VT_MONTHLY_CEILING_DEFAULT,
        event_bus=None,
    )
    return allowed
