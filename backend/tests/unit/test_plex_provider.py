"""Plex provider tests using httpx.MockTransport (no real server)."""

from __future__ import annotations

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _config(**overrides) -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i1",
        name="plex-prod",
        kind="plex",
        options={"base_url": "http://plex.test", **overrides.get("options", {})},
        secrets={"token": "abc123", **overrides.get("secrets", {})},
    )


def _make_provider(transport: httpx.MockTransport):
    """Build a PlexProvider whose AsyncClient uses our MockTransport."""
    import importlib.util
    import sys
    from pathlib import Path

    plex_dir = Path(__file__).resolve().parents[2] / "plugins" / "plex"
    spec = importlib.util.spec_from_file_location(
        "plex_plugin_backend", plex_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    plex_backend = importlib.util.module_from_spec(spec)
    sys.modules["plex_plugin_backend"] = plex_backend
    spec.loader.exec_module(plex_backend)

    provider = plex_backend.PlexProvider(log=None)
    original = provider._client

    def patched(config: IntegrationConfig) -> httpx.AsyncClient:
        c = original(config)
        # Swap the transport without otherwise touching the client.
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Plex-Token") == "abc123"
        assert request.url.path == "/identity"
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "friendlyName": "homeplex",
                    "machineIdentifier": "abc",
                    "version": "1.40.0",
                    "platform": "Linux",
                }
            },
        )

    provider = _make_provider(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "ok"
    assert report.detail == "homeplex"
    assert report.metadata["version"] == "1.40.0"


@pytest.mark.asyncio
async def test_healthcheck_token_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    provider = _make_provider(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"
    assert "401" in (report.detail or "")


@pytest.mark.asyncio
async def test_healthcheck_network_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _make_provider(httpx.MockTransport(handler))
    report = await provider.healthcheck(_config())
    assert report.status == "error"
    assert "HTTP error" in (report.detail or "")


@pytest.mark.asyncio
async def test_discover_libraries() -> None:
    payload = {
        "MediaContainer": {
            "Directory": [
                {
                    "key": "1",
                    "type": "movie",
                    "title": "Movies",
                    "Location": [{"path": "/data/movies"}],
                    "agent": "tv.plex.agents.movie",
                    "scanner": "Plex Movie",
                    "language": "en-US",
                    "uuid": "uuid-movies",
                },
                {
                    "key": "2",
                    "type": "show",
                    "title": "TV",
                    "Location": [{"path": "/data/tv"}],
                },
                {
                    "key": "3",
                    "type": "artist",
                    "title": "Music",
                    "Location": [{"path": "/data/music"}],
                },
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/library/sections"
        return httpx.Response(200, json=payload)

    provider = _make_provider(httpx.MockTransport(handler))
    libs = await provider.discover_libraries(_config())
    assert {(l.kind, l.name, l.root_path) for l in libs} == {
        ("movies", "Movies", "/data/movies"),
        ("tv", "TV", "/data/tv"),
        ("music", "Music", "/data/music"),
    }


@pytest.mark.asyncio
async def test_missing_token_validates() -> None:
    provider = _make_provider(httpx.MockTransport(lambda _r: httpx.Response(200)))
    report = await provider.healthcheck(
        IntegrationConfig(
            integration_id="i",
            name="n",
            kind="plex",
            options={"base_url": "http://x"},
            secrets={},  # ← token missing
        )
    )
    assert report.status == "error"
    assert "token" in (report.detail or "").lower()


@pytest.mark.asyncio
async def test_sync_tags_returns_empty_in_v0_1() -> None:
    provider = _make_provider(httpx.MockTransport(lambda _r: httpx.Response(200)))
    assert await provider.sync_tags(_config()) == []
