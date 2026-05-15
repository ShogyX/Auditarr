"""Tdarr provider tests via httpx.MockTransport."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_module():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "tdarr"
    spec = importlib.util.spec_from_file_location(
        "tdarr_plugin_backend", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tdarr_plugin_backend"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="tdarr",
        kind="tdarr",
        options={"base_url": "http://tdarr.test", **overrides.get("options", {})},
        secrets=overrides.get("secrets", {}),
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_module()
    provider = mod.TdarrProvider(log=None)
    original = provider._client

    def patched(cfg):
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_healthcheck_all_nodes_online() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/status"
        assert "Authorization" not in request.headers  # no token configured
        return httpx.Response(
            200,
            json={
                "nodes": [
                    {"status": "online", "version": "2.20.00"},
                    {"status": "online", "version": "2.20.00"},
                ]
            },
        )

    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.metadata["nodes"] == 2
    assert report.metadata["version"] == "2.20.00"


@pytest.mark.asyncio
async def test_healthcheck_degraded_when_node_offline() -> None:
    handler = lambda _r: httpx.Response(
        200,
        json={"nodes": [{"status": "online", "version": "2.20"}, {"status": "offline"}]},
    )
    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "degraded"
    assert "1 of 2" in (report.detail or "")


@pytest.mark.asyncio
async def test_healthcheck_token_sent_when_present() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"nodes": []})

    provider = _provider_with(httpx.MockTransport(handler))
    cfg = _config(secrets={"token": "secret-token"})
    await provider.healthcheck(cfg)
    assert seen["auth"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_discover_libraries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/cruddb"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json=[
                {
                    "_id": "lib1",
                    "name": "Movies",
                    "folder": "/data/movies",
                    "scanFoundCount": 12345,
                    "transcodeQueue": 17,
                },
                {
                    "_id": "lib2",
                    "name": "TV",
                    "folder": "/data/tv",
                },
                {
                    "_id": "lib3",
                    "folder": None,  # skipped
                },
            ],
        )

    libs = await _provider_with(httpx.MockTransport(handler)).discover_libraries(
        _config()
    )
    assert {(l.name, l.root_path) for l in libs} == {
        ("Movies", "/data/movies"),
        ("TV", "/data/tv"),
    }
    # All Tdarr libraries report kind="mixed"
    assert all(l.kind == "mixed" for l in libs)


@pytest.mark.asyncio
async def test_sync_tags_returns_empty() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _r: httpx.Response(200)))
    assert await provider.sync_tags(_config()) == []
