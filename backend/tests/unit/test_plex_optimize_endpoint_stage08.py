"""Stage 08 (v1.7) — Plex transcode hand-off endpoint shape.

Plan §459:
    Mock the Plex endpoint; assert the body shape matches the
    documented endpoint contract.

Addendum B.6:
    The supported endpoint is
    ``/library/metadata/{ratingKey}/optimize`` with body params
    ``targetTagID`` (1=Original, 2=Mobile, 3=TV) and
    ``videoQuality`` / ``videoResolution``. Smart playlists at
    ``/playlists/all?playlistType=video&smart=1``. The
    implementer does NOT invent or guess endpoints.

This file pins the request shapes for all three Stage 08 Plex
methods plus the smart-playlist enumeration, and ensures the
implementation never strays beyond the addendum's whitelisted
endpoints.
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
from plugins.plex.backend import PLEX_BUILTIN_TARGETS, PlexProvider


# ── Helpers ────────────────────────────────────────────────────


def _provider() -> PlexProvider:
    return PlexProvider(log=None)


def _config(base_url: str = "http://plex.test:32400") -> IntegrationConfig:
    return IntegrationConfig(
        integration_id="ig-plex",
        name="plex-1",
        kind="plex",
        options={"base_url": base_url, "verify_ssl": False},
        secrets={"token": "test-token"},
    )


def _job_spec(**overrides: Any) -> TranscodeJobSpec:
    defaults = dict(
        item_id="opt-1",
        input_path="/movies/example.mkv",
        transcode_scope="video_and_audio",
        video_codec="libx265",
        audio_codec="copy",
        container="mkv",
        metadata={
            "ratingKey": "12345",
            "provider_profile_id": "2",  # Mobile
        },
    )
    defaults.update(overrides)
    return TranscodeJobSpec(**defaults)  # type: ignore[arg-type]


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handlers: list[tuple[str, Any]]) -> None:
        self._handlers = handlers
        self.requests: list[httpx.Request] = []

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        self.requests.append(request)
        for pat, fac in self._handlers:
            if pat in str(request.url.path):
                if callable(fac):
                    return fac(request)
                return fac
        return httpx.Response(404, content=b'{"error":"unhandled"}')


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, transport: _MockTransport
) -> None:
    real_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("plugins.plex.backend.httpx.AsyncClient", _patched)


# ── Built-in target constants pinned ───────────────────────────


def test_builtin_targets_match_addendum_B6() -> None:
    """Addendum B.6 specifies these exact IDs and names. Pin them
    so a typo can't break Plex's documented contract."""
    ids = {t[0] for t in PLEX_BUILTIN_TARGETS}
    assert ids == {"1", "2", "3"}
    names = {t[1] for t in PLEX_BUILTIN_TARGETS}
    assert names == {"Original Quality", "Mobile", "TV"}


# ── submit_transcode_job ───────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_rejects_when_auto_lookup_cannot_find_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 08 (v1.7) — when no ratingKey is supplied, the
    provider auto-resolves it by walking Plex's library sections
    and matching on ``Media.Part.file``. When no match is found
    (operator hasn't configured path_mappings correctly, or the
    file isn't in Plex at all), submission is rejected with an
    operator-actionable message pointing at path_mappings."""
    # No video sections returned → auto-lookup fails fast.
    transport = _MockTransport(
        [
            (
                "/library/sections",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {"MediaContainer": {"Directory": []}}
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(
        _config(),
        _job_spec(metadata={"provider_profile_id": "2"}),
    )
    assert result.status == "rejected"
    assert "auto-lookup" in (result.detail or "").lower()
    assert "path_mappings" in (result.detail or "")
    # The provider DID call /library/sections (that's the
    # documented endpoint for the lookup) but did NOT hit the
    # optimize endpoint, because the lookup failed first.
    paths_called = {r.url.path for r in transport.requests}
    assert "/library/sections" in paths_called
    assert "/library/metadata/12345/optimize" not in paths_called


@pytest.mark.asyncio
async def test_submit_rejects_without_provider_profile_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport([])
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(
        _config(),
        _job_spec(metadata={"ratingKey": "12345"}),
    )
    assert result.status == "rejected"
    assert "target" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_submit_calls_documented_optimize_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addendum B.6: ``/library/metadata/{ratingKey}/optimize``.
    Pin the URL pattern explicitly so a typo can't redirect to
    a non-documented endpoint."""
    transport = _MockTransport(
        [
            (
                "/library/metadata/12345/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "accepted"

    assert len(transport.requests) == 1
    req = transport.requests[0]
    # Plex's optimize endpoint accepts PUT (this is the documented
    # method per the addendum's reference).
    assert req.method == "PUT"
    assert req.url.path == "/library/metadata/12345/optimize"
    # Body params land as query string.
    assert req.url.params["targetTagID"] == "2"
    # X-Plex-Token header (auth).
    assert "X-Plex-Token" in req.headers


@pytest.mark.asyncio
async def test_submit_forwards_video_quality_and_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addendum B.6 says ``videoQuality`` and ``videoResolution``
    are the documented body params. Pin them."""
    transport = _MockTransport(
        [
            (
                "/library/metadata/12345/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    spec = _job_spec(
        metadata={
            "ratingKey": "12345",
            "provider_profile_id": "2",
            "video_quality": "90",
            "video_resolution": "1920x1080",
        }
    )
    await _provider().submit_transcode_job(_config(), spec)

    req = transport.requests[0]
    assert req.url.params["videoQuality"] == "90"
    assert req.url.params["videoResolution"] == "1920x1080"


@pytest.mark.asyncio
async def test_submit_returns_synthetic_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plex's optimize endpoint doesn't return a job id, so we
    synthesize ``plex:<ratingKey>:<targetID>`` for poller use."""
    transport = _MockTransport(
        [
            (
                "/library/metadata/12345/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.upstream_job_id == "plex:12345:2"


@pytest.mark.asyncio
async def test_submit_handles_4xx_5xx_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [
            (
                "/library/metadata/12345/optimize",
                httpx.Response(401, content=b"unauthorized"),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "error"
    assert "401" in (result.detail or "")


# ── Auto-lookup of ratingKey from path ─────────────────────────


@pytest.mark.asyncio
async def test_submit_auto_resolves_ratingKey_when_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 08 (v1.7) — operators don't have to pre-pin a
    ratingKey. The provider walks ``/library/sections`` + each
    section's ``/all`` until it finds a ``Media.Part.file`` that
    matches the Auditarr file path."""
    sections_payload = {
        "MediaContainer": {
            "Directory": [
                {"key": "1", "type": "movie", "title": "Movies"},
            ]
        }
    }
    section_all_payload = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "99999",
                    "title": "Auto-resolved movie",
                    "Media": [
                        {
                            "Part": [
                                {"file": "/movies/example.mkv"},
                            ]
                        }
                    ],
                }
            ]
        }
    }
    transport = _MockTransport(
        [
            (
                "/library/sections/1/all",
                httpx.Response(
                    200, content=json.dumps(section_all_payload).encode()
                ),
            ),
            (
                "/library/sections",
                httpx.Response(
                    200, content=json.dumps(sections_payload).encode()
                ),
            ),
            (
                "/library/metadata/99999/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    # job_spec carries an input_path but NO metadata.ratingKey.
    spec = _job_spec(
        metadata={"provider_profile_id": "2"},  # no ratingKey
    )
    result = await _provider().submit_transcode_job(_config(), spec)

    assert result.status == "accepted"
    assert result.upstream_job_id == "plex:99999:2"

    # The provider walked /library/sections, then the section
    # /all, then hit the optimize endpoint with the AUTO-RESOLVED
    # ratingKey (99999) — not the operator-supplied one.
    paths = [r.url.path for r in transport.requests]
    assert "/library/sections" in paths
    assert "/library/sections/1/all" in paths
    assert "/library/metadata/99999/optimize" in paths


@pytest.mark.asyncio
async def test_submit_auto_lookup_applies_path_mappings_inverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the integration has ``path_mappings`` configured, the
    auto-lookup applies them in reverse (Auditarr-side path →
    Plex-side path) before matching ``Part.file``."""
    sections_payload = {
        "MediaContainer": {
            "Directory": [
                {"key": "1", "type": "movie", "title": "Movies"},
            ]
        }
    }
    # Plex sees the file at /plex/media/... while Auditarr sees
    # /home/me/media/... — the path_mapping rewrites between them.
    section_all_payload = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "88888",
                    "Media": [
                        {
                            "Part": [
                                {"file": "/plex/media/Movies/example.mkv"},
                            ]
                        }
                    ],
                }
            ]
        }
    }
    transport = _MockTransport(
        [
            (
                "/library/sections/1/all",
                httpx.Response(
                    200, content=json.dumps(section_all_payload).encode()
                ),
            ),
            (
                "/library/sections",
                httpx.Response(
                    200, content=json.dumps(sections_payload).encode()
                ),
            ),
            (
                "/library/metadata/88888/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    # Configure the integration with a path_mapping. The
    # ``from`` (src_prefix) is the Plex-side path, ``to``
    # (dst_prefix) is the Auditarr-side path.
    config = IntegrationConfig(
        integration_id="ig-plex",
        name="plex-1",
        kind="plex",
        options={
            "base_url": "http://plex.test:32400",
            "verify_ssl": False,
            "path_mappings": [
                {"from": "/plex/media", "to": "/home/me/media"},
            ],
        },
        secrets={"token": "test-token"},
    )

    spec = _job_spec(
        input_path="/home/me/media/Movies/example.mkv",
        metadata={"provider_profile_id": "2"},
    )
    result = await _provider().submit_transcode_job(config, spec)

    assert result.status == "accepted"
    assert result.upstream_job_id == "plex:88888:2"


@pytest.mark.asyncio
async def test_submit_auto_lookup_no_match_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Plex has a video section but no item matches the
    path, the rejection message names the path and points the
    operator at path_mappings."""
    sections_payload = {
        "MediaContainer": {
            "Directory": [{"key": "1", "type": "movie", "title": "Movies"}]
        }
    }
    transport = _MockTransport(
        [
            (
                "/library/sections/1/all",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {"MediaContainer": {"Metadata": []}}
                    ).encode(),
                ),
            ),
            (
                "/library/sections",
                httpx.Response(
                    200, content=json.dumps(sections_payload).encode()
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    spec = _job_spec(
        input_path="/home/me/media/Movies/missing.mkv",
        metadata={"provider_profile_id": "2"},
    )
    result = await _provider().submit_transcode_job(_config(), spec)
    assert result.status == "rejected"
    assert "missing.mkv" in (result.detail or "")
    assert "path_mappings" in (result.detail or "")


@pytest.mark.asyncio
async def test_submit_explicit_ratingKey_bypasses_auto_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator-supplied ratingKey still works — auto-lookup
    is the fallback, not a replacement. The provider should NOT
    call /library/sections when ratingKey is supplied."""
    transport = _MockTransport(
        [
            (
                "/library/metadata/12345/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    result = await _provider().submit_transcode_job(_config(), _job_spec())
    assert result.status == "accepted"
    assert result.upstream_job_id == "plex:12345:2"
    paths = {r.url.path for r in transport.requests}
    assert "/library/sections" not in paths
    assert "/library/metadata/12345/optimize" in paths


@pytest.mark.asyncio
async def test_submit_auto_lookup_handles_section_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plex unreachable during section enumeration → rejected
    with the HTTP error surfaced (so the operator can diagnose)."""
    transport = _MockTransport(
        [("/library/sections", httpx.Response(503, content=b"down"))],
    )
    _install_transport(monkeypatch, transport)
    spec = _job_spec(metadata={"provider_profile_id": "2"})
    result = await _provider().submit_transcode_job(_config(), spec)
    assert result.status == "rejected"
    assert "section" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_submit_auto_lookup_walks_multiple_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file may live in the second section enumerated; the
    provider keeps walking sections until it finds a match."""
    sections_payload = {
        "MediaContainer": {
            "Directory": [
                {"key": "1", "type": "movie", "title": "Movies"},
                {"key": "2", "type": "movie", "title": "4K"},
            ]
        }
    }
    transport = _MockTransport(
        [
            (
                # Section 1 has no match.
                "/library/sections/1/all",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {"MediaContainer": {"Metadata": []}}
                    ).encode(),
                ),
            ),
            (
                # Section 2 has the file.
                "/library/sections/2/all",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "MediaContainer": {
                                "Metadata": [
                                    {
                                        "ratingKey": "77777",
                                        "Media": [
                                            {
                                                "Part": [
                                                    {"file": "/movies/example.mkv"}
                                                ]
                                            }
                                        ],
                                    }
                                ]
                            }
                        }
                    ).encode(),
                ),
            ),
            (
                "/library/sections",
                httpx.Response(
                    200, content=json.dumps(sections_payload).encode()
                ),
            ),
            (
                "/library/metadata/77777/optimize",
                httpx.Response(200, content=b""),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    spec = _job_spec(metadata={"provider_profile_id": "2"})
    result = await _provider().submit_transcode_job(_config(), spec)
    assert result.status == "accepted"
    assert result.upstream_job_id == "plex:77777:2"


# ── list_transcode_profiles ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_profiles_always_includes_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when Plex's smart-playlist endpoint fails, the three
    built-in targets are always available."""
    transport = _MockTransport(
        [("/playlists/all", httpx.Response(500, content=b"err"))],
    )
    _install_transport(monkeypatch, transport)

    profiles = await _provider().list_transcode_profiles(_config())
    assert len(profiles) == 3
    ids = [p.id for p in profiles]
    assert ids == ["1", "2", "3"]
    # Each built-in carries the kind hint.
    for p in profiles:
        assert p.metadata.get("target_kind") == "builtin"


@pytest.mark.asyncio
async def test_list_profiles_uses_documented_smart_playlists_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addendum B.6: ``/playlists/all?playlistType=video&smart=1``."""
    transport = _MockTransport(
        [
            (
                "/playlists/all",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "MediaContainer": {
                                "Metadata": [
                                    {
                                        "ratingKey": "999",
                                        "title": "My HEVC Target",
                                        "summary": "Custom transcode.",
                                    },
                                ],
                            }
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    profiles = await _provider().list_transcode_profiles(_config())

    # Right endpoint + params.
    req = transport.requests[0]
    assert req.url.path == "/playlists/all"
    assert req.url.params["playlistType"] == "video"
    assert req.url.params["smart"] == "1"

    # 3 built-ins + 1 smart playlist.
    assert len(profiles) == 4
    smart = profiles[-1]
    assert smart.id == "999"
    assert smart.name == "My HEVC Target"
    assert smart.metadata["target_kind"] == "smart_playlist"


@pytest.mark.asyncio
async def test_list_profiles_handles_plex_xml_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older Plex servers return XML; the provider tolerates both."""
    xml_body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<MediaContainer size="1">'
        b'<Playlist ratingKey="888" title="XML Smart" />'
        b'</MediaContainer>'
    )
    transport = _MockTransport(
        [
            (
                "/playlists/all",
                httpx.Response(
                    200,
                    content=xml_body,
                    headers={"Content-Type": "application/xml"},
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)

    profiles = await _provider().list_transcode_profiles(_config())
    smart_ids = [p.id for p in profiles if p.metadata.get("target_kind") == "smart_playlist"]
    assert smart_ids == ["888"]


# ── get_transcode_job_status ───────────────────────────────────


@pytest.mark.asyncio
async def test_status_rejects_unrecognised_id_format() -> None:
    """The synthetic id is ``plex:<ratingKey>:<targetID>``; other
    shapes return ``unknown``."""
    status = await _provider().get_transcode_job_status(
        _config(), "tdarr-1234"
    )
    assert status.status == "unknown"
    assert "expected" in (status.detail or "").lower()


@pytest.mark.asyncio
async def test_status_returns_running_when_ratingKey_in_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plex queue contains the ratingKey → job still running."""
    transport = _MockTransport(
        [
            (
                "/library/optimize",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "MediaContainer": {
                                "Metadata": [
                                    {"sourceRatingKey": "12345"},
                                ]
                            }
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(
        _config(), "plex:12345:2"
    )
    assert status.status == "running"


@pytest.mark.asyncio
async def test_status_returns_completed_when_ratingKey_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ratingKey not in optimize queue → treated as completed.
    Detail mentions the cancel/complete ambiguity per the
    documented Plex behaviour."""
    transport = _MockTransport(
        [
            (
                "/library/optimize",
                httpx.Response(
                    200,
                    content=json.dumps(
                        {
                            "MediaContainer": {
                                "Metadata": [
                                    {"sourceRatingKey": "99999"},
                                ]
                            }
                        }
                    ).encode(),
                ),
            ),
        ]
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(
        _config(), "plex:12345:2"
    )
    assert status.status == "completed"
    assert "cancel" in (status.detail or "").lower()


@pytest.mark.asyncio
async def test_status_http_error_returns_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _MockTransport(
        [("/library/optimize", httpx.Response(503, content=b"down"))],
    )
    _install_transport(monkeypatch, transport)
    status = await _provider().get_transcode_job_status(
        _config(), "plex:12345:2"
    )
    assert status.status == "unknown"


@pytest.mark.asyncio
async def test_status_uses_documented_library_optimize_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the polling endpoint URL per addendum B.6 — the
    implementer must not invent endpoints."""
    transport = _MockTransport(
        [("/library/optimize", httpx.Response(200, content=b"{}"))],
    )
    _install_transport(monkeypatch, transport)
    await _provider().get_transcode_job_status(_config(), "plex:12345:2")
    assert len(transport.requests) == 1
    assert transport.requests[0].url.path == "/library/optimize"
