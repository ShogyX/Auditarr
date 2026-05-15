"""VirusTotal hash-lookup client (Stage 19 audit follow-up).

Free-tier: ``GET /files/{hash}``. We do NOT upload files — that
needs a paid tier and was explicitly out of scope. The quota
counter resets daily at UTC midnight and is in-process only (no
shared state needed; per-process daily quotas are aligned with
VT's own per-API-key counting).

Usage:

    async with httpx.AsyncClient(timeout=10.0) as client:
        result = await lookup_by_hash(
            session=session, api_key=key, sha256=hex_hash
        )
        if result is not None:
            media_file.virustotal_result = result
            media_file.virustotal_checked_at = utcnow()

``None`` return value means "no result to persist" (quota
exhausted, or 404, or transient network error). Caller stays
quiet.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger("auditarr.virustotal", category="virustotal")

_VT_BASE = "https://www.virustotal.com/api/v3"


@dataclass
class _QuotaState:
    """Per-day submission counter. Resets at UTC midnight."""

    counter: int = 0
    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_quota = _QuotaState()


async def _check_and_increment_quota(cap: int) -> bool:
    """Atomically check whether we can spend one quota unit.
    Returns ``True`` if the caller may proceed; ``False`` if the
    daily cap is exhausted (caller skips the lookup silently)."""
    today = datetime.now(UTC).date()
    async with _quota.lock:
        if _quota.day != today:
            _quota.day = today
            _quota.counter = 0
        if _quota.counter >= cap:
            return False
        _quota.counter += 1
        return True


async def lookup_by_hash(
    *, api_key: str, sha256: str, daily_quota: int, timeout: float = 10.0
) -> dict[str, Any] | None:
    """Look up a hash on VirusTotal. Returns a small persistable
    result dict, or ``None`` if there's nothing to persist (quota,
    404, or transient error).

    The persisted shape is deliberately tiny — the verbose VT
    response gets compressed to the four severity-style counters
    that the Files page surfaces, plus the permalink so the
    operator can click through.
    """
    if not api_key or not sha256:
        return None
    if not await _check_and_increment_quota(daily_quota):
        log.info("virustotal.quota_exhausted", sha256=sha256, cap=daily_quota)
        return None

    url = f"{_VT_BASE}/files/{sha256}"
    headers = {"x-apikey": api_key, "accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        log.warning(
            "virustotal.network_error", sha256=sha256, error=str(exc)[:200]
        )
        return None

    if response.status_code == 404:
        # File hash unknown to VT. Persist the negative result so
        # the Files page can show "Unknown to VirusTotal" rather
        # than render nothing.
        return {
            "status": "not_found",
            "checked_at": datetime.now(UTC).isoformat(),
        }
    if response.status_code in (401, 403):
        log.warning("virustotal.auth_rejected", status=response.status_code)
        return None
    if response.status_code == 429:
        log.warning("virustotal.rate_limited")
        return None
    if response.status_code >= 400:
        log.warning(
            "virustotal.upstream_error", status=response.status_code
        )
        return None

    try:
        body = response.json()
    except ValueError:
        return None

    attributes = (body.get("data") or {}).get("attributes") or {}
    stats = attributes.get("last_analysis_stats") or {}
    return {
        "status": "ok",
        "malicious": int(stats.get("malicious", 0)),
        "suspicious": int(stats.get("suspicious", 0)),
        "harmless": int(stats.get("harmless", 0)),
        "undetected": int(stats.get("undetected", 0)),
        "permalink": f"https://www.virustotal.com/gui/file/{sha256}",
        "checked_at": datetime.now(UTC).isoformat(),
    }


def reset_quota_for_tests() -> None:
    """Test-only helper: zero the daily counter so a test suite
    doesn't run up against the cap. Not exported via __all__."""
    _quota.counter = 0
    _quota.day = datetime.now(UTC).date()
