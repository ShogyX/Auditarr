"""Stage 19 (audit follow-up) — VirusTotal client unit tests.

Pins:
  1. ``lookup_by_hash`` returns a parsed result dict on a 200.
  2. ``lookup_by_hash`` returns ``status="not_found"`` on a 404
     so the file drawer can render "Unknown to VT".
  3. ``lookup_by_hash`` returns ``None`` when the daily quota is
     exhausted (caller skips silently).
"""
from __future__ import annotations

import httpx
import pytest

from app.services.virustotal import lookup_by_hash, reset_quota_for_tests


def _mock_transport(status_code: int, json_body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=json_body or {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_lookup_returns_parsed_result_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_quota_for_tests()
    body = {
        "data": {
            "attributes": {
                "last_analysis_stats": {
                    "malicious": 1,
                    "suspicious": 0,
                    "harmless": 42,
                    "undetected": 7,
                },
            }
        }
    }
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(200, body))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    result = await lookup_by_hash(
        api_key="key", sha256="a" * 64, daily_quota=100
    )
    assert result is not None
    assert result["status"] == "ok"
    assert result["malicious"] == 1
    assert result["harmless"] == 42
    assert "permalink" in result


@pytest.mark.asyncio
async def test_lookup_returns_not_found_status_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_quota_for_tests()
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("transport", _mock_transport(404))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    result = await lookup_by_hash(
        api_key="key", sha256="b" * 64, daily_quota=100
    )
    # Stage 10 (addendum B.4): the not_found result carries
    # ``vt_status="not_found"`` so the rule engine column
    # picks it up, plus ``permalink`` for the Files page UI.
    # Stage 19's stricter ``status="not_found"`` field is
    # preserved for backwards compat.
    assert result is not None
    assert result["status"] == "not_found"
    assert result["vt_status"] == "not_found"
    assert "checked_at" in result
    assert result["permalink"] == f"https://www.virustotal.com/gui/file/{'b' * 64}"


@pytest.mark.asyncio
async def test_lookup_returns_none_when_quota_exhausted() -> None:
    """If we've spent the daily cap, the client should bail before
    any network call (no transport needed — if quota check passed
    the request would crash because no client mock is in place)."""
    reset_quota_for_tests()
    # Burn the entire cap.
    from app.services.virustotal import _check_and_increment_quota

    for _ in range(3):
        ok = await _check_and_increment_quota(3)
        assert ok is True
    result = await lookup_by_hash(
        api_key="key", sha256="c" * 64, daily_quota=3
    )
    assert result is None
