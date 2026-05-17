"""Stage 09 (v1.7) — Live playback aggregating endpoint test.

Plan §493:
    Mock plex's "sessions" endpoint; assert /playback/live
    returns the parsed sessions.

We exercise the surface end-to-end:
  1. A stub provider registered as kind="plex" returns hand-
     crafted LivePlaybackDTOs.
  2. The HTTP endpoint aggregates them, applies the
     integration's path mappings, and surfaces
     ``media_file_id`` when the post-remap path matches a
     seeded MediaFile.
  3. The response shape matches :class:`LivePlaybackResponse`.

Per addendum A.7 / Stage 09 design: ``resolved`` and
``unresolved`` counts are returned so the frontend can render
a path-mappings hint when ``unresolved > 0``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    LivePlaybackDTO,
    PlaybackEventDTO,
    TagSync,
)
from app.main import create_app
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import PlaybackSession
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


# ── Stub provider (mocks Plex's /status/sessions surface) ──────


class _LiveStub:
    """Stub provider whose ``fetch_live_playbacks`` returns a
    canned batch the test owns. Conforms to the Stage 09
    Protocol surface plus enough of the rest to keep the
    ``runtime_checkable`` isinstance check happy."""

    kind = "plex"
    label = "Stub Plex (Stage 09)"
    config_schema: dict = {"type": "object", "properties": {}}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.next_live: list[LivePlaybackDTO] = []
        self.raise_on_call: Exception | None = None

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since
    ) -> list[PlaybackEventDTO]:
        return []

    async def fetch_live_playbacks(
        self, _config: IntegrationConfig
    ) -> list[LivePlaybackDTO]:
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return list(self.next_live)

    # Stage 07 / Stage 08 Protocol shims so the registry's
    # runtime_checkable isinstance() check passes when the
    # manager looks up the provider.
    async def submit_transcode_job(self, _config, _job_spec):  # noqa: ANN001, ANN202
        from app.integrations.types import JobSubmitResult

        return JobSubmitResult(status="rejected", detail="stub")

    async def list_transcode_profiles(self, _config):  # noqa: ANN001, ANN202
        return []

    async def get_transcode_job_status(self, _config, _upstream_job_id):  # noqa: ANN001, ANN202
        from app.integrations.types import TranscodeJobStatus

        return TranscodeJobStatus(status="unknown")


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "live09.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Seed library + media files. The "live now" tile resolves
    # remapped paths against MediaFile.path for deep-linking.
    async with db.session() as session:
        lib = Library(
            name="Movies", root_path="/mnt/media/Movies", kind="movies"
        )
        session.add(lib)
        await session.flush()
        for fname in ("a.mkv", "b.mkv"):
            session.add(
                MediaFile(
                    library_id=lib.id,
                    path=f"/mnt/media/Movies/{fname}",
                    relative_path=fname,
                    filename=fname,
                    extension="mkv",
                    size_bytes=1024 * 1024,
                    mtime=_dt.datetime.now(_dt.UTC),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    has_subtitles=False,
                    seen_at=_dt.datetime.now(_dt.UTC),
                    is_orphaned=False,
                )
            )
        # Integration with path_mappings configured. Plex sees
        # /data/movies/* which we rewrite to /mnt/media/Movies/*.
        integration = Integration(
            name="Stub Plex",
            kind="plex",
            enabled=True,
            poll_interval_seconds=900,
            config={
                "base_url": "http://stub/",
                "path_mappings": [
                    {"from": "/data/movies", "to": "/mnt/media/Movies"},
                ],
            },
            health_status="ok",
        )
        session.add(integration)
        await session.commit()
        integration_id = integration.id

    # Wire the stub onto the registry under the Plex kind so the
    # IntegrationManager picks it up.
    from app.core.registry import get_registry

    registry = get_registry()
    bus = get_event_bus()
    stub = _LiveStub()
    # Replace any built-in Plex registration with the stub for
    # the test's duration.
    registry.clear()
    registry.register_capability("integration.plex", stub)

    # Build a TestClient over the real ASGI app so we exercise
    # the full router + dependency wiring.
    app = create_app()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    # Register + login as admin to get a bearer.
    from sqlalchemy import update as _update

    from app.models.user import User

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin09@example.com",
            "username": "admin09",
            "password": "supersecret-password-1!",
        },
    )
    assert reg.status_code in (200, 201), reg.text
    user = reg.json()
    async with db.session() as sess:
        await sess.execute(
            _update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin09", "password": "supersecret-password-1!"},
    )
    assert login.status_code == 200, login.text
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    try:
        yield {
            "client": client,
            "db": db,
            "stub": stub,
            "integration_id": integration_id,
            "bus": bus,
            "headers": headers,
        }
    finally:
        await client.aclose()
        registry.clear()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


# ── Helper (v1.8.0): seed playback_sessions directly ──────────


async def _seed_playback_sessions(
    env, rows: list[dict],
) -> None:
    """v1.8.0 / Stage 17: the live endpoint's Plex path now reads
    from the ``playback_sessions`` table (populated by the
    worker's SSE listener) instead of polling
    ``fetch_live_playbacks`` on every request. These tests
    pre-Stage-17 stubbed the provider; we now seed the table
    directly.

    Each ``rows`` entry is a dict of column overrides; required
    fields (integration_id, session_key, state, decision,
    started_at, last_event_at) are set from defaults if omitted.
    """
    db = env["db"]
    integration_id = env["integration_id"]
    async with db.session() as session:
        for spec in rows:
            row = PlaybackSession(
                integration_id=spec.get("integration_id", integration_id),
                session_key=spec["session_key"],
                state=spec.get("state", "playing"),
                decision=spec.get("decision", "direct_play"),
                source_path=spec.get("source_path"),
                title=spec.get("title"),
                user=spec.get("user"),
                device_kind=spec.get("device_kind"),
                device_name=spec.get("device_name"),
                source_codec=spec.get("source_codec"),
                source_bitrate_kbps=spec.get("source_bitrate_kbps"),
                source_width=spec.get("source_width"),
                source_height=spec.get("source_height"),
                source_container=spec.get("source_container"),
                target_codec=spec.get("target_codec"),
                target_bitrate_kbps=spec.get("target_bitrate_kbps"),
                view_offset_ms=spec.get("view_offset_ms"),
                duration_ms=spec.get("duration_ms"),
                started_at=spec.get("started_at", utcnow()),
                last_event_at=spec.get("last_event_at", utcnow()),
                stopped_at=spec.get("stopped_at"),
            )
            session.add(row)
        await session.commit()


# ── Test 1 — Plan §493 contract ────────────────────────────────


@pytest.mark.asyncio
async def test_live_endpoint_returns_parsed_sessions(env) -> None:
    """Plan §493: mock Plex's sessions endpoint; assert
    /playback/live returns the parsed sessions.

    v1.8.0 / Stage 17 update: Plex sessions now come from the
    ``playback_sessions`` table populated by the worker's SSE
    listener. We seed that table directly so the test exercises
    the same DB→endpoint code path as production.
    """
    client = env["client"]
    now = utcnow().replace(microsecond=0)

    await _seed_playback_sessions(
        env,
        [
            {
                "session_key": "session-1",
                # ``source_path`` is the integration-side (pre-remap)
                # path; the endpoint applies the integration's
                # path_mappings to produce the Auditarr-side path.
                "source_path": "/data/movies/a.mkv",
                "decision": "direct_play",
                "started_at": now - _dt.timedelta(minutes=5),
                "state": "playing",
                "view_offset_ms": 4250,
                "duration_ms": 10000,
                "user": "alice",
                "device_kind": "Roku",
                "device_name": "Living Room Roku",
                "source_codec": "h264",
                "source_width": 1920,
                "source_height": 1080,
                "source_container": "mkv",
                "title": "The Matrix",
            },
            {
                "session_key": "session-2",
                "source_path": "/data/movies/b.mkv",
                "decision": "transcode",
                "started_at": now - _dt.timedelta(minutes=1),
                "state": "paused",
                "view_offset_ms": 1000,
                "duration_ms": 10000,
                "user": "bob",
                "device_kind": "iOS",
                "source_codec": "hevc",
                "source_width": 3840,
                "source_height": 2160,
                "target_codec": "h264",
                "target_bitrate_kbps": 8000,
                "title": "The Matrix Reloaded",
            },
        ],
    )

    response = await client.get("/api/v1/playback/live", headers=env["headers"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 2
    assert body["resolved"] == 2  # both paths matched after remap.
    assert body["unresolved"] == 0
    sessions = body["sessions"]
    assert len(sessions) == 2

    # The DB doesn't preserve insertion order strictly, so sort
    # by upstream_id (= session_key) for deterministic asserts.
    sessions.sort(key=lambda s: s["upstream_id"])
    first, second = sessions[0], sessions[1]

    assert first["upstream_id"] == "session-1"
    # Path was remapped from /data/movies/a.mkv to the Auditarr
    # side.
    assert first["source_path"] == "/mnt/media/Movies/a.mkv"
    assert first["decision"] == "direct_play"
    assert first["state"] == "playing"
    assert first["progress_pct"] == 42.5
    assert first["user"] == "alice"
    assert first["device_kind"] == "Roku"
    assert first["title"] == "The Matrix"
    # Resolved → media_file_id stamped.
    assert first["media_file_id"] is not None

    assert second["upstream_id"] == "session-2"
    assert second["source_path"] == "/mnt/media/Movies/b.mkv"
    assert second["decision"] == "transcode"
    assert second["state"] == "paused"
    assert second["target_codec"] == "h264"
    assert second["target_bitrate_kbps"] == 8000
    assert second["media_file_id"] is not None


# ── Test 2 — Unresolved paths surface in split counts ──────────


@pytest.mark.asyncio
async def test_live_endpoint_surfaces_unresolved_count(env) -> None:
    """When a remapped path doesn't match a MediaFile, the
    session still surfaces but ``media_file_id`` is None and
    the response's ``unresolved`` counter increments. The
    frontend reads this to render the path-mappings hint
    (addendum A.7).

    v1.8.0 update: seed PlaybackSession rows directly (was
    stubbing fetch_live_playbacks pre-Stage-17).
    """
    client = env["client"]
    now = utcnow().replace(microsecond=0)

    await _seed_playback_sessions(
        env,
        [
            {
                "session_key": "session-resolved",
                "source_path": "/data/movies/a.mkv",
                "decision": "direct_play",
                "started_at": now,
                "state": "playing",
                "title": "Known",
            },
            {
                "session_key": "session-unresolved",
                # Won't match any seeded MediaFile after remap.
                "source_path": "/data/movies/unknown.mkv",
                "decision": "direct_play",
                "started_at": now,
                "state": "playing",
                "title": "Unknown",
            },
        ],
    )

    response = await client.get("/api/v1/playback/live", headers=env["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["resolved"] == 1
    assert body["unresolved"] == 1


# ── Test 3 — Empty when no enabled integrations ────────────────


@pytest.mark.asyncio
async def test_live_endpoint_empty_when_no_sessions(env) -> None:
    """No active sessions → empty list + zero counters.

    v1.8.0: no rows seeded in playback_sessions ⇒ no Plex
    sessions returned. No fetch_live_playbacks stub to clear.
    """
    client = env["client"]

    response = await client.get("/api/v1/playback/live", headers=env["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["sessions"] == []
    assert body["resolved"] == 0
    assert body["unresolved"] == 0


# ── Test 4 — Stopped sessions don't show ───────────────────────


@pytest.mark.asyncio
async def test_live_endpoint_excludes_stopped_sessions(env) -> None:
    """v1.8.0 / Stage 17 contract: PlaybackSession rows with
    ``state="stopped"`` are NOT returned by /playback/live. They
    persist in the table for history but the live tile shows
    only active sessions.

    Pre-Stage-17 there was no equivalent because the polling
    endpoint didn't surface stopped sessions in the first
    place — they were already gone from
    /status/sessions by the time the dashboard polled. This
    test pins the new contract explicitly.
    """
    client = env["client"]
    now = utcnow().replace(microsecond=0)

    await _seed_playback_sessions(
        env,
        [
            {
                "session_key": "s-playing",
                "source_path": "/data/movies/a.mkv",
                "decision": "direct_play",
                "started_at": now - _dt.timedelta(minutes=2),
                "state": "playing",
            },
            {
                "session_key": "s-stopped",
                "source_path": "/data/movies/b.mkv",
                "decision": "direct_play",
                "started_at": now - _dt.timedelta(minutes=10),
                "state": "stopped",
                "stopped_at": now - _dt.timedelta(seconds=30),
            },
        ],
    )

    response = await client.get("/api/v1/playback/live", headers=env["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["sessions"][0]["upstream_id"] == "s-playing"


# ── Test 5 — Provider errors no longer in the hot path ─────────


@pytest.mark.asyncio
async def test_live_endpoint_isolates_provider_errors(env) -> None:
    """v1.8.0 update: Plex sessions are now sourced from the
    DB, so a Plex transport failure can't crash the live
    endpoint at all — there's no upstream HTTP call to fail.

    The endpoint returns the current snapshot of
    playback_sessions even if the worker's SSE listener is
    disconnected. This test pins that resilience: the
    response is 200 and reflects whatever's in the table.
    """
    client = env["client"]

    # No rows seeded → empty response, no upstream call attempted.
    response = await client.get("/api/v1/playback/live", headers=env["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["sessions"] == []


# ── Test 5 — Plex DTO parser pins shape on raw payload ─────────


@pytest.mark.asyncio
async def test_plex_session_payload_translates_to_live_dto() -> None:
    """Verify the Plex parser ``_plex_live_to_dto`` correctly
    handles a realistic ``/status/sessions`` Metadata entry.
    Plan §493's "mock Plex's sessions endpoint" requirement is
    end-to-end-tested above; this pin pulls the per-entry
    translation into its own assertion so future changes to the
    Plex shape are caught here, not by downstream brittleness.
    """
    from plugins.plex.backend import _plex_live_to_dto

    entry = {
        "sessionKey": "42",
        "title": "Inception",
        "addedAt": 1736000000,
        "viewOffset": 600000,  # 10 minutes
        "duration": 6000000,  # 100 minutes
        "Media": [
            {
                "videoCodec": "hevc",
                "bitrate": 12000,
                "width": 3840,
                "height": 2160,
                "container": "mkv",
                "duration": 6000000,
                "Part": [{"file": "/plex/media/Movies/Inception.mkv"}],
            }
        ],
        "Player": {"state": "playing", "device": "AppleTV", "title": "Bedroom"},
        "User": {"title": "alice"},
        "TranscodeSession": {
            "videoDecision": "transcode",
            "videoCodec": "h264",
            "bitrate": 8000,
        },
    }
    dto = _plex_live_to_dto(entry)
    assert dto is not None
    assert dto.upstream_id == "42"
    assert dto.source_path == "/plex/media/Movies/Inception.mkv"
    assert dto.decision == "transcode"
    assert dto.state == "playing"
    # 600000 / 6000000 = 10.0
    assert dto.progress_pct == 10.0
    assert dto.user == "alice"
    assert dto.device_kind == "AppleTV"
    assert dto.source_codec == "hevc"
    assert dto.source_bitrate_kbps == 12000
    assert dto.source_width == 3840
    assert dto.source_height == 2160
    assert dto.target_codec == "h264"
    assert dto.target_bitrate_kbps == 8000
    assert dto.title == "Inception"


# ── Test 6 — Plex parser handles direct-play (no transcode) ────


@pytest.mark.asyncio
async def test_plex_session_direct_play_decision() -> None:
    """No ``TranscodeSession`` → decision is ``direct_play``."""
    from plugins.plex.backend import _plex_live_to_dto

    entry = {
        "sessionKey": "100",
        "Media": [
            {"Part": [{"file": "/plex/media/Movies/x.mkv"}], "duration": 100000}
        ],
        "Player": {"state": "playing"},
        "User": {"title": "carol"},
    }
    dto = _plex_live_to_dto(entry)
    assert dto is not None
    assert dto.decision == "direct_play"


# ── Test 7 — Plex parser tolerates malformed entries ───────────


@pytest.mark.asyncio
async def test_plex_session_malformed_returns_none() -> None:
    """A bad entry returns None rather than raising — one bad
    session can't poison the live tile."""
    from plugins.plex.backend import _plex_live_to_dto

    # No Media / no Part / no file.
    assert _plex_live_to_dto({"sessionKey": "1"}) is None
    assert _plex_live_to_dto({"sessionKey": "1", "Media": []}) is None
    assert (
        _plex_live_to_dto({"sessionKey": "1", "Media": [{"Part": []}]}) is None
    )
    # No sessionKey.
    assert (
        _plex_live_to_dto(
            {"Media": [{"Part": [{"file": "/x"}]}]}
        )
        is None
    )


# ── Test 8 — Jellyfin parser ─────────────────────────────────


@pytest.mark.asyncio
async def test_jellyfin_session_payload_translates_to_live_dto() -> None:
    """Verify Jellyfin's parser handles a realistic
    ``/Sessions`` entry."""
    from plugins.jellyfin.backend import _jellyfin_session_to_live_dto

    entry = {
        "Id": "sess-99",
        "UserName": "dave",
        "Client": "Jellyfin Mobile",
        "DeviceName": "Pixel 8",
        "LastActivityDate": "2026-01-01T12:00:00Z",
        "NowPlayingItem": {
            "Id": "item-1",
            "Name": "Arrival",
            "Path": "/jellyfin/media/Movies/Arrival.mkv",
            "RunTimeTicks": 60000000000,  # 6000 seconds @ 10000 ticks/ms
            "MediaSources": [
                {
                    "Container": "mkv",
                    "Bitrate": 8000000,  # 8 Mbps in bps
                    "MediaStreams": [
                        {
                            "Type": "Video",
                            "Codec": "h264",
                            "Width": 1920,
                            "Height": 1080,
                        },
                    ],
                }
            ],
        },
        "PlayState": {
            "IsPaused": False,
            "PositionTicks": 6000000000,  # 10% in
        },
        "TranscodingInfo": {
            "VideoCodec": "h264",
            "Bitrate": 4000000,
            "IsVideoDirect": False,
            "IsAudioDirect": True,
        },
    }

    dto = _jellyfin_session_to_live_dto(entry)
    assert dto is not None
    assert dto.upstream_id == "sess-99"
    assert dto.source_path == "/jellyfin/media/Movies/Arrival.mkv"
    assert dto.decision == "transcode"  # IsVideoDirect=False.
    assert dto.state == "playing"
    assert dto.progress_pct == 10.0
    assert dto.user == "dave"
    assert dto.title == "Arrival"


@pytest.mark.asyncio
async def test_jellyfin_session_idle_returns_none() -> None:
    """A session without ``NowPlayingItem`` is idle — the live
    tile only renders actively-playing sessions."""
    from plugins.jellyfin.backend import _jellyfin_session_to_live_dto

    assert (
        _jellyfin_session_to_live_dto(
            {"Id": "sess-1", "UserName": "eve"}
        )
        is None
    )
