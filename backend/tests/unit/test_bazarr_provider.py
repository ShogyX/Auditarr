"""Bazarr provider tests via httpx.MockTransport."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_module():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "bazarr"
    spec = importlib.util.spec_from_file_location(
        "bazarr_plugin_backend", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["bazarr_plugin_backend"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="bazarr",
        kind="bazarr",
        options={"base_url": "http://bazarr.test", **overrides.get("options", {})},
        secrets={"api_key": "key", **overrides.get("secrets", {})},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_module()
    provider = mod.BazarrProvider(log=None)
    original = provider._client

    def patched(cfg):
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_healthcheck_ok_flat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "key"
        assert request.url.path == "/api/system/status"
        return httpx.Response(
            200,
            json={
                "bazarr_version": "1.4.0",
                "instance_name": "home-bazarr",
                "sonarr_signalr_connected": True,
                "radarr_signalr_connected": True,
            },
        )

    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.detail == "home-bazarr"
    assert report.metadata["version"] == "1.4.0"
    assert report.metadata["sonarr_connected"] is True


@pytest.mark.asyncio
async def test_healthcheck_wrapped_in_data() -> None:
    handler = lambda _r: httpx.Response(
        200, json={"data": {"bazarr_version": "1.4.2", "instance_name": "alt"}}
    )
    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.metadata["version"] == "1.4.2"


@pytest.mark.asyncio
async def test_discover_returns_empty() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _r: httpx.Response(200)))
    assert await provider.discover_libraries(_config()) == []


@pytest.mark.asyncio
async def test_sync_tags_emits_missing_subs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/series":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "path": "/data/tv/Breaking Bad",
                            "missing_subtitles": [
                                {"code2": "en", "name": "English"},
                                {"code2": "es", "name": "Spanish"},
                            ],
                        },
                        {
                            "id": 2,
                            "path": "/data/tv/Some Show",
                            "missing_subtitles": [],
                        },
                        {
                            "id": 3,
                            "path": None,
                            "missing_subtitles": [{"code2": "en"}],
                        },
                    ]
                },
            )
        if request.url.path == "/api/movies":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 10,
                            "path": "/data/movies/Heat (1995)",
                            "missing_subtitles": ["fr"],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    tags = await _provider_with(httpx.MockTransport(handler)).sync_tags(_config())
    pairs = {(t.media_path, t.tag) for t in tags}
    assert pairs == {
        ("/data/tv/Breaking Bad", "missing-subs:en"),
        ("/data/tv/Breaking Bad", "missing-subs:es"),
        ("/data/movies/Heat (1995)", "missing-subs:fr"),
    }


@pytest.mark.asyncio
async def test_sync_tags_disabled() -> None:
    handler = lambda _r: httpx.Response(500)  # would fail if called
    provider = _provider_with(httpx.MockTransport(handler))
    cfg = _config(options={"sync_missing_subs": False})
    assert await provider.sync_tags(cfg) == []
