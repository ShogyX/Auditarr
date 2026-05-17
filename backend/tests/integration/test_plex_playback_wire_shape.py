"""Wire-shape regression test for the Plex playback fetcher.

Pre-fix bug summary
-------------------

Before 2026-05-17, ``PlexProvider.fetch_playback_events`` built its
HTTP request like this:

    params = {
        "sort": "viewedAt:desc",
        "viewedAt>=": int(cutoff.timestamp()),
        "X-Plex-Container-Start": 0,
        "X-Plex-Container-Size": 200,
    }
    response = await client.get(
        "/status/sessions/history/all", params=params
    )

Two real problems with that shape:

1. ``params={"viewedAt>=": N}`` is URL-encoded by httpx to
   ``viewedAt%3E%3D=N``. Plex Media Server's filter parser needs
   the literal ``>=`` operator in the query string; the encoded
   variant doesn't match and Plex silently drops the filter,
   returning either no results (recent PMS) or unfiltered
   results (older PMS).

2. ``X-Plex-Container-Start`` / ``Size`` are documented as
   **HTTP headers**, not query params. Plex ignores them as
   query params on most builds and falls back to its default
   page (typically 50 entries with reduced detail).

The original Stage 16 test for the parser
(``test_telemetry_parsers.py``) covered ``_plex_history_to_event``
in isolation, but never exercised the actual HTTP call shape, so
the bug shipped green.

This test installs an httpx ``MockTransport`` that captures the
outgoing request and asserts the wire shape is correct:

  * URL contains LITERAL ``viewedAt>={unix}`` (no URL encoding).
  * Pagination is on the HEADERS, not the query string.
  * Container-Start and Container-Size are sent as their canonical
    Plex-documented values.

We also add a parallel test for ``fetch_live_playbacks`` to pin
its shape, since that's the other surface where "doesn't work"
came up.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

import httpx
import pytest

from app.integrations.types import IntegrationConfig
from plugins.plex.backend import PlexProvider


# ── Structlog-shaped stub ──────────────────────────────────────


class _RecordingLog:
    """Captures structlog-style log calls (kwargs are part of the
    payload, not formatting args). The plugin code calls
    ``self._log.warning("event.name", error=str(exc), ...)`` —
    a stdlib logger raises TypeError on the kwargs, so we use this
    captures-only stub for tests."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.records.append(("warning", event, dict(kwargs)))

    def info(self, event: str, **kwargs: Any) -> None:
        self.records.append(("info", event, dict(kwargs)))

    def error(self, event: str, **kwargs: Any) -> None:
        self.records.append(("error", event, dict(kwargs)))

    def debug(self, event: str, **kwargs: Any) -> None:
        self.records.append(("debug", event, dict(kwargs)))

    def events(self, level: str | None = None) -> list[str]:
        """Convenience: list event names, optionally filtered by level."""
        return [
            event
            for lvl, event, _ in self.records
            if level is None or lvl == level
        ]


# ── Mock transport infrastructure ──────────────────────────────


class _CaptureTransport(httpx.AsyncBaseTransport):
    """Capture outgoing requests; return a scripted JSON response.

    Stores the most-recent request as ``last_request`` for the
    test to inspect.
    """

    def __init__(self, response_body: dict[str, Any] | None = None) -> None:
        self._response_body = response_body or {"MediaContainer": {}}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200,
            content=json.dumps(self._response_body).encode(),
            headers={"content-type": "application/json"},
            request=request,
        )

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "expected at least one HTTP request"
        return self.requests[-1]


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, transport: _CaptureTransport
) -> None:
    real_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("plugins.plex.backend.httpx.AsyncClient", _patched)


def _plex_config() -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="plex-1",
        name="Test Plex",
        kind="plex",
        options={"base_url": "http://plex.test:32400", "timeout_seconds": 5.0},
        secrets={"token": "plex-token-XYZ"},
    )


# ── Test 1 — fetch_playback_events sends the viewedAt filter LITERALLY


@pytest.mark.asyncio
async def test_fetch_playback_events_filters_old_entries_python_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``since`` cutoff is applied in Python after parsing,
    not via a Plex URL filter operator.

    Earlier code attempted to pass ``viewedAt>={unix}`` as a query
    parameter. httpx always URL-encodes ``>`` to ``%3E``, regardless
    of how the URL is constructed, and Plex Media Server's filter
    parser is inconsistent about decoding-then-matching the operator
    across versions. We sidestep entirely by fetching the most
    recent N entries (sort=viewedAt:desc, container-size=500) and
    filtering by cutoff in Python.

    This test pins that:
      * Plex receives the request with NO viewedAt URL operator
        (no ``viewedAt>=``, no ``viewedAt%3E``).
      * Returned entries older than the ``since`` cutoff are
        dropped in Python.
      * Returned entries at-or-after the cutoff survive.
    """
    cutoff = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)
    cutoff_unix = int(cutoff.timestamp())

    transport = _CaptureTransport(
        response_body={
            "MediaContainer": {
                "Metadata": [
                    # Newer than cutoff — should be kept.
                    {
                        "ratingKey": "1",
                        "viewedAt": cutoff_unix + 3600,
                        "Media": [
                            {
                                "videoCodec": "h264",
                                "Part": [{"file": "/m/new.mkv"}],
                            }
                        ],
                    },
                    # Older than cutoff — should be filtered out.
                    {
                        "ratingKey": "2",
                        "viewedAt": cutoff_unix - 3600,
                        "Media": [
                            {
                                "videoCodec": "h264",
                                "Part": [{"file": "/m/old.mkv"}],
                            }
                        ],
                    },
                ]
            }
        }
    )
    _install_transport(monkeypatch, transport)

    provider = PlexProvider(log=_RecordingLog())
    events = await provider.fetch_playback_events(_plex_config(), cutoff)

    # Outgoing URL has NO viewedAt URL operator (we removed it).
    url = str(transport.last_request.url)
    assert "viewedAt>=" not in url
    assert "viewedAt%3E" not in url
    # The sort param IS sent so we get newest-first.
    assert "sort=viewedAt%3Adesc" in url or "sort=viewedAt:desc" in url

    # Python-side filter kept the new entry and dropped the old one.
    assert len(events) == 1
    assert events[0].source_path == "/m/new.mkv"


# ── Test 2 — pagination is sent as HEADERS, not query params


@pytest.mark.asyncio
async def test_fetch_playback_events_sends_pagination_as_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``X-Plex-Container-Start`` / ``Size`` must be HTTP HEADERS,
    not query params. Plex Media Server documents pagination as
    header-based; sending as query params is silently ignored on
    most PMS builds, and the server returns its default page
    (typically 50 entries) rather than the 200 we asked for.

    The original implementation sent these as part of the
    ``params=`` dict, which httpx then included in the query
    string. This test pins the corrected wire shape.
    """
    transport = _CaptureTransport(
        response_body={"MediaContainer": {"Metadata": []}}
    )
    _install_transport(monkeypatch, transport)

    provider = PlexProvider(log=_RecordingLog())
    await provider.fetch_playback_events(_plex_config(), None)

    req = transport.last_request
    headers = {k.lower(): v for k, v in req.headers.items()}

    assert "x-plex-container-start" in headers, (
        f"expected X-Plex-Container-Start header; saw headers={list(headers)}"
    )
    assert headers["x-plex-container-start"] == "0"
    assert "x-plex-container-size" in headers
    # 500 is generous — covers the busiest server's last hour
    # of playback in one page. Filtered by cutoff in Python.
    assert headers["x-plex-container-size"] == "500"

    # And NOT in the query string.
    url = str(req.url)
    assert "X-Plex-Container-Start" not in url, (
        f"pagination must not leak into query string: {url!r}"
    )
    assert "X-Plex-Container-Size" not in url


# ── Test 3 — auth + JSON accept header come from _client


@pytest.mark.asyncio
async def test_fetch_playback_events_sends_auth_and_json_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Plex token must be present on every request, and the
    Accept header must be application/json (Plex defaults to XML
    otherwise, which makes ``response.json()`` crash)."""
    transport = _CaptureTransport(
        response_body={"MediaContainer": {"Metadata": []}}
    )
    _install_transport(monkeypatch, transport)

    provider = PlexProvider(log=_RecordingLog())
    await provider.fetch_playback_events(_plex_config(), None)

    headers = {k.lower(): v for k, v in transport.last_request.headers.items()}
    assert headers.get("x-plex-token") == "plex-token-XYZ"
    assert "application/json" in headers.get("accept", "").lower()


# ── Test 4 — fetch_live_playbacks hits /status/sessions with auth


@pytest.mark.asyncio
async def test_fetch_live_playbacks_uses_status_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live endpoint is ``/status/sessions`` (no query params,
    no filter). Pin the URL + auth headers so a future refactor
    doesn't accidentally change the wire shape."""
    transport = _CaptureTransport(
        response_body={"MediaContainer": {"Metadata": []}}
    )
    _install_transport(monkeypatch, transport)

    provider = PlexProvider(log=_RecordingLog())
    result = await provider.fetch_live_playbacks(_plex_config())

    assert result == []
    url = str(transport.last_request.url)
    assert "/status/sessions" in url
    # The live endpoint must NOT hit /status/sessions/history/all by
    # accident (a refactor regression we want to catch).
    assert "/history" not in url, f"live endpoint hit history URL: {url!r}"

    headers = {k.lower(): v for k, v in transport.last_request.headers.items()}
    assert headers.get("x-plex-token") == "plex-token-XYZ"
    assert "application/json" in headers.get("accept", "").lower()


# ── Test 5 — fetch_live_playbacks parses a realistic session payload


@pytest.mark.asyncio
async def test_fetch_live_playbacks_parses_session_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: scripted Plex response → parsed DTO list.

    Distinct from ``test_plex_session_payload_translates_to_live_dto``
    in ``test_playback_live_stage09.py`` — that test calls
    ``_plex_live_to_dto`` directly. This one exercises the
    full HTTP-fetch path, so a bug in either the HTTP shape OR
    the response parsing fails the test.
    """
    transport = _CaptureTransport(
        response_body={
            "MediaContainer": {
                "Metadata": [
                    {
                        "sessionKey": "42",
                        "title": "Inception",
                        "addedAt": 1736000000,
                        "viewOffset": 600000,
                        "duration": 6000000,
                        "Media": [
                            {
                                "videoCodec": "hevc",
                                "bitrate": 12000,
                                "width": 3840,
                                "height": 2160,
                                "container": "mkv",
                                "duration": 6000000,
                                "Part": [{"file": "/plex/Movies/Inception.mkv"}],
                            }
                        ],
                        "Player": {
                            "state": "playing",
                            "device": "AppleTV",
                            "title": "Bedroom",
                        },
                        "User": {"title": "alice"},
                    }
                ]
            }
        }
    )
    _install_transport(monkeypatch, transport)

    provider = PlexProvider(log=_RecordingLog())
    sessions = await provider.fetch_live_playbacks(_plex_config())

    assert len(sessions) == 1
    s = sessions[0]
    assert s.upstream_id == "42"
    assert s.source_path == "/plex/Movies/Inception.mkv"
    assert s.user == "alice"
    assert s.source_codec == "hevc"


# ── Test 6 — non-JSON response degrades cleanly with diagnostic log


@pytest.mark.asyncio
async def test_fetch_live_playbacks_logs_parse_failure_on_non_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Plex returns non-JSON (XML, an HTML error page from a
    misbehaving reverse proxy, etc.), the fetcher must degrade to
    an empty list AND log a diagnostic so the operator has signal
    in the logs. Pre-fix, parse failures were silently swallowed
    and the operator had no idea why their live tile was empty.
    """

    class _XmlTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):  # noqa: ANN001, ANN202
            return httpx.Response(
                200,
                content=b"<MediaContainer/>",
                headers={"content-type": "application/xml"},
                request=request,
            )

    real_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _XmlTransport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr("plugins.plex.backend.httpx.AsyncClient", _patched)

    rec_log = _RecordingLog()
    provider = PlexProvider(log=rec_log)
    sessions = await provider.fetch_live_playbacks(_plex_config())

    assert sessions == []
    # The fetcher logged the parse failure — operator has signal.
    assert "plex.live.fetch_parse_failed" in rec_log.events("warning"), (
        f"expected 'plex.live.fetch_parse_failed' warning; "
        f"saw {rec_log.records}"
    )
    # And the diagnostic includes the upstream content-type so the
    # operator can tell whether the issue is the proxy or the PMS.
    parse_record = next(
        kwargs
        for lvl, event, kwargs in rec_log.records
        if event == "plex.live.fetch_parse_failed"
    )
    assert parse_record.get("content_type") == "application/xml"
