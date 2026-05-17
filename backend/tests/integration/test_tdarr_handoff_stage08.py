"""Stage 08 (v1.7) — Tdarr provider transcode hand-off.

Plan §458:
    Mock the Tdarr API; queue an item with ``routing_target="tdarr"``;
    assert the worker calls the right endpoint with the right body,
    transitions to ``routed``, and ``poll_routed_transcodes`` moves
    it to ``completed`` when Tdarr reports done.

This file covers the PROVIDER side: the right HTTP calls go to
Tdarr with the right body shapes, response payloads are parsed
correctly, and provider-state values map to Auditarr's enum
faithfully. The WORKER side (queue → submit → poll → complete)
lives in ``test_worker_tdarr_handoff_stage08.py`` (built after
Stage 08 Layer 6).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.integrations.types import (
    IntegrationConfig,
    TranscodeJobSpec,
)
from plugins.tdarr.backend import TdarrProvider, _TDARR_STATE_TO_AUDITARR


# ── Helpers ────────────────────────────────────────────────────


def _provider() -> TdarrProvider:
    return TdarrProvider(log=None)


def _config(base_url: str = "http://tdarr.test:8265") -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="ig-1",
        name="tdarr-1",
        kind="tdarr",
        options={"base_url": base_url, "verify_ssl": False},
        secrets={},
    )


def _job_spec(**overrides: Any) -> TranscodeJobSpec:
    defaults = dict(
        item_id="opt-item-1",
        input_path="/media/Movies/example.mkv",
        transcode_scope="video_and_audio",
        video_codec="libx265",
        audio_codec="copy",
        container="mkv",
        metadata={"provider_profile_id": "Tdarr_Plugin_henk_h265"},
    )
    defaults.update(overrides)
    return TranscodeJobSpec(**defaults)  # type: ignore[arg-type]


class _MockTransport(httpx.AsyncBaseTransport):
    """Capture outgoing requests + serve scripted responses."""

    def __init__(self, handlers: list[tuple[str, Any]]) -> None:
        # Each handler: (url_path_substring, response_factory)
        # response_factory may be a callable(request) -> httpx.Response
        # or a static httpx.Response.
        self._handlers = handlers
        self.requests: list[httpx.Request] = []

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        self.requests.append(request)
        for pat, fac in self._handlers:
            if pat in str(request.url):
                if callable(fac):
                    return fac(request)
                return fac
        return httpx.Response(404, content=b'{"error":"unhandled"}')


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, transport: _MockTransport
) -> None:
    """Replace httpx.AsyncClient with one that uses our transport."""
    real_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("plugins.tdarr.backend.httpx.AsyncClient", _patched)


# ── State mapping ──────────────────────────────────────────────


def test_state_map_covers_documented_tdarr_stages() -> None:
    """Pin the documented Tdarr ``transcodeStage`` strings explicitly so
    a future change can't silently re-route a terminal state."""
    assert _TDARR_STATE_TO_AUDITARR["currently processing"] == "running"
    assert _TDARR_STATE_TO_AUDITARR["transcode success"] == "completed"
    assert _TDARR_STATE_TO_AUDITARR["transcode error"] == "failed"
    assert _TDARR_STATE_TO_AUDITARR["queued"] == "pending"
    assert _TDARR_STATE_TO_AUDITARR[""] == "pending"


# ── submit_transcode_job ───────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_rejects_when_no_provider_profile_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No plugin id picked → ``rejected`` with the documented
    operator-guidance message (plan §437)."""
    # No HTTP should happen.
    transport = _MockTransport([])
    _install_transport(monkeypatch, transport)

    result = await _provider().submit_transcode_job(
        _config(),
        _job_spec(metadata={}),  # no provider_profile_id
    )
    assert result.status == "rejected"
    assert result.detail is not None
    assert "plugin" in result.detail.lower()
    assert transport.requests == []


@pytest.mark.asyncio
async def test_submit_rejects_when_provider_profile_id_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport([])
    _install_transport(monkeypatch, transport)

    result = await _provider().submit_transcode_job(
        _config(),
        _job_spec(metadata={"provider_profile_id": ""}),
    )
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_submit_posts_to_cruddb_filejson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: provider hits POST /api/v2/cruddb with
    collection=FileJSONDB and mode=insert, carries the picked
    plugin id, returns the inserted doc's _id."""
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        [{"_id": "tdarr-job-42", "file": "/media/x.mkv"}]
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    result = await _provider().submit_transcode_job(
        _config(),
        _job_spec(),
    )
    assert result.status == "accepted"
    assert result.upstream_job_id == "tdarr-job-42"

    # Verify the request shape.
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v2/cruddb"
    body = json.loads(req.content)
    data = body["data"]
    assert data["collection"] == "FileJSONDB"
    assert data["mode"] == "insert"
    doc = data["docs"][0]
    assert doc["file"] == "/media/Movies/example.mkv"
    assert doc["transcodeChosenPlugin"] == "Tdarr_Plugin_henk_h265"
    # Auditarr correlation metadata is included.
    assert doc["auditarr_item_id"] == "opt-item-1"
    assert doc["auditarr_transcode_scope"] == "video_and_audio"


@pytest.mark.asyncio
async def test_submit_handles_wrapped_docs_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tdarr's response shape varies; ``{docs: [...]}`` parses too."""
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {"docs": [{"_id": "tdarr-99"}]}
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "accepted"
    assert result.upstream_job_id == "tdarr-99"


@pytest.mark.asyncio
async def test_submit_handles_http_error_as_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network / 5xx → ``status="error"`` (worker will re-enqueue)."""
    transport = _MockTransport(
        [("/api/v2/cruddb", httpx.Response(503, content=b"down"))],
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "error"
    assert result.detail is not None


@pytest.mark.asyncio
async def test_submit_rejects_when_response_lacks_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tdarr accepted the write but returned an unparseable body —
    we error because we can't correlate completion."""
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(200, content=b"{}"),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "error"
    assert "job id" in (result.detail or "").lower()


# ── list_transcode_profiles ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_profiles_queries_pluginsjsondb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        [
                            {
                                "id": "Tdarr_Plugin_henk_h265",
                                "Name": "henk: convert to h265",
                                "Description": "Re-encode using x265.",
                                "Type": "Video",
                                "Stage": "Pre-processing",
                            },
                            {
                                "id": "Tdarr_Plugin_lol_remux",
                                "Name": "lol: remux to mkv",
                                "Description": "Just remux.",
                            },
                        ]
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    profiles = await _provider().list_transcode_profiles(_config())

    # Right collection queried.
    body = json.loads(transport.requests[0].content)
    assert body["data"]["collection"] == "PluginsJSONDB"
    assert body["data"]["mode"] == "getAll"

    assert len(profiles) == 2
    assert profiles[0].id == "Tdarr_Plugin_henk_h265"
    assert profiles[0].name == "henk: convert to h265"
    assert profiles[0].description == "Re-encode using x265."
    assert profiles[0].metadata["Type"] == "Video"


@pytest.mark.asyncio
async def test_list_profiles_returns_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [("/api/v2/cruddb", httpx.Response(503, content=b"down"))],
    )
    _install_transport(monkeypatch, transport)
    profiles = await _provider().list_transcode_profiles(_config())
    assert profiles == []


@pytest.mark.asyncio
async def test_list_profiles_skips_entries_without_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        [
                            {"id": "a", "Name": "A"},
                            {"Name": "no-id"},
                            "not-a-dict",
                        ]
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    profiles = await _provider().list_transcode_profiles(_config())
    assert len(profiles) == 1
    assert profiles[0].id == "a"


# ── get_transcode_job_status ───────────────────────────────────


@pytest.mark.asyncio
async def test_status_maps_currently_processing_to_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "_id": "tdarr-1",
                            "transcodeStage": "Currently processing",
                            "transcodePercent": 35,
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(
        _config(), "tdarr-1"
    )
    assert status.status == "running"
    assert status.progress_pct == 35

    # Right query parameters.
    body = json.loads(transport.requests[0].content)
    assert body["data"]["collection"] == "FileJSONDB"
    assert body["data"]["mode"] == "getById"
    assert body["data"]["docID"] == "tdarr-1"


@pytest.mark.asyncio
async def test_status_maps_transcode_success_to_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "_id": "tdarr-1",
                            "transcodeStage": "Transcode success",
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.status == "completed"


@pytest.mark.asyncio
async def test_status_maps_transcode_error_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "_id": "tdarr-1",
                            "transcodeStage": "Transcode error",
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.status == "failed"


@pytest.mark.asyncio
async def test_status_unknown_stage_returns_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tdarr can report any string here; unknown maps to "unknown"
    so the worker keeps polling rather than guessing terminal."""
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "_id": "tdarr-1",
                            "transcodeStage": "Some Future Stage",
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.status == "unknown"


@pytest.mark.asyncio
async def test_status_http_error_returns_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport errors flow through as ``unknown`` — the worker
    keeps polling and the next tick may succeed."""
    transport = _MockTransport(
        [("/api/v2/cruddb", httpx.Response(503, content=b"down"))],
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.status == "unknown"
    assert status.detail is not None


@pytest.mark.asyncio
async def test_status_handles_wrapped_docs_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {"docs": [{"_id": "tdarr-1", "transcodeStage": "queued"}]}
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.status == "pending"


@pytest.mark.asyncio
async def test_status_clamps_progress_to_0_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/api/v2/cruddb",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "_id": "tdarr-1",
                            "transcodeStage": "Currently processing",
                            "transcodePercent": 250,  # out of range
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(_config(), "tdarr-1")
    assert status.progress_pct == 100
