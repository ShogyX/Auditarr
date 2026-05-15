"""Radarr provider tests using httpx.MockTransport."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_module():
    import importlib.util

    radarr_dir = Path(__file__).resolve().parents[2] / "plugins" / "radarr"
    spec = importlib.util.spec_from_file_location(
        "radarr_plugin_backend", radarr_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["radarr_plugin_backend"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="radarr",
        kind="radarr",
        options={"base_url": "http://radarr.test", **overrides.get("options", {})},
        secrets={"api_key": "key123", **overrides.get("secrets", {})},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_module()
    provider = mod.RadarrProvider(log=None)
    original = provider._client

    def patched(cfg):
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/system/status"
        return httpx.Response(
            200, json={"instanceName": "Home Radarr", "version": "5.0.0", "branch": "main"}
        )

    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.metadata["version"] == "5.0.0"


@pytest.mark.asyncio
async def test_discover_libraries_marks_movies() -> None:
    handler = lambda _r: httpx.Response(
        200, json=[{"id": 1, "path": "/data/movies", "accessible": True, "freeSpace": 100}]
    )
    libs = await _provider_with(httpx.MockTransport(handler)).discover_libraries(_config())
    assert [l.kind for l in libs] == ["movies"]
    assert libs[0].root_path == "/data/movies"


@pytest.mark.asyncio
async def test_sync_tags_radarr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/tag":
            return httpx.Response(200, json=[{"id": 7, "label": "remux"}])
        if request.url.path == "/api/v3/movie":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "path": "/data/movies/Heat (1995)", "tags": [7]},
                    {"id": 2, "path": "/data/movies/Dune (2021)", "tags": []},
                ],
            )
        return httpx.Response(404)

    tags = await _provider_with(httpx.MockTransport(handler)).sync_tags(_config())
    assert [(t.media_path, t.tag) for t in tags] == [
        ("/data/movies/Heat (1995)", "remux")
    ]
