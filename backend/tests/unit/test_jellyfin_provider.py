"""Jellyfin provider tests via httpx.MockTransport."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_module():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "jellyfin"
    spec = importlib.util.spec_from_file_location(
        "jellyfin_plugin_backend", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["jellyfin_plugin_backend"] = module
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="jellyfin",
        kind="jellyfin",
        options={"base_url": "http://jelly.test", **overrides.get("options", {})},
        secrets={"api_key": "key", **overrides.get("secrets", {})},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_module()
    provider = mod.JellyfinProvider(log=None)
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
        assert request.headers["X-Emby-Token"] == "key"
        assert request.url.path == "/System/Info"
        return httpx.Response(
            200,
            json={
                "ServerName": "home-jelly",
                "Version": "10.9.0",
                "OperatingSystem": "Linux",
                "Id": "abc-123",
            },
        )

    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "ok"
    assert report.detail == "home-jelly"
    assert report.metadata["version"] == "10.9.0"


@pytest.mark.asyncio
async def test_healthcheck_unauthorized() -> None:
    handler = lambda _r: httpx.Response(401)
    report = await _provider_with(httpx.MockTransport(handler)).healthcheck(_config())
    assert report.status == "error"
    assert "401" in (report.detail or "")


@pytest.mark.asyncio
async def test_discover_libraries_multi_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/Library/VirtualFolders"
        return httpx.Response(
            200,
            json=[
                {
                    "Name": "Movies",
                    "CollectionType": "movies",
                    "ItemId": "m1",
                    "Locations": ["/data/movies", "/data/4k"],
                },
                {
                    "Name": "TV",
                    "CollectionType": "tvshows",
                    "ItemId": "t1",
                    "Locations": ["/data/tv"],
                },
                {
                    "Name": "Mixed Bag",
                    "CollectionType": None,
                    "ItemId": "mb",
                    "Locations": [],
                },
            ],
        )

    libs = await _provider_with(httpx.MockTransport(handler)).discover_libraries(
        _config()
    )
    pairs = {(l.kind, l.root_path) for l in libs}
    assert ("movies", "/data/movies") in pairs
    assert ("movies", "/data/4k") in pairs
    assert ("tv", "/data/tv") in pairs
    # The library with no locations still emits an entry (without root_path).
    assert any(l.name == "Mixed Bag" and l.root_path is None for l in libs)


@pytest.mark.asyncio
async def test_sync_tags_returns_empty() -> None:
    provider = _provider_with(httpx.MockTransport(lambda _r: httpx.Response(200)))
    assert await provider.sync_tags(_config()) == []
