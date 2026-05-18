"""v1.9 Stage 6.1 — Plex ratingKey resolver TTL cache.

Pins:
  1. First resolution hits Plex (one /sections + one section walk).
  2. Second resolution within TTL is served from cache (no HTTP).
  3. Resolution after TTL expiry re-hits Plex.
  4. Failed resolutions are NOT cached — re-resolving a missing
     path immediately rechecks Plex (operator just added the file).
  5. Different ``(integration_id, path)`` pairs are isolated.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_plex():
    plex_dir = Path(__file__).resolve().parents[2] / "plugins" / "plex"
    spec = importlib.util.spec_from_file_location(
        "plex_plugin_backend_v19s6_cache", plex_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plex_plugin_backend_v19s6_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


def _config(integration_id: str = "i1") -> IntegrationConfig:
    return IntegrationConfig(
        integration_id=integration_id,
        name="plex-prod",
        kind="plex",
        options={"base_url": "http://plex.test"},
        secrets={"token": "abc"},
    )


def _provider_with(handler):
    mod = _load_plex()
    provider = mod.PlexProvider(log=None)
    original = provider._client

    def patched(config: IntegrationConfig) -> httpx.AsyncClient:
        c = original(config)
        c._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


# Plex's library response shapes are verbose; helpers keep tests
# readable.
def _sections_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "MediaContainer": {
                "Directory": [
                    {
                        "key": "1",
                        "type": "movie",
                        "title": "Movies",
                    }
                ]
            }
        },
    )


def _movie_all_response(path: str, rating_key: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "MediaContainer": {
                "size": 1,
                "totalSize": 1,
                "Metadata": [
                    {
                        "ratingKey": rating_key,
                        "type": "movie",
                        "Media": [
                            {"Part": [{"file": path}]},
                        ],
                    }
                ],
            }
        },
    )


def _make_handler(target_path: str, rating_key: str, *, counter: list[int]):
    """A handler that counts requests + returns the matching item.

    We track the request count via a mutable list so the test can
    assert the cache short-circuited the second call."""

    def handler(req: httpx.Request) -> httpx.Response:
        counter.append(1)
        if req.url.path == "/library/sections":
            return _sections_response()
        if req.url.path.startswith("/library/sections/"):
            return _movie_all_response(target_path, rating_key)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_cache_hit_skips_http() -> None:
    """Two resolutions of the same path within TTL → second one
    served from cache (no additional HTTP)."""
    counter: list[int] = []
    handler = _make_handler("/data/x.mkv", "rk-42", counter=counter)
    provider = _provider_with(handler)

    r1, err1 = await provider._resolve_rating_key_from_path(
        _config(), "/data/x.mkv"
    )
    assert err1 is None and r1 == "rk-42"
    http_calls_first = len(counter)
    assert http_calls_first > 0

    r2, err2 = await provider._resolve_rating_key_from_path(
        _config(), "/data/x.mkv"
    )
    assert err2 is None and r2 == "rk-42"
    # No new HTTP calls.
    assert len(counter) == http_calls_first


@pytest.mark.asyncio
async def test_cache_miss_after_ttl_expires() -> None:
    """After TTL expiry, the same path re-hits Plex."""
    counter: list[int] = []
    handler = _make_handler("/data/x.mkv", "rk-42", counter=counter)
    provider = _provider_with(handler)
    # Make TTL tiny for the test rather than waiting 60s.
    provider._RATING_KEY_TTL_SECONDS = 0

    await provider._resolve_rating_key_from_path(_config(), "/data/x.mkv")
    first_call_count = len(counter)
    # Manually expire the cached entry by setting expires_at to
    # the past. (TTL=0 means the cache stores expires_at ==
    # written_at, which the next read will treat as expired.)
    key = ("i1", "/data/x.mkv")
    cached = provider._rating_key_cache.get(key)
    if cached:
        provider._rating_key_cache[key] = (
            cached[0],
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=1),
        )

    await provider._resolve_rating_key_from_path(_config(), "/data/x.mkv")
    # Second resolution must have re-hit Plex.
    assert len(counter) > first_call_count


@pytest.mark.asyncio
async def test_failed_resolution_is_not_cached() -> None:
    """A "not found" result is NOT cached — re-resolving an
    operator-added file should not wait for TTL expiry."""
    counter: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        counter.append(1)
        if req.url.path == "/library/sections":
            return _sections_response()
        if req.url.path.startswith("/library/sections/"):
            # Empty result — no matching file.
            return httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "size": 0,
                        "totalSize": 0,
                        "Metadata": [],
                    }
                },
            )
        return httpx.Response(404)

    provider = _provider_with(handler)

    rk1, err1 = await provider._resolve_rating_key_from_path(
        _config(), "/data/missing.mkv"
    )
    assert rk1 is None and err1 is not None
    first_call_count = len(counter)

    # Second attempt MUST hit Plex again.
    rk2, err2 = await provider._resolve_rating_key_from_path(
        _config(), "/data/missing.mkv"
    )
    assert rk2 is None and err2 is not None
    assert len(counter) > first_call_count


@pytest.mark.asyncio
async def test_cache_is_keyed_by_integration_id() -> None:
    """Two integrations resolving the same path must NOT share
    cache entries — different Plex servers may map the same
    Auditarr path to different ratingKeys."""
    counter: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        counter.append(1)
        if req.url.path == "/library/sections":
            return _sections_response()
        if req.url.path.startswith("/library/sections/"):
            return _movie_all_response("/data/x.mkv", "rk-1")
        return httpx.Response(404)

    provider = _provider_with(handler)

    await provider._resolve_rating_key_from_path(
        _config("integration-A"), "/data/x.mkv"
    )
    first_call_count = len(counter)

    # Different integration_id same path — second resolution
    # MUST go to HTTP because the cache key differs.
    await provider._resolve_rating_key_from_path(
        _config("integration-B"), "/data/x.mkv"
    )
    assert len(counter) > first_call_count
