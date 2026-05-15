"""Rules API + service integration test."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "rules.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
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
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
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
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "a@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = response.json()
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


async def _seed_one_file() -> tuple[str, str]:
    """Insert one library + one MediaFile. Returns (library_id, media_id)."""
    async with get_database().session() as sess:
        lib = Library(name="Movies", root_path="/data/movies", kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path="/data/movies/Dune (2021)/movie.mkv",
            relative_path="Dune (2021)/movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=20_000_000_000,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            container="matroska",
            video_codec="hevc",
            audio_codec="eac3",
            width=3840,
            height=2160,
            duration_seconds=9000.0,
            bitrate_kbps=25000,
            has_subtitles=True,
            seen_at=utcnow(),
            is_orphaned=False,
        )
        sess.add(media)
        await sess.commit()
        return lib.id, media.id


@pytest.mark.asyncio
async def test_rule_crud(client: AsyncClient) -> None:
    headers = await _admin_headers(client)

    create = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "Fat HEVC",
            "description": "Flag big HEVC files",
            "definition": {
                "match": {
                    "all": [
                        {"field": "video_codec", "op": "eq", "value": "hevc"},
                        {"field": "bitrate_kbps", "op": "gt", "value": 20000},
                    ]
                },
                "actions": [
                    {"type": "set_severity", "severity": "warn"},
                    {"type": "add_tag", "tag": "fat-hevc"},
                ],
            },
        },
    )
    assert create.status_code == 201, create.text
    rule_id = create.json()["id"]

    fetched = await client.get(
        f"/api/v1/rules/{rule_id}", headers=headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["enabled"] is True

    listing = await client.get("/api/v1/rules", headers=headers)
    assert {r["id"] for r in listing.json()} == {rule_id}

    update_response = await client.patch(
        f"/api/v1/rules/{rule_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert update_response.json()["enabled"] is False

    delete = await client.delete(
        f"/api/v1/rules/{rule_id}", headers=headers
    )
    assert delete.status_code == 204
    assert (await client.get("/api/v1/rules", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_create_rejects_bad_definition(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "Bad",
            "definition": {
                "match": {"field": "nonsense", "op": "eq", "value": "x"},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dry_run_against_real_file(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    _, media_id = await _seed_one_file()

    response = await client.post(
        "/api/v1/rules/dry-run",
        headers=headers,
        json={
            "media_file_id": media_id,
            "definition": {
                "match": {"field": "bitrate_kbps", "op": "gt", "value": 20000},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] is True
    assert body["severity"] == "warn"


@pytest.mark.asyncio
async def test_evaluate_library_writes_severity(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    library_id, media_id = await _seed_one_file()

    # Create a rule that should match the seeded file.
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "Fat HEVC",
            "definition": {
                "match": {
                    "all": [
                        {"field": "video_codec", "op": "eq", "value": "hevc"},
                        {"field": "bitrate_kbps", "op": "gt", "value": 20000},
                    ]
                },
                "actions": [
                    {"type": "set_severity", "severity": "warn"},
                    {"type": "add_tag", "tag": "fat-hevc"},
                ],
            },
        },
    )

    evaluate = await client.post(
        f"/api/v1/rules/libraries/{library_id}/evaluate", headers=headers
    )
    assert evaluate.status_code == 200
    assert evaluate.json()["files_evaluated"] == 1

    # Confirm the file's severity got updated.
    file_response = await client.get(
        f"/api/v1/media/{media_id}", headers=headers
    )
    body = file_response.json()
    assert body["severity"] == "warn"
    assert body["severity_rank"] == 40


# ── Stage 15: rule vocabulary ─────────────────────────────────
@pytest.mark.asyncio
async def test_vocabulary_endpoint_returns_fields_ops_severities_actions(
    client: AsyncClient,
) -> None:
    """The visual builder consumes ``/rules/vocabulary`` to render
    typed inputs per condition. The shape must include the four core
    arrays the frontend depends on."""
    headers = await _admin_headers(client)

    response = await client.get("/api/v1/rules/vocabulary", headers=headers)
    assert response.status_code == 200
    body = response.json()

    # Top-level shape
    assert "fields" in body
    assert "ops" in body
    assert "severities" in body
    assert "actions" in body

    # Fields: a non-empty list of {key, label, type}
    assert len(body["fields"]) > 0
    field_keys = {f["key"] for f in body["fields"]}
    # Spot-check a few well-known supported fields.
    for required in (
        "video_codec",
        "size_bytes",
        "has_subtitles",
        "tags",
        "category",
    ):
        assert required in field_keys, f"missing field: {required}"

    # Each field has a type from the known set.
    for field in body["fields"]:
        assert field["type"] in ("numeric", "string", "bool", "array")

    # ``category`` has an enum.
    category = next(f for f in body["fields"] if f["key"] == "category")
    assert category["enum"] is not None
    assert "media" in category["enum"]

    # Op sets cover every field type the frontend renders for.
    for key in ("numeric", "string", "bool", "array"):
        assert key in body["ops"]
        assert len(body["ops"][key]) > 0

    # Numeric ops include the usual comparison family.
    for op in ("eq", "gt", "lt", "gte", "lte", "ne"):
        assert op in body["ops"]["numeric"]

    # Severities is the full SEVERITY_LEVELS ordering.
    assert body["severities"] == ["ok", "info", "warn", "high", "error", "crit"]

    # Actions: pre-Stage-9 the visual builder exposed four;
    # Stage 9 adds ``quarantine`` and ``delete``.
    action_types = {a["type"] for a in body["actions"]}
    assert action_types == {
        "set_severity",
        "add_tag",
        "queue_optimization",
        "notify",
        "quarantine",
        "delete",
    }

    # ``set_severity`` exposes the severities as an enum in its args.
    sev_action = next(a for a in body["actions"] if a["type"] == "set_severity")
    assert "severity" in sev_action["args_schema"]
    assert "enum" in sev_action["args_schema"]["severity"]


@pytest.mark.asyncio
async def test_vocabulary_endpoint_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/rules/vocabulary")
    assert response.status_code == 401


# ── Stage 16 Turn 2: rule suggestions ─────────────────────────
async def _seed_suggestion(*, status: str = "pending") -> str:
    """Insert one RuleSuggestion directly. Returns its id."""
    from app.models.rule_suggestion import RuleSuggestion

    async with get_database().session() as sess:
        sug = RuleSuggestion(
            name="Flag HEVC files that transcode frequently",
            heuristic="high_transcode_codec",
            definition={
                "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            },
            evidence={
                "codec": "hevc",
                "total_plays": 47,
                "transcodes": 39,
                "transcode_rate": 0.83,
            },
            files_affected=39,
            est_runtime_s=None,
            confidence=0.85,
            dedup_key="high_transcode_codec:hevc",
            status=status,
        )
        sess.add(sug)
        await sess.commit()
        return sug.id


@pytest.mark.asyncio
async def test_list_suggestions_returns_pending_only(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    pending_id = await _seed_suggestion(status="pending")
    # A dismissed suggestion should NOT appear in the list response.
    async with get_database().session() as sess:
        from app.models.rule_suggestion import RuleSuggestion

        dismissed = RuleSuggestion(
            name="Other",
            heuristic="container_compat",
            definition={
                "match": {"field": "container", "op": "eq", "value": "mkv"},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            },
            evidence={},
            files_affected=10,
            confidence=0.5,
            dedup_key="container_compat:mkv",
            status="dismissed",
            dismissed_at=utcnow(),
        )
        sess.add(dismissed)
        await sess.commit()

    response = await client.get("/api/v1/rules/suggestions", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == pending_id
    assert body[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_suggestion_returns_detail(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()
    response = await client.get(
        f"/api/v1/rules/suggestions/{sid}", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sid
    assert body["evidence"]["transcode_rate"] == 0.83
    assert body["definition"]["match"]["field"] == "video_codec"


@pytest.mark.asyncio
async def test_get_suggestion_404_for_unknown(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.get(
        "/api/v1/rules/suggestions/nonexistent-id", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deploy_suggestion_creates_rule_and_updates_status(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()

    response = await client.post(
        f"/api/v1/rules/suggestions/{sid}/deploy",
        json={"name": "Flag HEVC (custom)", "priority": 50},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Flag HEVC (custom)"
    assert body["priority"] == 50
    assert body["definition"]["match"]["value"] == "hevc"
    rule_id = body["id"]

    # Suggestion should now be "deployed" with deployed_rule_id set.
    detail = await client.get(
        f"/api/v1/rules/suggestions/{sid}", headers=headers
    )
    assert detail.status_code == 200
    suggestion = detail.json()
    assert suggestion["status"] == "deployed"
    assert suggestion["deployed_rule_id"] == rule_id


@pytest.mark.asyncio
async def test_deploy_suggestion_with_modified_definition(
    client: AsyncClient,
) -> None:
    """Operator edited the rule in the visual builder before deploying."""
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()

    modified = {
        "match": {
            "all": [
                {"field": "video_codec", "op": "eq", "value": "hevc"},
                {"field": "bitrate_kbps", "op": "gt", "value": 15000},
            ]
        },
        "actions": [
            {"type": "set_severity", "severity": "high"},
            {"type": "queue_optimization", "profile": "hevc-to-h264"},
        ],
    }

    response = await client.post(
        f"/api/v1/rules/suggestions/{sid}/deploy",
        json={"definition": modified},
        headers=headers,
    )
    assert response.status_code == 201
    rule_body = response.json()
    # The deployed rule carries the modified definition, not the
    # original suggestion's definition.
    assert "all" in rule_body["definition"]["match"]
    assert (
        rule_body["definition"]["actions"][1]["type"] == "queue_optimization"
    )


@pytest.mark.asyncio
async def test_deploy_suggestion_rejects_invalid_definition(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()

    bad = {
        "match": {"field": "nope_not_a_real_field", "op": "eq", "value": 1},
        "actions": [{"type": "set_severity", "severity": "warn"}],
    }
    response = await client.post(
        f"/api/v1/rules/suggestions/{sid}/deploy",
        json={"definition": bad},
        headers=headers,
    )
    assert response.status_code == 422
    # Suggestion should remain pending; the bad payload mustn't
    # accidentally mark it deployed.
    detail = await client.get(
        f"/api/v1/rules/suggestions/{sid}", headers=headers
    )
    assert detail.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_deploy_suggestion_409_if_already_deployed(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()
    r1 = await client.post(
        f"/api/v1/rules/suggestions/{sid}/deploy", json={}, headers=headers
    )
    assert r1.status_code == 201
    r2 = await client.post(
        f"/api/v1/rules/suggestions/{sid}/deploy", json={}, headers=headers
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_dismiss_suggestion_marks_dismissed(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion()
    response = await client.post(
        f"/api/v1/rules/suggestions/{sid}/dismiss",
        json={"reason": "Not relevant to my library"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dismissed"
    assert body["dismissed_reason"] == "Not relevant to my library"
    assert body["dismissed_at"] is not None


@pytest.mark.asyncio
async def test_dismiss_suggestion_cannot_dismiss_deployed(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    sid = await _seed_suggestion(status="deployed")
    response = await client.post(
        f"/api/v1/rules/suggestions/{sid}/dismiss",
        json={"reason": "Too late"},
        headers=headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_analyze_playback_run_returns_outcome(
    client: AsyncClient,
) -> None:
    """Manual analyze trigger. With no playback events seeded, the
    analyzer reports zero examined and ``skipped_too_few_events`` true."""
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/rules/analyze-playback/run", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["examined_events"] == 0
    assert body["suggestions_created"] == 0
    assert body["skipped_too_few_events"] is True


@pytest.mark.asyncio
async def test_suggestion_endpoints_require_auth(client: AsyncClient) -> None:
    r1 = await client.get("/api/v1/rules/suggestions")
    assert r1.status_code == 401
    r2 = await client.post(
        "/api/v1/rules/suggestions/fake-id/deploy", json={}
    )
    assert r2.status_code == 401
    r3 = await client.post(
        "/api/v1/rules/suggestions/fake-id/dismiss", json={}
    )
    assert r3.status_code == 401
    r4 = await client.post("/api/v1/rules/analyze-playback/run")
    assert r4.status_code == 401
