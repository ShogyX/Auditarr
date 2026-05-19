"""Stage 10 (v1.7) — VirusTotal quota tests.

Plan §524:
    Set ``daily_quota=2``, fire 3 lookups, assert 2 succeed
    and the 3rd returns None with the quota event.

Addendum B.7 — the VT free-tier has three windows:
    * Per-minute: 4 lookups (VT's physical ceiling).
    * Per-day:    500 lookups default.
    * Per-month:  15500 lookups default.
The plugin enforces all three. We pin each window
independently here so a future bug that drops one of the
windows surfaces as a test failure, not as a real-world
operator complaint.

All tests mock the httpx transport so no real VT calls fire
(per plan §532 "Out of scope: Real VT calls in tests (mock
httpx)").
"""

from __future__ import annotations


import httpx
import pytest

from app.events.bus import EventBus
from app.events.types import DomainEvent
from plugins.virustotal.backend import (
    VT_MINUTE_CEILING,
    _check_and_increment_quota,
    _quota,
    lookup_by_hash,
    quota_snapshot,
    reset_quota_for_tests,
)


# ── Helpers ─────────────────────────────────────────────────────


def _mock_transport(
    status_code: int = 200, body: dict | None = None
) -> httpx.MockTransport:
    """Returns an httpx mock transport that responds with the
    given status + body for every request. Per plan §532 we
    never call the real VT endpoint in tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json=body or {
                "data": {
                    "attributes": {
                        "last_analysis_stats": {
                            "malicious": 0,
                            "suspicious": 0,
                            "harmless": 50,
                            "undetected": 10,
                        },
                    }
                }
            },
        )

    return httpx.MockTransport(handler)


class _RecordingBus(EventBus):
    """Test double for the event bus that records every published
    event. Inherits from EventBus so type-checks pass — we
    override ``publish`` to skip the dispatch and just collect."""

    def __init__(self) -> None:
        # Call ``super().__init__()`` even though this test double
        # never touches the subscriber registry — the base init
        # just allocates an empty dict + lock, so it's cheap and it
        # silences CodeQL's ``py/missing-call-to-init`` rule.
        super().__init__()
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


# ── Test 1 — Plan §524 contract ────────────────────────────────


@pytest.mark.asyncio
async def test_quota_exhaustion_after_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §524: set ``daily_quota=2``, fire 3 lookups, assert
    2 succeed and the 3rd returns None with the quota event."""
    reset_quota_for_tests()
    bus = _RecordingBus()

    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(200))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    # First two lookups succeed.
    r1 = await lookup_by_hash(
        api_key="k", sha256="a" * 64, daily_quota=2, event_bus=bus
    )
    r2 = await lookup_by_hash(
        api_key="k", sha256="b" * 64, daily_quota=2, event_bus=bus
    )
    assert r1 is not None and r1["status"] == "ok"
    assert r2 is not None and r2["status"] == "ok"

    # Third lookup hits the cap; quota_exhausted fires on the bus.
    r3 = await lookup_by_hash(
        api_key="k", sha256="c" * 64, daily_quota=2, event_bus=bus
    )
    assert r3 is None

    # Two virustotal.result events (one per successful lookup)
    # + one virustotal.quota_exhausted for the day window.
    names = [e.name for e in bus.events]
    assert names.count("virustotal.result") == 2
    assert names.count("virustotal.quota_exhausted") == 1
    exhausted = next(
        e for e in bus.events if e.name == "virustotal.quota_exhausted"
    )
    assert exhausted.payload["window"] == "day"
    assert exhausted.payload["cap"] == 2


# ── Test 2 — Addendum B.7: per-minute ceiling ──────────────────


@pytest.mark.asyncio
async def test_minute_window_enforces_4_lookups_per_60s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free-tier per-minute ceiling is 4 lookups (VT's
    physical limit per addendum B.7). The 5th lookup in the
    same minute returns None with a ``window="minute"``
    quota_exhausted event, even if the daily quota has plenty
    of room.
    """
    reset_quota_for_tests()
    bus = _RecordingBus()

    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(200))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    # Burn the minute ceiling (4 lookups). All succeed.
    for i in range(VT_MINUTE_CEILING):
        r = await lookup_by_hash(
            api_key="k",
            sha256=f"{i:064x}",
            daily_quota=100,  # plenty of headroom
            monthly_quota=10000,
            event_bus=bus,
        )
        assert r is not None, f"Lookup {i} unexpectedly failed"

    # 5th lookup in the same minute → blocked.
    r5 = await lookup_by_hash(
        api_key="k",
        sha256="f" * 64,
        daily_quota=100,
        monthly_quota=10000,
        event_bus=bus,
    )
    assert r5 is None

    # Per-minute quota_exhausted fires exactly once.
    exhausted = [
        e for e in bus.events if e.name == "virustotal.quota_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0].payload["window"] == "minute"
    assert exhausted[0].payload["cap"] == VT_MINUTE_CEILING


# ── Test 3 — Addendum B.7: monthly ceiling ─────────────────────


@pytest.mark.asyncio
async def test_monthly_window_enforces_independently_of_daily() -> None:
    """The per-month window is enforced independently. With a
    daily cap that's never hit, exhausting the monthly cap
    still blocks lookups."""
    reset_quota_for_tests()
    bus = _RecordingBus()

    # We exercise the quota helper directly (without the
    # outer lookup_by_hash) so we don't need an httpx mock for
    # every iteration. The contract under test is that the
    # monthly counter actually enforces independent of daily.
    for i in range(3):
        allowed, window = await _check_and_increment_quota(
            minute_cap=100,  # plenty
            daily_cap=100,  # plenty
            monthly_cap=3,  # the limiter under test
            event_bus=bus,
        )
        assert allowed is True, f"Iter {i}: should be allowed"

    # 4th hit blocks on the month window.
    allowed, window = await _check_and_increment_quota(
        minute_cap=100,
        daily_cap=100,
        monthly_cap=3,
        event_bus=bus,
    )
    assert allowed is False
    assert window == "month"

    # quota_exhausted fires exactly once per window.
    exhausted = [
        e for e in bus.events if e.name == "virustotal.quota_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0].payload["window"] == "month"


# ── Test 4 — quota_exhausted fires once per window ─────────────


@pytest.mark.asyncio
async def test_quota_exhausted_only_fires_once_per_window() -> None:
    """Once a window is exhausted, subsequent calls within that
    same window MUST NOT spam the bus — the operator only
    needs one notification."""
    reset_quota_for_tests()
    bus = _RecordingBus()

    # Burn the daily cap.
    for _ in range(2):
        allowed, _ = await _check_and_increment_quota(
            minute_cap=100, daily_cap=2, monthly_cap=1000, event_bus=bus
        )
        assert allowed is True

    # Three more attempts, all blocked on day window.
    for _ in range(3):
        allowed, window = await _check_and_increment_quota(
            minute_cap=100, daily_cap=2, monthly_cap=1000, event_bus=bus
        )
        assert allowed is False
        assert window == "day"

    # ONLY one quota_exhausted event despite 3 attempts.
    exhausted = [
        e for e in bus.events if e.name == "virustotal.quota_exhausted"
    ]
    assert len(exhausted) == 1


# ── Test 5 — quota_snapshot surfaces all three windows ─────────


@pytest.mark.asyncio
async def test_quota_snapshot_surfaces_all_three_windows() -> None:
    """Addendum B.7: the status endpoint surfaces remaining
    capacity for all three windows. We pin the snapshot's
    shape here so the endpoint test (in
    test_virustotal_status_endpoint_stage10.py) and the
    frontend hook can rely on it."""
    reset_quota_for_tests()

    # Burn 2 minute / 2 day / 2 month (same calls advance all
    # three windows in lock-step).
    for _ in range(2):
        await _check_and_increment_quota(
            minute_cap=4, daily_cap=500, monthly_cap=15500, event_bus=None
        )

    snap = quota_snapshot(minute_cap=4, daily_cap=500, monthly_cap=15500)
    # All three windows present + counters + caps + remaining.
    for prefix in ("minute", "day", "month"):
        assert f"{prefix}_used" in snap
        assert f"{prefix}_cap" in snap
        assert f"{prefix}_remaining" in snap
    assert snap["minute_used"] == 2
    assert snap["minute_remaining"] == 2
    assert snap["day_used"] == 2
    assert snap["day_remaining"] == 498
    assert snap["month_used"] == 2
    assert snap["month_remaining"] == 15498
    # last_check_at is populated after at least one allowed call.
    assert snap["last_check_at"] is not None


# ── Test 6 — Malicious result classified correctly ─────────────


@pytest.mark.asyncio
async def test_malicious_response_classified_as_malicious(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addendum B.4: a non-zero ``malicious`` engine count from
    VT → ``vt_status="malicious"`` in the persisted result.
    The rule engine filters on this exact string."""
    reset_quota_for_tests()
    real_init = httpx.AsyncClient.__init__

    body = {
        "data": {
            "attributes": {
                "last_analysis_stats": {
                    "malicious": 3,
                    "suspicious": 1,
                    "harmless": 0,
                    "undetected": 40,
                }
            }
        }
    }

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(200, body))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    bus = _RecordingBus()
    result = await lookup_by_hash(
        api_key="k",
        sha256="d" * 64,
        daily_quota=100,
        event_bus=bus,
    )
    assert result is not None
    assert result["vt_status"] == "malicious"  # malicious wins over suspicious
    assert result["malicious"] == 3
    assert result["suspicious"] == 1
    # Bus event reports the canonical status string.
    res_events = [e for e in bus.events if e.name == "virustotal.result"]
    assert len(res_events) == 1
    assert res_events[0].payload["vt_status"] == "malicious"


# ── Test 7 — 404 → not_found with vt_status string ─────────────


@pytest.mark.asyncio
async def test_404_returns_not_found_vt_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addendum B.4: a 404 from VT → ``vt_status="not_found"``
    in the persisted result. Distinct from ``error`` (which
    means "VT call failed entirely")."""
    reset_quota_for_tests()
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(404))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    bus = _RecordingBus()
    result = await lookup_by_hash(
        api_key="k", sha256="e" * 64, daily_quota=100, event_bus=bus
    )
    assert result is not None
    assert result["vt_status"] == "not_found"
    assert result["status"] == "not_found"
    # Bus event still fires for the not_found outcome so the
    # rule engine can act.
    res_events = [e for e in bus.events if e.name == "virustotal.result"]
    assert len(res_events) == 1
    assert res_events[0].payload["vt_status"] == "not_found"


# ── Test 8 — Window rotation: minute counter rolls over ────────


@pytest.mark.asyncio
async def test_minute_window_rotates_after_60s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-minute window must roll over after 60 seconds —
    if it didn't, a long-running process would be permanently
    blocked after the first burst.

    We simulate time advancing by replacing the plugin's
    internal ``datetime.now`` reference inside the rotation
    helper. The cleanest way: directly mutate
    ``_quota.minute_window_started`` to a value 61 seconds in
    the past, then assert the next call rotates.
    """
    from datetime import UTC, datetime, timedelta

    reset_quota_for_tests()
    bus = _RecordingBus()

    # Burn the minute cap.
    for _ in range(VT_MINUTE_CEILING):
        allowed, _ = await _check_and_increment_quota(
            minute_cap=VT_MINUTE_CEILING,
            daily_cap=500,
            monthly_cap=15500,
            event_bus=bus,
        )
        assert allowed is True

    # Confirm we'd be blocked right now.
    allowed, window = await _check_and_increment_quota(
        minute_cap=VT_MINUTE_CEILING,
        daily_cap=500,
        monthly_cap=15500,
        event_bus=bus,
    )
    assert allowed is False
    assert window == "minute"

    # Fake the passage of time: rewind the minute-window start
    # by 61 seconds so the next call sees a rolled-over window.
    _quota.minute_window_started = (
        datetime.now(UTC) - timedelta(seconds=61)
    )
    # Reset the alert flag manually too, since the rotation
    # helper handles that on the NEXT call.

    allowed, _ = await _check_and_increment_quota(
        minute_cap=VT_MINUTE_CEILING,
        daily_cap=500,
        monthly_cap=15500,
        event_bus=bus,
    )
    assert allowed is True  # window rolled over → fresh budget


# ── Test 9 — autouse C.5 fixture clears state between tests ────


@pytest.mark.asyncio
async def test_autouse_quota_reset_fixture_provides_clean_state() -> None:
    """Addendum C.5: the autouse ``_reset_virustotal_quota``
    fixture in conftest zeros the singleton between tests.
    This test runs LAST in the file but starts with a clean
    snapshot because of the autouse reset."""
    snap = quota_snapshot(minute_cap=4, daily_cap=500, monthly_cap=15500)
    assert snap["minute_used"] == 0
    assert snap["day_used"] == 0
    assert snap["month_used"] == 0
    assert snap["last_check_at"] is None
