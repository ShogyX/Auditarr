"""v1.9 Stage 6.1 — Plex diagnostics + verify helpers.

Pins:
  1. ``_parse_synthetic_job_id`` parses ``plex:<rk>:<target>``
     and rejects malformed inputs.
  2. ``diagnostics`` runs all four probes; happy-path returns
     {ok: true} for each.
  3. ``diagnostics`` activities branch — 401/403 treated as ok
     (with detail) since it's not core to Auditarr.
  4. ``diagnostics`` individual probe HTTP error doesn't abort
     the others; failed probe surfaces ok=false with detail.
  5. ``verify_optimization_started`` returns True when the
     rating key is in the optimize queue, False otherwise.
  6. ``verify_optimization_completed`` returns True when the
     rating key is NOT in the optimize queue, False when it
     is or when the call errors.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from app.integrations.types import IntegrationConfig


def _load_plex():
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "plex"
    spec = importlib.util.spec_from_file_location(
        "plex_plugin_backend_v19s6", plugin_dir / "backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["plex_plugin_backend_v19s6"] = module
    spec.loader.exec_module(module)
    return module


def _config() -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="i",
        name="plex",
        kind="plex",
        options={"base_url": "http://plex.test", "verify_tls": False},
        secrets={"token": "tok123"},
    )


def _provider_with(transport: httpx.MockTransport):
    mod = _load_plex()
    provider = mod.PlexProvider(log=None)
    original = provider._client

    def patched(cfg: IntegrationConfig) -> httpx.AsyncClient:
        c = original(cfg)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    provider._client = patched  # type: ignore[method-assign]
    return provider


# ── synthetic id parser ─────────────────────────────────────────


def test_parse_synthetic_job_id_full() -> None:
    mod = _load_plex()
    assert mod._parse_synthetic_job_id("plex:12345:builtin") == "12345"


def test_parse_synthetic_job_id_missing_target() -> None:
    """The two-segment form ``plex:<rk>`` still yields the
    rating key — defensive handling of stage 07's old format
    before the target was appended."""
    mod = _load_plex()
    assert mod._parse_synthetic_job_id("plex:rk") == "rk"


def test_parse_synthetic_job_id_rejects_non_plex_prefix() -> None:
    mod = _load_plex()
    assert mod._parse_synthetic_job_id("tdarr:rk:t") is None


def test_parse_synthetic_job_id_rejects_empty_rk() -> None:
    mod = _load_plex()
    assert mod._parse_synthetic_job_id("plex::target") is None


# ── diagnostics ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diagnostics_happy_path_all_ok() -> None:
    """Every probe returns 200 → every entry ok=True."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"MediaContainer": {}})

    provider = _provider_with(httpx.MockTransport(handler))
    result = await provider.diagnostics(_config())
    assert set(result.keys()) == {
        "root",
        "library_sections",
        "activities",
        "optimize_queue",
    }
    for name, entry in result.items():
        assert entry["ok"] is True, f"{name} should be ok"
        assert "latency_ms" in entry


@pytest.mark.asyncio
async def test_diagnostics_activities_403_treated_as_ok() -> None:
    """Plex servers behind permission-gated reverse proxies
    sometimes return 401/403 on /activities. That's not a hard
    error for Auditarr — surface it as ok with a hint."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/activities":
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={})

    provider = _provider_with(httpx.MockTransport(handler))
    result = await provider.diagnostics(_config())
    assert result["activities"]["ok"] is True
    detail = result["activities"]["detail"]
    assert "403" in str(detail)


@pytest.mark.asyncio
async def test_diagnostics_optimize_403_is_a_real_failure() -> None:
    """``/library/optimize`` returning 403 IS a real failure —
    Auditarr needs that endpoint for Stage 07 transcode
    submission. The diagnostic surfaces it."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/library/optimize":
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={})

    provider = _provider_with(httpx.MockTransport(handler))
    result = await provider.diagnostics(_config())
    assert result["optimize_queue"]["ok"] is False
    assert "403" in str(result["optimize_queue"]["detail"])
    # The other probes still succeeded — one failed probe
    # doesn't abort the others.
    assert result["root"]["ok"] is True
    assert result["library_sections"]["ok"] is True


@pytest.mark.asyncio
async def test_diagnostics_network_error_per_probe() -> None:
    """A network error on one probe doesn't propagate to others."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/library/sections":
            raise httpx.ConnectError("network down")
        return httpx.Response(200, json={})

    provider = _provider_with(httpx.MockTransport(handler))
    result = await provider.diagnostics(_config())
    assert result["library_sections"]["ok"] is False
    assert "HTTP error" in str(result["library_sections"]["detail"])
    assert result["root"]["ok"] is True


# ── verify helpers ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_started_finds_rating_key() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"Item": [{"ratingKey": "12345"}]},
                        {"Item": [{"ratingKey": "99999"}]},
                    ]
                }
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    assert (
        await provider.verify_optimization_started(
            _config(), "plex:12345:builtin"
        )
        is True
    )


@pytest.mark.asyncio
async def test_verify_started_returns_false_when_not_present() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [{"Item": [{"ratingKey": "99999"}]}]
                }
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    assert (
        await provider.verify_optimization_started(
            _config(), "plex:12345:builtin"
        )
        is False
    )


@pytest.mark.asyncio
async def test_verify_started_false_on_http_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    provider = _provider_with(httpx.MockTransport(handler))
    assert (
        await provider.verify_optimization_started(
            _config(), "plex:12345:builtin"
        )
        is False
    )


@pytest.mark.asyncio
async def test_verify_completed_returns_true_when_absent() -> None:
    """Rating key NOT in queue → considered completed (Plex
    doesn't distinguish completed from cancelled)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [{"Item": [{"ratingKey": "99999"}]}]
                }
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    assert (
        await provider.verify_optimization_completed(
            _config(), "plex:12345:builtin"
        )
        is True
    )


@pytest.mark.asyncio
async def test_verify_completed_returns_false_when_still_queued() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [{"Item": [{"ratingKey": "12345"}]}]
                }
            },
        )

    provider = _provider_with(httpx.MockTransport(handler))
    assert (
        await provider.verify_optimization_completed(
            _config(), "plex:12345:builtin"
        )
        is False
    )
