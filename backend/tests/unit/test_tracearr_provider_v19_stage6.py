"""Tracearr integration plugin tests.

Auditarr polls Tracearr's read-only public API at
``/api/v1/public/history``. Tracearr's response shape, query
parameters, and authentication are sourced from
``apps/server/src/routes/public.ts`` upstream
(github.com/connorgallopo/Tracearr).

Pins:
  Mapping
    1.  Happy path → fully-populated DTO with synthesised
        ``source_path``.
    2.  Missing ``id`` → None.
    3.  Missing ``startedAt`` → None.
    4.  Malformed ``startedAt`` → None.
    5.  Decision mapping (direct_play / direct_stream /
        transcode).
    6.  ``source_path`` synthesis for episode, track, movie.

  Healthcheck
    7.  Happy path on ``/health`` → status="ok".
    8.  Fallback when ``/health`` 404s → next path used.
    9.  401 on an authenticated probe → status="degraded" with
        token-format hint (helps the operator who pasted the
        wrong token).
    10. All 404 → status="error".
    11. ``status: "degraded"`` payload → status="degraded".
    12. HTTP 5xx → status="error".
    13. Network error → status="error" (no raise).

  fetch_playback_events
    14. Single page → DTOs, query params include pageSize,
        timezone=UTC, startDate (date-granularity) when ``since``
        is provided.
    15. No ``since`` → no startDate param.
    16. Multi-page → loops until page*pageSize ≥ meta.total.
    17. Empty data → break immediately, no extra page.
    18. Page-iter safety cap terminates a misbehaving upstream.

  Misc
    19. ``discover_libraries`` and ``sync_tags`` are no-ops.
    20. ``trigger_search`` explicitly errors (read-only plugin).
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
        secrets={"api_key": "trr_pub_test", **overrides.get("secrets", {})},
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


def _row(**overrides) -> dict:
    """Minimal Tracearr /history row, shaped like the upstream
    response. Tests override individual fields."""
    row = {
        "id": "play-1",
        "serverId": "srv-1",
        "serverName": "Main Plex",
        "state": "stopped",
        "mediaType": "movie",
        "mediaTitle": "Inception",
        "showTitle": None,
        "seasonNumber": None,
        "episodeNumber": None,
        "year": 2010,
        "startedAt": "2026-05-17T10:00:00.000Z",
        "stoppedAt": "2026-05-17T12:28:00.000Z",
        "durationMs": 8880000,
        "platform": "tvOS",
        "player": "Plex for Apple TV",
        "device": "Apple TV",
        "isTranscode": False,
        "videoDecision": "directplay",
        "audioDecision": "directplay",
        "bitrate": 8200,
        "sourceVideoCodec": "hevc",
        "sourceVideoWidth": 1920,
        "sourceVideoHeight": 1080,
        "sourceVideoDetails": {"bitrate": 8200},
        "streamVideoCodec": "hevc",
        "streamVideoDetails": {"bitrate": 8200},
        "transcodeInfo": None,
    }
    row.update(overrides)
    return row


# ── _map_tracearr_event ──────────────────────────────────────────


def test_map_event_happy_path() -> None:
    mod = _load_tracearr()
    dto = mod._map_tracearr_event(_row())
    assert dto is not None
    assert dto.upstream_id == "play-1"
    assert dto.decision == "direct_play"
    assert dto.source_codec == "hevc"
    assert dto.source_width == 1920
    assert dto.source_height == 1080
    assert dto.source_bitrate_kbps == 8200
    assert dto.target_codec == "hevc"
    assert dto.target_bitrate_kbps == 8200
    assert dto.device_kind == "tvOS"
    assert dto.device_name == "Plex for Apple TV"
    assert dto.started_at == _dt.datetime(2026, 5, 17, 10, 0, tzinfo=_dt.UTC)
    assert dto.completed_at == _dt.datetime(2026, 5, 17, 12, 28, tzinfo=_dt.UTC)
    assert dto.duration_s == 8880  # 8880000 ms
    # Synthesised source_path carries serverId + media type + leaf.
    assert dto.source_path.startswith("tracearr://srv-1/movie/")
    assert "Inception (2010)" in dto.source_path


def test_map_event_missing_id_returns_none() -> None:
    mod = _load_tracearr()
    assert mod._map_tracearr_event(_row(id=None)) is None


def test_map_event_missing_started_at_returns_none() -> None:
    mod = _load_tracearr()
    assert mod._map_tracearr_event(_row(startedAt=None)) is None
    assert mod._map_tracearr_event(_row(startedAt="")) is None


def test_map_event_malformed_started_at_returns_none() -> None:
    mod = _load_tracearr()
    assert mod._map_tracearr_event(_row(startedAt="not-a-timestamp")) is None


def test_decision_directplay_when_both_tracks_directplay() -> None:
    mod = _load_tracearr()
    row = _row(videoDecision="directplay", audioDecision="directplay", isTranscode=False)
    assert mod._decision_from(row) == "direct_play"


def test_decision_transcode_when_video_transcoded() -> None:
    mod = _load_tracearr()
    row = _row(videoDecision="transcode", audioDecision="directplay", isTranscode=True)
    assert mod._decision_from(row) == "transcode"


def test_decision_transcode_when_only_isTranscode_true() -> None:
    """Older Tracearr rows may have ``isTranscode=true`` with
    ``videoDecision``/``audioDecision`` left null."""
    mod = _load_tracearr()
    row = _row(videoDecision=None, audioDecision=None, isTranscode=True)
    assert mod._decision_from(row) == "transcode"


def test_decision_direct_stream_when_audio_copy_only() -> None:
    mod = _load_tracearr()
    row = _row(videoDecision="directplay", audioDecision="copy", isTranscode=False)
    assert mod._decision_from(row) == "direct_stream"


def test_synth_source_path_for_episode() -> None:
    mod = _load_tracearr()
    path = mod._synth_source_path(
        _row(
            mediaType="episode",
            showTitle="Breaking Bad",
            seasonNumber=5,
            episodeNumber=16,
            mediaTitle="Felina",
            year=2013,
        )
    )
    assert path == "tracearr://srv-1/episode/Breaking Bad/S05E16 — Felina"


def test_synth_source_path_for_track() -> None:
    mod = _load_tracearr()
    path = mod._synth_source_path(
        _row(
            mediaType="track",
            mediaTitle="Time",
            artistName="Pink Floyd",
            albumName="The Dark Side of the Moon",
        )
    )
    assert (
        path
        == "tracearr://srv-1/track/Pink Floyd/The Dark Side of the Moon/Time"
    )


def test_synth_source_path_without_year() -> None:
    mod = _load_tracearr()
    path = mod._synth_source_path(_row(year=None, mediaTitle="Inception"))
    assert path == "tracearr://srv-1/movie/Inception"


def test_map_event_reason_code_from_transcode_info() -> None:
    mod = _load_tracearr()
    dto = mod._map_tracearr_event(
        _row(
            videoDecision="transcode",
            isTranscode=True,
            transcodeInfo={"reasons": ["video.codec.unsupported", "bitrate.cap"]},
        )
    )
    assert dto is not None
    assert dto.reason_code == "video.codec.unsupported,bitrate.cap"


# ── healthcheck ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_healthcheck_ok_on_unauthenticated_health() -> None:
    """Tracearr's primary unauthenticated probe is ``/health``."""
    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        return httpx.Response(
            200, json={"status": "ok", "version": "0.9.2"}
        )

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "ok"
    assert "0.9.2" in (report.detail or "")
    assert seen_paths[0] == "/health"


@pytest.mark.asyncio
async def test_healthcheck_falls_back_when_first_path_404s() -> None:
    """Builds that lack ``/health`` (legacy / custom proxy setups)
    should still surface healthy via a later candidate."""
    seen_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        if req.url.path == "/health":
            return httpx.Response(404)
        return httpx.Response(200, json={"status": "ok"})

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "ok"
    # Tried /health first, then /api/v1/public/health.
    assert seen_paths == ["/health", "/api/v1/public/health"]


@pytest.mark.asyncio
async def test_healthcheck_401_surfaces_token_hint() -> None:
    """An authenticated probe returning 401 is the operator's
    most likely failure mode (wrong token format pasted into the
    api_key field). The detail must point at Settings > General
    and the ``trr_pub_`` prefix."""

    def handler(req: httpx.Request) -> httpx.Response:
        # Skip /health to force the next candidate to authenticate.
        if req.url.path == "/health":
            return httpx.Response(404)
        return httpx.Response(401, json={"error": "Unauthorized"})

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "degraded"
    detail = report.detail or ""
    assert "401" in detail
    assert "trr_pub_" in detail


@pytest.mark.asyncio
async def test_healthcheck_all_paths_404_returns_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = _provider_with(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"
    assert "none of the known health paths" in (report.detail or "")


@pytest.mark.asyncio
async def test_healthcheck_degraded_on_non_ok_status() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "degraded", "version": "0.9.2"}
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
async def test_fetch_single_page_with_since_passes_startdate() -> None:
    """One page returned, ``meta.total`` matches page size → no
    second page is requested. The since cutoff threads through as
    a UTC date string + timezone=UTC."""
    captured: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/public/history"
        captured.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={
                "data": [
                    _row(id="evt-1", startedAt="2026-05-17T10:00:00Z"),
                    _row(id="evt-2", startedAt="2026-05-17T11:00:00Z"),
                ],
                "meta": {"total": 2, "page": 1, "pageSize": 100},
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    since = _dt.datetime(2026, 5, 15, 6, 30, tzinfo=_dt.UTC)
    events = await provider.fetch_playback_events(_config(), since)
    assert {e.upstream_id for e in events} == {"evt-1", "evt-2"}
    assert len(captured) == 1
    assert captured[0]["page"] == "1"
    assert captured[0]["pageSize"] == "100"
    assert captured[0]["timezone"] == "UTC"
    # since=2026-05-15T06:30:00Z → startDate=2026-05-15
    assert captured[0]["startDate"] == "2026-05-15"


@pytest.mark.asyncio
async def test_fetch_no_since_omits_startdate() -> None:
    captured: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={"data": [], "meta": {"total": 0, "page": 1, "pageSize": 100}},
        )

    provider = _provider_with(httpx.MockTransport(handler))
    await provider.fetch_playback_events(_config(), None)
    assert "startDate" not in captured[0]


@pytest.mark.asyncio
async def test_fetch_walks_until_total_reached() -> None:
    """Two pages of 25, ``meta.total=50`` → loop fetches page 1
    then page 2, then stops because page*pageSize >= total."""
    rows_per_page = 25
    requests: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        requests.append(params)
        page = int(params["page"])
        data = [
            _row(id=f"evt-{page}-{i}", startedAt="2026-05-17T10:00:00Z")
            for i in range(rows_per_page)
        ]
        return httpx.Response(
            200,
            json={
                "data": data,
                "meta": {"total": 50, "page": page, "pageSize": rows_per_page},
            },
        )

    provider = _provider_with(
        httpx.MockTransport(handler)
    )
    events = await provider.fetch_playback_events(
        _config(options={"page_size": rows_per_page}), None
    )
    assert len(events) == 50
    assert [r["page"] for r in requests] == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_empty_data_breaks_immediately() -> None:
    """Empty ``data`` even with a misreported ``meta.total`` must
    not loop — the safety break on ``not items`` covers
    misbehaving servers that report total but return nothing."""
    call_count = [0]

    def handler(_req: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(
            200,
            json={"data": [], "meta": {"total": 1000, "page": 1, "pageSize": 100}},
        )

    provider = _provider_with(httpx.MockTransport(handler))
    events = await provider.fetch_playback_events(_config(), None)
    assert events == []
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_fetch_terminates_on_iter_cap() -> None:
    """A server that always reports total > offset will loop —
    the safety cap (50) is the backstop."""
    call_count = [0]

    def handler(_req: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(
            200,
            json={
                "data": [
                    _row(
                        id=f"evt-{call_count[0]}",
                        startedAt="2026-05-17T10:00:00Z",
                    )
                ],
                "meta": {
                    "total": 10_000,
                    "page": call_count[0],
                    "pageSize": 100,
                },
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    events = await provider.fetch_playback_events(_config(), None)
    assert len(events) == 50
    assert call_count[0] == 50


@pytest.mark.asyncio
async def test_fetch_clamps_oversized_page_size() -> None:
    """Operators may misconfigure ``page_size`` > 100; Tracearr
    rejects with HTTP 400. Clamp client-side."""
    captured: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={"data": [], "meta": {"total": 0, "page": 1, "pageSize": 100}},
        )

    provider = _provider_with(httpx.MockTransport(handler))
    await provider.fetch_playback_events(
        _config(options={"page_size": 500}), None
    )
    assert captured[0]["pageSize"] == "100"


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
    provider = _provider_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    result = await provider.trigger_search(_config(), "/x.mkv")
    assert result.status == "error"
    assert "does not accept search" in (result.detail or "")
