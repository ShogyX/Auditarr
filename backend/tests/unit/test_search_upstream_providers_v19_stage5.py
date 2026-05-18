"""v1.9 Stage 5.1 — Provider unit tests for ``trigger_search``.

Covers:
  1. ``_find_arr_id_by_path_prefix`` resolver — longest prefix,
     directory-boundary anchoring (Show A vs Show Anniversary),
     no-match returns None, missing fields skipped.
  2. Sonarr.trigger_search — happy path POSTs SeriesSearch with
     the resolved series id; not_found when no path matches;
     error on 4xx from /api/v3/command.
  3. Radarr.trigger_search — same pattern with MoviesSearch +
     {movieIds: [<id>]}.
  4. Bazarr.trigger_search — series path matched first; movie
     path fallback when no series matches; error on 4xx;
     not_found when neither list contains a matching path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_plugin(name: str):
    """Import a plugin's ``backend.py`` by filesystem path so the
    test file works without installing the plugin package."""
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / name
    spec = importlib.util.spec_from_file_location(
        f"{name}_plugin_backend_v19s5", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{name}_plugin_backend_v19s5"] = module
    spec.loader.exec_module(module)
    return module


def _config(kind: str, **overrides) -> IntegrationConfig:
    base_urls = {
        "sonarr": "http://sonarr.test",
        "radarr": "http://radarr.test",
        "bazarr": "http://bazarr.test",
    }
    return IntegrationConfig(
        integration_id="i",
        name=kind,
        kind=kind,
        options={
            "base_url": base_urls[kind],
            **overrides.get("options", {}),
        },
        secrets={"api_key": "key123", **overrides.get("secrets", {})},
    )


def _provider(mod, kind: str, transport: httpx.MockTransport):
    """Build a provider with its HTTP transport replaced."""
    cls_name = {
        "sonarr": "SonarrProvider",
        "radarr": "RadarrProvider",
        "bazarr": "BazarrProvider",
    }[kind]
    provider = getattr(mod, cls_name)(log=None)
    original = provider._client

    def patched(cfg: IntegrationConfig) -> httpx.AsyncClient:
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


# ── Resolver helper ──────────────────────────────────────────────


def test_find_arr_id_picks_longest_prefix() -> None:
    """When two series paths could match, the longer one wins.

    The classic case is ``Show A`` vs ``Show Anniversary``: a
    file under the longer one must NOT resolve to the shorter."""
    sonarr_mod = _load_plugin("sonarr")
    items = [
        {"id": 1, "path": "/data/tv/Show A"},
        {"id": 2, "path": "/data/tv/Show Anniversary"},
        {"id": 3, "path": "/data/tv"},
    ]
    # File under "Show A" must resolve to id=1, not id=3 (parent)
    # and certainly not id=2 (shares prefix at the string level
    # but not at a directory boundary).
    assert (
        sonarr_mod._find_arr_id_by_path_prefix(
            items, "/data/tv/Show A/S01/ep01.mkv"
        )
        == 1
    )
    assert (
        sonarr_mod._find_arr_id_by_path_prefix(
            items, "/data/tv/Show Anniversary/S01/ep01.mkv"
        )
        == 2
    )


def test_find_arr_id_returns_none_when_no_path_matches() -> None:
    sonarr_mod = _load_plugin("sonarr")
    items = [{"id": 1, "path": "/data/tv/Show"}]
    assert (
        sonarr_mod._find_arr_id_by_path_prefix(items, "/elsewhere/file.mkv")
        is None
    )


def test_find_arr_id_skips_items_missing_fields() -> None:
    """Items without a ``path`` or ``id`` are skipped silently —
    Sonarr can occasionally surface partial entries while a
    series is being added."""
    sonarr_mod = _load_plugin("sonarr")
    items = [
        {"id": 1},  # no path
        {"path": "/data/tv/X"},  # no id
        {"id": 5, "path": "/data/tv/Show"},
    ]
    assert (
        sonarr_mod._find_arr_id_by_path_prefix(
            items, "/data/tv/Show/S01/ep.mkv"
        )
        == 5
    )


# ── Sonarr ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sonarr_trigger_search_happy_path() -> None:
    """GET /api/v3/series → POST /api/v3/command with the right
    payload → ``status="submitted"`` carrying the upstream id."""
    sonarr_mod = _load_plugin("sonarr")
    posted: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/series":
            return httpx.Response(
                200,
                json=[
                    {"id": 42, "path": "/data/tv/Show A"},
                    {"id": 99, "path": "/data/tv/Show B"},
                ],
            )
        if req.url.path == "/api/v3/command":
            import json

            posted.update(json.loads(req.content))
            return httpx.Response(200, json={"id": 1234})
        return httpx.Response(404)

    provider = _provider(
        sonarr_mod, "sonarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("sonarr"), "/data/tv/Show A/S01/ep01.mkv"
    )
    assert result.status == "submitted"
    assert result.upstream_id == "42"
    assert posted == {"name": "SeriesSearch", "seriesId": 42}
    assert result.metadata.get("command_id") == 1234


@pytest.mark.asyncio
async def test_sonarr_trigger_search_not_found() -> None:
    """No series path is a prefix of the file → status=not_found,
    no command POSTed."""
    sonarr_mod = _load_plugin("sonarr")
    cmd_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/series":
            return httpx.Response(
                200, json=[{"id": 1, "path": "/data/tv/Show A"}]
            )
        if req.url.path == "/api/v3/command":
            cmd_calls.append(str(req.url))
            return httpx.Response(200, json={})
        return httpx.Response(404)

    provider = _provider(
        sonarr_mod, "sonarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("sonarr"), "/elsewhere/file.mkv"
    )
    assert result.status == "not_found"
    assert cmd_calls == []


@pytest.mark.asyncio
async def test_sonarr_trigger_search_command_rejected() -> None:
    """Series found, but the command POST returns 4xx → status=error
    with the upstream_id preserved (for audit attribution)."""
    sonarr_mod = _load_plugin("sonarr")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/series":
            return httpx.Response(
                200, json=[{"id": 7, "path": "/data/tv/Show"}]
            )
        if req.url.path == "/api/v3/command":
            return httpx.Response(400, json={"message": "bad command"})
        return httpx.Response(404)

    provider = _provider(
        sonarr_mod, "sonarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("sonarr"), "/data/tv/Show/S01/ep.mkv"
    )
    assert result.status == "error"
    assert result.upstream_id == "7"
    assert "HTTP 400" in (result.detail or "")


# ── Radarr ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_radarr_trigger_search_happy_path() -> None:
    radarr_mod = _load_plugin("radarr")
    posted: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/movie":
            return httpx.Response(
                200,
                json=[
                    {"id": 7, "path": "/data/movies/Title (2020)"},
                    {"id": 8, "path": "/data/movies/Other"},
                ],
            )
        if req.url.path == "/api/v3/command":
            import json

            posted.update(json.loads(req.content))
            return httpx.Response(200, json={"id": 9001})
        return httpx.Response(404)

    provider = _provider(
        radarr_mod, "radarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("radarr"),
        "/data/movies/Title (2020)/Title (2020).mkv",
    )
    assert result.status == "submitted"
    assert result.upstream_id == "7"
    # Radarr-specific payload: movieIds is a list of one id.
    assert posted == {"name": "MoviesSearch", "movieIds": [7]}


@pytest.mark.asyncio
async def test_radarr_trigger_search_not_found() -> None:
    radarr_mod = _load_plugin("radarr")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/movie":
            return httpx.Response(
                200, json=[{"id": 1, "path": "/data/movies/X"}]
            )
        return httpx.Response(404)

    provider = _provider(
        radarr_mod, "radarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("radarr"), "/elsewhere/file.mkv"
    )
    assert result.status == "not_found"


# ── Bazarr ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bazarr_trigger_search_series_matches_first() -> None:
    """Bazarr's series list is consulted before its movie list.
    A path matching only a series row produces a
    ``type=series`` search."""
    bazarr_mod = _load_plugin("bazarr")
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/series":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 11, "path": "/data/tv/Show A"},
                    ]
                },
            )
        if req.url.path == "/api/movies":
            return httpx.Response(200, json={"data": []})
        if req.url.path == "/api/subtitles":
            captured["params"] = dict(req.url.params)
            return httpx.Response(200, json={"queued": True})
        return httpx.Response(404)

    provider = _provider(
        bazarr_mod, "bazarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("bazarr"), "/data/tv/Show A/S01/ep.mkv"
    )
    assert result.status == "submitted"
    assert result.upstream_id == "11"
    assert captured["params"] == {
        "action": "search",
        "type": "series",
        "id": "11",
    }
    assert result.metadata.get("target_kind") == "series"


@pytest.mark.asyncio
async def test_bazarr_trigger_search_movie_fallback() -> None:
    """No series match but movies list contains it → search
    ``type=movie``."""
    bazarr_mod = _load_plugin("bazarr")
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/series":
            return httpx.Response(200, json={"data": []})
        if req.url.path == "/api/movies":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 22, "path": "/data/movies/Title (2020)"},
                    ]
                },
            )
        if req.url.path == "/api/subtitles":
            captured["params"] = dict(req.url.params)
            return httpx.Response(200, json={})
        return httpx.Response(404)

    provider = _provider(
        bazarr_mod, "bazarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("bazarr"),
        "/data/movies/Title (2020)/Title (2020).mkv",
    )
    assert result.status == "submitted"
    assert result.upstream_id == "22"
    assert captured["params"]["type"] == "movie"


@pytest.mark.asyncio
async def test_bazarr_trigger_search_not_found() -> None:
    bazarr_mod = _load_plugin("bazarr")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/series":
            return httpx.Response(200, json={"data": []})
        if req.url.path == "/api/movies":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    provider = _provider(
        bazarr_mod, "bazarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("bazarr"), "/data/tv/Show/S01/ep.mkv"
    )
    assert result.status == "not_found"


@pytest.mark.asyncio
async def test_bazarr_trigger_search_command_rejected() -> None:
    bazarr_mod = _load_plugin("bazarr")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/series":
            return httpx.Response(
                200, json={"data": [{"id": 5, "path": "/data/tv/X"}]}
            )
        if req.url.path == "/api/movies":
            return httpx.Response(200, json={"data": []})
        if req.url.path == "/api/subtitles":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(404)

    provider = _provider(
        bazarr_mod, "bazarr", httpx.MockTransport(handler)
    )
    result = await provider.trigger_search(
        _config("bazarr"), "/data/tv/X/S01/ep.mkv"
    )
    assert result.status == "error"
    assert result.upstream_id == "5"
    assert "HTTP 500" in (result.detail or "")
