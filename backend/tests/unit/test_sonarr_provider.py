"""Sonarr provider tests using httpx.MockTransport."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_module():
    import importlib.util

    sonarr_dir = Path(__file__).resolve().parents[2] / "plugins" / "sonarr"
    spec = importlib.util.spec_from_file_location(
        "sonarr_plugin_backend", sonarr_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sonarr_plugin_backend"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="sonarr",
        kind="sonarr",
        options={"base_url": "http://sonarr.test", **overrides.get("options", {})},
        secrets={"api_key": "key123", **overrides.get("secrets", {})},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_module()
    provider = mod.SonarrProvider(log=None)
    original = provider._client

    def patched(cfg: IntegrationConfig) -> httpx.AsyncClient:
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key123"
        assert request.url.path == "/api/v3/system/status"
        return httpx.Response(
            200,
            json={
                "instanceName": "Home Sonarr",
                "version": "4.0.0",
                "branch": "main",
                "appData": "/config",
            },
        )

    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.detail == "Home Sonarr"
    assert report.metadata["version"] == "4.0.0"


@pytest.mark.asyncio
async def test_healthcheck_unauthorized() -> None:
    handler = lambda _r: httpx.Response(401, json={})
    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "error"
    assert "401" in (report.detail or "")


@pytest.mark.asyncio
async def test_discover_root_folders() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/rootfolder"
        return httpx.Response(
            200,
            json=[
                {"id": 1, "path": "/data/tv", "accessible": True, "freeSpace": 1_000_000},
                {"id": 2, "path": "/data/anime", "accessible": True, "freeSpace": 500_000},
            ],
        )

    libs = await _provider_with(httpx.MockTransport(handler)).discover_libraries(_config())
    assert {(l.kind, l.root_path) for l in libs} == {
        ("tv", "/data/tv"),
        ("tv", "/data/anime"),
    }


@pytest.mark.asyncio
async def test_sync_tags_expands_series_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/tag":
            return httpx.Response(200, json=[{"id": 1, "label": "4k"}, {"id": 2, "label": "anime"}])
        if request.url.path == "/api/v3/series":
            return httpx.Response(
                200,
                json=[
                    {"id": 100, "path": "/data/tv/Show A", "tags": [1, 2]},
                    {"id": 101, "path": "/data/tv/Show B", "tags": [1]},
                    {"id": 102, "path": "/data/tv/Show C", "tags": []},
                    {"id": 103, "path": None, "tags": [1]},  # no path → skipped
                ],
            )
        return httpx.Response(404)

    tags = await _provider_with(httpx.MockTransport(handler)).sync_tags(_config())
    by_pair = {(t.media_path, t.tag) for t in tags}
    assert by_pair == {
        ("/data/tv/Show A", "4k"),
        ("/data/tv/Show A", "anime"),
        ("/data/tv/Show B", "4k"),
    }


@pytest.mark.asyncio
async def test_sync_tags_disabled_returns_empty() -> None:
    handler = lambda _r: httpx.Response(500)
    provider = _provider_with(httpx.MockTransport(handler))
    # Even with a broken upstream, the disabled flag short-circuits.
    cfg = _config(options={"sync_tags_per_file": False})
    assert await provider.sync_tags(cfg) == []


@pytest.mark.asyncio
async def test_missing_api_key() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _r: httpx.Response(200)))
    report = await provider.healthcheck(
        IntegrationConfig(
            integration_id="i",
            name="x",
            kind="sonarr",
            options={"base_url": "http://x"},
            secrets={},
        )
    )
    assert report.status == "error"
    assert "api_key" in (report.detail or "").lower()
