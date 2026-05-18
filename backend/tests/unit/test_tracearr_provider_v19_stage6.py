"""v1.9 Stage 6.4 — Tracearr integration plugin.

Pins:
  1. ``_map_tracearr_event`` happy path → fully-populated DTO.
  2. ``_map_tracearr_event`` missing required field → None
     (silent skip, not exception).
  3. ``_map_tracearr_event`` malformed started_at → None.
  4. ``healthcheck`` happy path → status="ok" with version detail.
  5. ``healthcheck`` non-ok status payload → status="degraded".
  6. ``healthcheck`` HTTP 5xx → status="error".
  7. ``healthcheck`` network error → status="error" (no raise).
  8. ``fetch_playback_events`` single page → list of DTOs,
     correct query params (limit + since).
  9. ``fetch_playback_events`` paginated → walks ``paging.next``
     cursors until exhausted, returns combined batch.
 10. ``fetch_playback_events`` pagination safety cap (50 iters)
     terminates a misbehaving upstream gracefully.
 11. ``discover_libraries`` and ``sync_tags`` are no-ops.
 12. ``trigger_search`` explicitly errors (read-only plugin).
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_tracearr():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "tracearr"
    spec = importlib.util.spec_from_file_location(
        "tracearr_plugin_backend_v19s6", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tracearr_plugin_backend_v19s6"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="trace",
        kind="tracearr",
        options={
            "base_url": "http://tracearr.test",
            **overrides.get("options", {}),
        },
        secrets={"api_key": "key123", **overrides.get("secrets", {})},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_tracearr()
    provider = mod.TracearrProvider(log=None)
    original = provider._client

    def patched(cfg: IntegrationConfig) -> httpx.AsyncClient:
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


# ── _map_tracearr_event ──────────────────────────────────────────


def test_map_event_happy_path() -> None:
    mod = _load_tracearr()
    dto = mod._map_tracearr_event(
        {
            "id": "evt-1",
            "source_path": "/data/movies/X.mkv",
            "started_at": "2026-05-17T10:00:00Z",
            "decision": "transcode",
            "client": {"name": "Plex Web", "platform": "Browser"},
            "media": {
                "codec": "hevc",
                "width": 1920,
                "height": 1080,
                "bitrate_kbps": 8200,
            },
        }
    )
    assert dto is not None
    assert dto.upstream_id == "evt-1"
    assert dto.source_path == "/data/movies/X.mkv"
    assert dto.decision == "transcode"
    assert dto.source_codec == "hevc"
    assert dto.source_width == 1920
    assert dto.source_height == 1080
    assert dto.source_bitrate_kbps == 8200
    assert dto.device_kind == "Browser"
    assert dto.device_name == "Plex Web"
    # Z suffix coerced to +00:00.
    assert dto.started_at == _dt.datetime(2026, 5, 17, 10, 0, tzinfo=_dt.UTC)


def test_map_event_missing_required_returns_none() -> None:
    """Tracearr can occasionally emit partial rows (e.g. a row
    written before the playback decision was finalized). Drop
    them silently rather than raising — the poller should keep
    ingesting the rest of the batch."""
    mod = _load_tracearr()
    assert mod._map_tracearr_event({}) is None
    assert (
        mod._map_tracearr_event({"id": "x", "started_at": "2026-01-01T00:00:00Z"})
        is None  # no source_path
    )
    assert (
        mod._map_tracearr_event(
            {"id": "x", "source_path": "/x", "decision": "direct_play"}
        )
        is None  # no started_at
    )


def test_map_event_malformed_started_at_returns_none() -> None:
    mod = _load_tracearr()
    assert (
        mod._map_tracearr_event(
            {
                "id": "x",
                "source_path": "/x",
                "decision": "direct_play",
                "started_at": "not-a-timestamp",
            }
        )
        is None
    )


# ── healthcheck ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_healthcheck_ok() -> None:
    """Default to ``/health`` (the most-common Tracearr build).
    Any 2xx body with ``status="ok"`` is reported healthy."""

    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        return httpx.Response(
            200, json={"status": "ok", "version": "1.2.3"}
        )

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "ok"
    assert "1.2.3" in (report.detail or "")
    # First-tried path is /health.
    assert seen_paths[0] == "/health"


@pytest.mark.asyncio
async def test_healthcheck_falls_back_when_first_path_404s() -> None:
    """v1.9 audit fix (OP-11): Tracearr builds expose
    /health, /api/health, /api/v1/health, or /status. When the
    first candidate 404s, the next is tried until one
    responds. Prevents the operator-visible "Tracearr /api/health
    returned HTTP 404" error when the configured build uses a
    different path."""

    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        # 404 on /health, OK on /api/health.
        if req.url.path == "/health":
            return httpx.Response(404)
        return httpx.Response(200, json={"status": "ok"})

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "ok"
    # Tried /health first, then /api/health.
    assert seen_paths == ["/health", "/api/health"]


@pytest.mark.asyncio
async def test_healthcheck_all_paths_404_returns_error() -> None:
    """If every known health path 404s, surface a clear error
    explaining what was tried."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"
    assert "none of the known health paths" in (report.detail or "")


@pytest.mark.asyncio
async def test_healthcheck_degraded_on_non_ok_status() -> None:
    """Tracearr exposing ``status: "degraded"`` (or any non-"ok"
    string) should surface as a degraded health report rather
    than a hard error."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "degraded", "version": "1.2.3"}
        )

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "degraded"


@pytest.mark.asyncio
async def test_healthcheck_error_on_http_5xx() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"
    assert "500" in (report.detail or "")


@pytest.mark.asyncio
async def test_healthcheck_error_on_network_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"


# ── fetch_playback_events ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_single_page() -> None:
    """One page returned, no ``paging.next`` → single request,
    DTOs returned. The since param threads through correctly."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["params"] = dict(req.url.params)
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "evt-1",
                        "source_path": "/x.mkv",
                        "started_at": "2026-05-17T10:00:00Z",
                        "decision": "direct_play",
                    },
                    {
                        "id": "evt-2",
                        "source_path": "/y.mkv",
                        "started_at": "2026-05-17T11:00:00Z",
                        "decision": "transcode",
                    },
                ],
                "paging": {},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    since = _dt.datetime(2026, 5, 1, tzinfo=_dt.UTC)
    events = await provider.fetch_playback_events(_config(), since)
    assert len(events) == 2
    assert {e.upstream_id for e in events} == {"evt-1", "evt-2"}
    # limit defaulted to 200, since serialized to ISO.
    assert captured["params"].get("limit") == "200"
    assert captured["params"].get("since") == since.isoformat()


@pytest.mark.asyncio
async def test_fetch_paginates_via_next_cursor() -> None:
    """Two pages joined via ``paging.next`` cursor → walker
    follows it and returns the union."""
    pages = [
        {
            "events": [
                {
                    "id": "evt-1",
                    "source_path": "/x.mkv",
                    "started_at": "2026-05-17T10:00:00Z",
                    "decision": "direct_play",
                }
            ],
            "paging": {"next": "cur-2"},
        },
        {
            "events": [
                {
                    "id": "evt-2",
                    "source_path": "/y.mkv",
                    "started_at": "2026-05-17T11:00:00Z",
                    "decision": "transcode",
                }
            ],
            "paging": {},
        },
    ]
    requests: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        requests.append(params)
        return httpx.Response(200, json=pages[len(requests) - 1])

    provider = _provider_with(httpx.MockTransport(handler))
    events = await provider.fetch_playback_events(_config(), None)
    assert len(events) == 2
    assert len(requests) == 2
    # First request has no cursor; second carries cur-2.
    assert "cursor" not in requests[0]
    assert requests[1]["cursor"] == "cur-2"


@pytest.mark.asyncio
async def test_fetch_terminates_on_safety_cap() -> None:
    """A misbehaving server that keeps emitting ``paging.next``
    forever should not loop indefinitely. The safety cap is 50
    iterations; we verify by emitting 60 self-pointing pages
    and asserting the loop terminates with exactly 50 results."""
    call_count = [0]

    def handler(_req: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": f"evt-{call_count[0]}",
                        "source_path": "/x.mkv",
                        "started_at": "2026-05-17T10:00:00Z",
                        "decision": "direct_play",
                    }
                ],
                "paging": {"next": f"cur-{call_count[0]}"},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    events = await provider.fetch_playback_events(_config(), None)
    assert len(events) == 50
    assert call_count[0] == 50


# ── No-ops ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_libraries_returns_empty() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    assert await provider.discover_libraries(_config()) == []


@pytest.mark.asyncio
async def test_sync_tags_returns_empty() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    assert await provider.sync_tags(_config()) == []


@pytest.mark.asyncio
async def test_trigger_search_returns_error() -> None:
    """Tracearr is read-only — pointing a ``search_upstream``
    rule action at a Tracearr integration should surface as
    status=error in the rule audit log, not raise."""
    provider = _provider_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    result = await provider.trigger_search(_config(), "/x.mkv")
    assert result.status == "error"
    assert "not accept search" in (result.detail or "").lower()
