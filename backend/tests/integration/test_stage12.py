"""Stage 12 (audit follow-up) — playback insights read API.

Pins:
  1. ``GET /playback/events`` honours every filter, paginates
     correctly, joins integration_name + library_name.
  2. ``GET /playback/stats/transcoded`` aggregates by media_file_id
     and emits a sentinel ``<unresolved>`` bucket when events have
     no resolved media file.
  3. ``GET /playback/stats/devices`` groups by (device_kind, decision)
     and coalesces null device_kind into ``"unknown"``.
  4. ``GET /playback/stats/decisions`` returns daily counts per
     decision.
  5. ``GET /playback/cursors`` lists every cursor with integration
     names.
  6. ``POST /playback/cursors/{id}/reset`` is admin-only and deletes
     the cursors. 404 when the integration id doesn't exist.

The poller / analyzer are explicitly out of scope and not touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import IntegrationPollingCursor, PlaybackEvent
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


# ── Fixtures ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage12.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = r.json()
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _user_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _seed(now: datetime | None = None) -> dict[str, str]:
    """Seed two integrations, one library, two media files, and a
    fixed set of playback events covering every decision + device
    bucket the tests assert against. Returns a dict of ids for the
    tests to reference."""
    now = now or datetime.now(UTC)
    ids: dict[str, str] = {}
    async with get_database().session() as sess:
        # Two integrations.
        plex = Integration(
            id="int-plex", name="My Plex", kind="plex", enabled=True,
        )
        jelly = Integration(
            id="int-jelly", name="My Jellyfin", kind="jellyfin", enabled=True,
        )
        sess.add_all([plex, jelly])

        # One library + two media files. mf-1 resolves; mf-unknown
        # is a sentinel for the "media_file_id IS NULL" case where
        # an event arrived for a path the scanner hadn't indexed.
        library = Library(
            id="lib-1",
            name="Movies",
            root_path="/tmp/lib",
            kind="movies",
            enabled=True,
        )
        sess.add(library)
        mf1 = MediaFile(
            id="mf-1",
            library_id="lib-1",
            path="/tmp/lib/x.mkv",
            filename="x.mkv",
            relative_path="x.mkv",
            extension="mkv",
            category="media",
            size_bytes=10,
            mtime=now,
            severity="ok",
            severity_rank=0,
        )
        mf2 = MediaFile(
            id="mf-2",
            library_id="lib-1",
            path="/tmp/lib/y.mkv",
            filename="y.mkv",
            relative_path="y.mkv",
            extension="mkv",
            category="media",
            size_bytes=10,
            mtime=now,
            severity="ok",
            severity_rank=0,
        )
        sess.add_all([mf1, mf2])

        # Events. Counts chosen so:
        #   mf-1: 5 transcode (becomes top of /stats/transcoded)
        #   mf-2: 2 transcode
        #   unresolved: 3 transcode
        #   3 direct_play (mf-1, jelly, tv device)
        #   1 failed (mf-2)
        events: list[PlaybackEvent] = []
        for i in range(5):
            events.append(
                PlaybackEvent(
                    integration_id="int-plex",
                    media_file_id="mf-1",
                    source_path="/tmp/lib/x.mkv",
                    device_kind="phone",
                    device_name="iPhone",
                    decision="transcode",
                    reason_code="codec",
                    source_codec="h264",
                    target_codec="h264",
                    started_at=now - timedelta(hours=i),
                    upstream_id=f"plex-transcode-mf1-{i}",
                )
            )
        for i in range(2):
            events.append(
                PlaybackEvent(
                    integration_id="int-plex",
                    media_file_id="mf-2",
                    source_path="/tmp/lib/y.mkv",
                    device_kind="phone",
                    device_name="iPhone",
                    decision="transcode",
                    source_codec="h264",
                    target_codec="hevc",
                    started_at=now - timedelta(hours=10 + i),
                    upstream_id=f"plex-transcode-mf2-{i}",
                )
            )
        for i in range(3):
            events.append(
                PlaybackEvent(
                    integration_id="int-plex",
                    media_file_id=None,  # unresolved
                    source_path="/tmp/missing/z.mkv",
                    device_kind="tv",
                    device_name="LG TV",
                    decision="transcode",
                    started_at=now - timedelta(hours=20 + i),
                    upstream_id=f"plex-transcode-unresolved-{i}",
                )
            )
        for i in range(3):
            events.append(
                PlaybackEvent(
                    integration_id="int-jelly",
                    media_file_id="mf-1",
                    source_path="/tmp/lib/x.mkv",
                    device_kind="tv",
                    device_name="LG TV",
                    decision="direct_play",
                    started_at=now - timedelta(hours=30 + i),
                    upstream_id=f"jelly-direct-mf1-{i}",
                )
            )
        events.append(
            PlaybackEvent(
                integration_id="int-jelly",
                media_file_id="mf-2",
                source_path="/tmp/lib/y.mkv",
                device_kind=None,  # null → "unknown" bucket
                decision="failed",
                reason_code="network_error",
                started_at=now - timedelta(hours=50),
                upstream_id="jelly-failed-mf2-1",
            )
        )
        sess.add_all(events)

        # One cursor on each integration so /cursors has two rows
        # and reset has something to delete.
        sess.add_all(
            [
                IntegrationPollingCursor(
                    integration_id="int-plex",
                    cursor_kind="playback_events",
                    cursor_value="2026-05-14T10:00:00Z",
                ),
                IntegrationPollingCursor(
                    integration_id="int-jelly",
                    cursor_kind="playback_events",
                    cursor_value="2026-05-14T11:00:00Z",
                ),
            ]
        )

        await sess.commit()
    ids["plex"] = "int-plex"
    ids["jelly"] = "int-jelly"
    ids["lib"] = "lib-1"
    ids["mf1"] = "mf-1"
    ids["mf2"] = "mf-2"
    return ids


# ── Tests ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_events_returns_paginated_with_joined_names(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/events?limit=20", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 5 + 2 + 3 + 3 + 1 = 14 events total.
    assert body["total"] == 14
    assert len(body["items"]) == 14
    assert body["offset"] == 0
    assert body["limit"] == 20
    # Ordered DESC by started_at — the most recent is the first
    # mf-1 transcode (i=0 in the loop).
    first = body["items"][0]
    assert first["media_file_id"] == "mf-1"
    # Integration name is joined onto the row.
    assert first["integration_name"] == "My Plex"
    # Library name is joined onto resolved events.
    assert first["library_name"] == "Movies"
    # Unresolved events have null library context.
    unresolved = [e for e in body["items"] if e["media_file_id"] is None]
    assert len(unresolved) == 3
    assert all(e["library_id"] is None for e in unresolved)
    assert all(e["library_name"] is None for e in unresolved)


@pytest.mark.asyncio
async def test_events_filter_by_decision(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/events?decision=direct_play",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert all(e["decision"] == "direct_play" for e in body["items"])


@pytest.mark.asyncio
async def test_events_filter_by_library_excludes_unresolved(
    client: AsyncClient,
) -> None:
    """Library filter joins through media_file → library, so events
    whose path didn't resolve to an indexed file are excluded."""
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/events?library_id=lib-1",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    # 14 total - 3 unresolved = 11
    assert body["total"] == 11
    for event in body["items"]:
        assert event["library_id"] == "lib-1"


@pytest.mark.asyncio
async def test_events_filter_by_media_file_returns_only_matching(
    client: AsyncClient,
) -> None:
    """The FileDetailDrawer's playback-history section uses this filter."""
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/events?media_file_id=mf-1&limit=10",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    # mf-1: 5 plex transcodes + 3 jelly direct_play = 8.
    assert body["total"] == 8
    assert all(e["media_file_id"] == "mf-1" for e in body["items"])


@pytest.mark.asyncio
async def test_events_pagination_offset_and_limit(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    page1 = await client.get(
        "/api/v1/playback/events?limit=5&offset=0", headers=headers
    )
    page2 = await client.get(
        "/api/v1/playback/events?limit=5&offset=5", headers=headers
    )
    assert page1.status_code == 200
    assert page2.status_code == 200
    body1 = page1.json()
    body2 = page2.json()
    assert body1["total"] == body2["total"] == 14
    assert len(body1["items"]) == 5
    assert len(body2["items"]) == 5
    # No overlap.
    ids1 = {e["id"] for e in body1["items"]}
    ids2 = {e["id"] for e in body2["items"]}
    assert not (ids1 & ids2)


@pytest.mark.asyncio
async def test_events_limit_capped(client: AsyncClient) -> None:
    """``limit > 500`` is rejected with 422."""
    headers = await _user_headers(client)
    r = await client.get(
        "/api/v1/playback/events?limit=10000", headers=headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_top_transcoded_aggregates_and_unresolved_bucket(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/stats/transcoded?days=30&limit=10",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_days"] == 30
    items = body["items"]
    # Expect mf-1 (5), mf-2 (2), and an unresolved bucket (3).
    by_id = {i["media_file_id"]: i for i in items}
    assert by_id["mf-1"]["transcode_count"] == 5
    assert by_id["mf-1"]["filename"] == "x.mkv"
    assert by_id["mf-2"]["transcode_count"] == 2
    # The unresolved sentinel is a separate item with media_file_id null.
    unresolved = next(
        (i for i in items if i["media_file_id"] is None), None
    )
    assert unresolved is not None
    assert unresolved["transcode_count"] == 3
    assert unresolved["path"] == "<unresolved>"


@pytest.mark.asyncio
async def test_top_transcoded_excludes_direct_play_and_failed(
    client: AsyncClient,
) -> None:
    """Only ``decision=transcode`` rows count."""
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/stats/transcoded?days=30",
        headers=headers,
    )
    body = r.json()
    # 3 direct_play + 1 failed should be excluded — totals match
    # only the 10 transcode events.
    total_counted = sum(i["transcode_count"] for i in body["items"])
    assert total_counted == 10


@pytest.mark.asyncio
async def test_device_matrix_groups_and_coalesces_unknown(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/stats/devices?days=30", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    cells = body["cells"]
    # Map for lookup.
    by_pair = {(c["device_kind"], c["decision"]): c["count"] for c in cells}
    # phone × transcode = 5 + 2 = 7 (the two mf-1 + mf-2 plex chains).
    assert by_pair[("phone", "transcode")] == 7
    # tv × transcode = 3 (unresolved bucket).
    assert by_pair[("tv", "transcode")] == 3
    # tv × direct_play = 3 (jelly direct_play to mf-1).
    assert by_pair[("tv", "direct_play")] == 3
    # The null-device-kind failed event coalesces into "unknown".
    assert by_pair[("unknown", "failed")] == 1


@pytest.mark.asyncio
async def test_decision_trend_has_per_day_buckets(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get(
        "/api/v1/playback/stats/decisions?days=30", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 30
    # Sum across all decisions must equal the 14 events.
    total = sum(p["count"] for p in body["points"])
    assert total == 14
    # Decisions surfaced — we expect at least the three we seeded.
    decisions_seen = {p["decision"] for p in body["points"]}
    assert {"transcode", "direct_play", "failed"} <= decisions_seen


@pytest.mark.asyncio
async def test_cursors_endpoint_lists_with_integration_names(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.get("/api/v1/playback/cursors", headers=headers)
    assert r.status_code == 200
    cursors = r.json()
    assert len(cursors) == 2
    names = {c["integration_name"] for c in cursors}
    assert names == {"My Plex", "My Jellyfin"}
    kinds = {c["integration_kind"] for c in cursors}
    assert kinds == {"plex", "jellyfin"}


@pytest.mark.asyncio
async def test_reset_cursors_requires_admin(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    await _seed()
    r = await client.post(
        "/api/v1/playback/cursors/int-plex/reset",
        headers=headers,
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_reset_cursors_deletes_rows(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed()
    r = await client.post(
        "/api/v1/playback/cursors/int-plex/reset", headers=headers
    )
    assert r.status_code == 204, r.text

    # Plex cursor gone; Jelly cursor still present.
    async with get_database().session() as sess:
        rows = (
            await sess.execute(select(IntegrationPollingCursor))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].integration_id == "int-jelly"


@pytest.mark.asyncio
async def test_reset_cursors_unknown_integration_404(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/playback/cursors/does-not-exist/reset", headers=headers
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reset_cursors_succeeds_even_when_no_cursors_present(
    client: AsyncClient,
) -> None:
    """Operators sometimes preemptively reset before the first poll;
    a zero-row delete must not 404 as long as the integration
    exists."""
    headers = await _admin_headers(client)
    # Seed an integration with no cursor.
    async with get_database().session() as sess:
        sess.add(
            Integration(
                id="int-empty",
                name="Empty",
                kind="plex",
                enabled=True,
            )
        )
        await sess.commit()
    r = await client.post(
        "/api/v1/playback/cursors/int-empty/reset", headers=headers
    )
    assert r.status_code == 204
