"""v1.9 Stage 9.3 — AI suggestion endpoint integration test.

Pins:
  1. POST /api/v1/rules/suggestions/ai-generate requires admin.
  2. With no AI provider integration configured, the response
     surfaces error=... with no suggestions created.
  3. With one configured + a stubbed provider returning a valid
     JSON proposal, one RuleSuggestion row lands with
     heuristic="ai_<kind>".
  4. Invalid proposals (failing RuleDefinition validation) are
     rejected without writing.
  5. The budget guard kicks in when the integration's
     daily_call_budget worth of audit rows already exist;
     budget_exceeded=True, no HTTP call.
  6. Each successful generate writes an ``ai.suggestions.call``
     audit row — that's how the budget is counted.
  7. The dismissed-suggestion list is carried into the context
     payload (the test stubs validate the messages it receives).
  8. ``send_paths_external=false`` redacts paths to ``<redacted>``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import PlaybackEvent
from app.models.rule import Rule
from app.models.rule_suggestion import RuleSuggestion
from app.models.user import User
from app.services.ai.providers import AIProviderConfig, ChatMessage, ChatResult
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class _StubProvider:
    """Test-controlled AI provider. The test sets ``response``
    + ``raises``; the chat method returns / raises accordingly
    and stashes the messages it received for assertions."""

    kind = "openai"

    def __init__(self) -> None:
        self.response: str = "[]"
        self.raises: Exception | None = None
        self.last_messages: list[ChatMessage] = []
        self.call_count: int = 0
        self.last_config: AIProviderConfig | None = None

    async def chat(
        self,
        config: AIProviderConfig,
        messages: list[ChatMessage],
    ) -> ChatResult:
        self.last_messages = list(messages)
        self.last_config = config
        self.call_count += 1
        if self.raises is not None:
            raise self.raises
        return ChatResult(
            content=self.response,
            prompt_tokens=10,
            completion_tokens=5,
            model="stub-model",
        )


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "stage93.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
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

    # Patch get_ai_provider to return the stub.
    stub_provider = _StubProvider()

    import app.services.ai.suggestions as sugg_mod

    monkeypatch.setattr(
        sugg_mod, "get_ai_provider", lambda _kind: stub_provider
    )

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield {"client": c, "db": db, "stub": stub_provider}
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
    user_id = r.json()["id"]
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user_id).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_ai_integration(
    db, *, send_paths_external: bool = True, budget: int = 50
) -> str:
    async with db.session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="OpenAI",
                kind="ai-provider",
                enabled=True,
                config={
                    "provider_kind": "openai",
                    "endpoint": "https://api.openai.test",
                    "model": "gpt-4o",
                    "temperature": 0.2,
                    "max_tokens": 1024,
                    "daily_call_budget": budget,
                    "send_paths_external": send_paths_external,
                },
            )
        )
        await sess.commit()
    return integ_id


# ── Auth ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_generate_requires_admin(env) -> None:
    client = env["client"]
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
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ai_generate_unauthenticated_returns_401(env) -> None:
    client = env["client"]
    r = await client.post("/api/v1/rules/suggestions/ai-generate")
    assert r.status_code == 401


# ── No-provider state ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_generate_no_provider_returns_422_with_actionable_message(
    env,
) -> None:
    """When no AI provider integration is configured, the endpoint
    must 422 instead of 200ing with empty counts — operators were
    misreading the 200 as "the call ran and produced nothing."
    """
    client = env["client"]
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert "no enabled" in body["message"].lower()


# ── Happy path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_generate_creates_suggestion_from_valid_response(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    stub.response = (
        '[{"name": "AI: Tag fat HEVC", "rationale": "common transcode source",'
        '  "definition": {'
        '    "match": {"field": "video_codec", "op": "eq", "value": "hevc"},'
        '    "actions": [{"type": "add_tag", "tag": "ai-flagged"}]'
        "  }"
        "}]"
    )
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggestions_created"] == 1
    assert body["candidates_received"] == 1
    assert body["candidates_rejected"] == 0
    assert body["provider_kind"] == "openai"
    assert body["error"] is None

    async with db.session() as sess:
        rows = (
            (await sess.execute(select(RuleSuggestion))).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].heuristic == "ai_openai"
    assert rows[0].evidence["rationale"] == "common transcode source"


@pytest.mark.asyncio
async def test_ai_generate_rejects_invalid_definition(env) -> None:
    """A proposal whose ``definition`` doesn't parse against
    RuleDefinition is rejected without writing."""
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    stub.response = '[{"name": "bad", "definition": {"match": "not-an-object"}}]'

    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    body = r.json()
    assert body["suggestions_created"] == 0
    assert body["candidates_received"] == 1
    assert body["candidates_rejected"] == 1

    async with db.session() as sess:
        rows = (
            (await sess.execute(select(RuleSuggestion))).scalars().all()
        )
    assert rows == []


# ── Budget guard ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_generate_budget_exceeded_skips_provider_call(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    integ_id = await _seed_ai_integration(db, budget=3)
    # Seed 3 audit rows in the last 24h.
    async with db.session() as sess:
        now = _dt.datetime.now(_dt.UTC)
        for i in range(3):
            sess.add(
                AuditLogEntry(
                    action="ai.suggestions.call",
                    actor_id=None,
                    actor_label="ai_suggestion_service",
                    target_type="integration",
                    target_id=integ_id,
                    metadata_={"status": "ok"},
                    occurred_at=now - _dt.timedelta(hours=i),
                )
            )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    body = r.json()
    assert body["budget_exceeded"] is True
    assert body["suggestions_created"] == 0
    # Provider must not have been called.
    assert stub.call_count == 0


# ── Audit-log side effect ──────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_generate_writes_audit_row(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    integ_id = await _seed_ai_integration(db)
    stub.response = "[]"
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 200
    async with db.session() as sess:
        audits = (
            (
                await sess.execute(
                    select(AuditLogEntry)
                    .where(AuditLogEntry.action == "ai.suggestions.call")
                    .where(AuditLogEntry.target_id == integ_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
    assert audits[0].metadata_["status"] == "ok"
    assert audits[0].metadata_["tokens_in"] == 10
    assert audits[0].metadata_["tokens_out"] == 5


# ── Provider failure path ──────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_exception_surfaces_as_error_response(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    integ_id = await _seed_ai_integration(db)
    stub.raises = RuntimeError("upstream 503")

    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggestions_created"] == 0
    assert body["error"] is not None
    assert "upstream 503" in body["error"]
    # Failure audit row.
    async with db.session() as sess:
        audits = (
            (
                await sess.execute(
                    select(AuditLogEntry)
                    .where(AuditLogEntry.action == "ai.suggestions.call")
                    .where(AuditLogEntry.target_id == integ_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
    assert audits[0].metadata_["status"] == "error"


# ── Privacy guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_paths_external_false_redacts_top_files(env) -> None:
    """When ``send_paths_external=False`` the top_files payload
    shows ``<redacted>`` instead of the path. We assert by
    inspecting the messages the stub provider received."""
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    integ_id = await _seed_ai_integration(db, send_paths_external=False)
    # Seed a playback event so top_files has something to show.
    async with db.session() as sess:
        lib = Library(name="L", root_path="/m", kind="movies")
        sess.add(lib)
        integ = Integration(
            id="poll-i",
            name="Plex",
            kind="stub",
            enabled=True,
            config={},
        )
        sess.add(integ)
        await sess.flush()
        sess.add(
            PlaybackEvent(
                integration_id="poll-i",
                upstream_id="e1",
                source_path="/sensitive/file.mkv",
                decision="transcode",
                started_at=_dt.datetime.now(_dt.UTC),
            )
        )
        await sess.commit()

    stub.response = "[]"
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 200
    # The user message in the chat call should contain
    # "<redacted>" and NOT contain "/sensitive/file.mkv".
    user_msgs = [m for m in stub.last_messages if m.role == "user"]
    assert user_msgs
    body = user_msgs[0].content
    assert "<redacted>" in body
    assert "/sensitive/file.mkv" not in body


# ── Active rules context ───────────────────────────────────────


@pytest.mark.asyncio
async def test_active_rules_are_included_in_context(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    async with db.session() as sess:
        sess.add(
            Rule(
                name="Existing tag rule",
                enabled=True,
                priority=100,
                definition={
                    "match": {
                        "field": "video_codec",
                        "op": "eq",
                        "value": "av1",
                    },
                    "actions": [{"type": "add_tag", "tag": "av1"}],
                },
            )
        )
        await sess.commit()
    stub.response = "[]"
    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    user_msg = [m for m in stub.last_messages if m.role == "user"][0]
    assert "Existing tag rule" in user_msg.content
    assert "active_rules" in user_msg.content


@pytest.mark.asyncio
async def test_dismissed_suggestions_are_included_in_context(env) -> None:
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    async with db.session() as sess:
        sess.add(
            RuleSuggestion(
                name="Rejected idea",
                definition={
                    "match": {
                        "field": "video_codec",
                        "op": "eq",
                        "value": "x",
                    },
                    "actions": [{"type": "add_tag", "tag": "x"}],
                },
                heuristic="ai_openai",
                evidence={},
                files_affected=0,
                est_runtime_s=None,
                confidence=0.5,
                dedup_key="rejected-1",
                status="dismissed",
            )
        )
        await sess.commit()
    stub.response = "[]"
    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    user_msg = [m for m in stub.last_messages if m.role == "user"][0]
    assert "Rejected idea" in user_msg.content
    assert "rejected_suggestions" in user_msg.content


# ── v1.9 audit fixes ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_provider_kind_fails_fast(env) -> None:
    """v1.9 audit fix (AI-1): integrations missing ``provider_kind``
    should error rather than silently default to OpenAI."""
    client = env["client"]
    db = env["db"]
    async with db.session() as sess:
        sess.add(
            Integration(
                id="bad-1",
                name="Bad",
                kind="ai-provider",
                enabled=True,
                config={"endpoint": "http://x", "model": "y"},
            )
        )
        await sess.commit()
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert "provider_kind" in body["message"]


@pytest.mark.asyncio
async def test_repeated_generate_does_not_collide_on_dedup_key(env) -> None:
    """v1.9 audit fix (AI-3): calling generate() twice with the
    same AI response must not crash on dedup_key uniqueness."""
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    stub.response = (
        '[{"name": "Tag fat HEVC", "rationale": "rationale",'
        '  "definition": {'
        '    "match": {"field": "video_codec", "op": "eq", "value": "hevc"},'
        '    "actions": [{"type": "add_tag", "tag": "ai-flagged"}]'
        "  }"
        "}]"
    )
    headers = await _admin_headers(client)
    r1 = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["suggestions_created"] == 1
    # Second invocation — must NOT crash; must NOT create a
    # duplicate; must count as rejected (already-existed).
    r2 = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["suggestions_created"] == 0
    assert body["candidates_rejected"] == 1
    async with db.session() as sess:
        rows = (
            (await sess.execute(select(RuleSuggestion))).scalars().all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_action_proposal_is_rejected(env) -> None:
    """v1.9 audit fix (AI-4): proposals containing ``delete``
    actions are hard-rejected, even if they pass schema
    validation."""
    client = env["client"]
    db = env["db"]
    stub = env["stub"]
    await _seed_ai_integration(db)
    stub.response = (
        '[{"name": "Dangerous", "rationale": "x",'
        '  "definition": {'
        '    "match": {"field": "video_codec", "op": "eq", "value": "av1"},'
        '    "actions": [{"type": "delete"}]'
        "  }"
        "}]"
    )
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rules/suggestions/ai-generate", headers=headers
    )
    body = r.json()
    assert body["suggestions_created"] == 0
    assert body["candidates_rejected"] == 1
    async with db.session() as sess:
        rows = (
            (await sess.execute(select(RuleSuggestion))).scalars().all()
        )
    assert rows == []


# ── v1.10 (Item 4) — /suggestions/ai-usage ──────────────────────


@pytest.mark.asyncio
async def test_ai_usage_returns_zero_for_fresh_integration(env) -> None:
    """v1.10: a freshly-configured AI provider with no calls
    reports calls_used_24h=0, full budget remaining, and
    budget_exceeded=false."""
    client = env["client"]
    db = env["db"]
    headers = await _admin_headers(client)
    integ_id = await _seed_ai_integration(db, budget=10)

    response = await client.get(
        "/api/v1/rules/suggestions/ai-usage", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    rows = body["integrations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["integration_id"] == integ_id
    assert row["provider_kind"] == "openai"
    assert row["calls_used_24h"] == 0
    assert row["daily_call_budget"] == 10
    assert row["budget_remaining"] == 10
    assert row["budget_exceeded"] is False
    assert row["window_kind"] == "rolling_24h"
    assert isinstance(row["next_reset_at"], str)


@pytest.mark.asyncio
async def test_ai_usage_counts_calls_in_rolling_window(env) -> None:
    """v1.10: a call recorded via _record_call_audit increments
    the usage count. Three calls vs a budget of 5 surfaces
    remaining=2, exceeded=false."""
    client = env["client"]
    db = env["db"]
    headers = await _admin_headers(client)
    integ_id = await _seed_ai_integration(db, budget=5)

    # Seed 3 prior AI call audit rows.
    from app.models.audit_log import AuditLogEntry
    from app.utils.datetime import utcnow

    async with db.session() as sess:
        for i in range(3):
            sess.add(
                AuditLogEntry(
                    action="ai.suggestions.call",
                    target_id=integ_id,
                    target_type="integration",
                    actor_id=None,
                    occurred_at=utcnow(),
                    metadata_={
                        "status": "ok",
                        "tokens_in": 100,
                        "tokens_out": 200,
                    },
                )
            )
        await sess.commit()

    response = await client.get(
        "/api/v1/rules/suggestions/ai-usage", headers=headers
    )
    body = response.json()
    row = body["integrations"][0]
    assert row["calls_used_24h"] == 3
    assert row["daily_call_budget"] == 5
    assert row["budget_remaining"] == 2
    assert row["budget_exceeded"] is False


@pytest.mark.asyncio
async def test_ai_usage_flags_budget_exceeded(env) -> None:
    """v1.10: calls_used >= budget flips budget_exceeded=true
    and clamps budget_remaining to 0 (no negative reading)."""
    client = env["client"]
    db = env["db"]
    headers = await _admin_headers(client)
    integ_id = await _seed_ai_integration(db, budget=2)

    from app.models.audit_log import AuditLogEntry
    from app.utils.datetime import utcnow

    async with db.session() as sess:
        for i in range(5):
            sess.add(
                AuditLogEntry(
                    action="ai.suggestions.call",
                    target_id=integ_id,
                    target_type="integration",
                    actor_id=None,
                    occurred_at=utcnow(),
                    metadata_={"status": "ok"},
                )
            )
        await sess.commit()

    response = await client.get(
        "/api/v1/rules/suggestions/ai-usage", headers=headers
    )
    body = response.json()
    row = body["integrations"][0]
    assert row["calls_used_24h"] == 5
    assert row["budget_remaining"] == 0
    assert row["budget_exceeded"] is True


@pytest.mark.asyncio
async def test_ai_usage_skips_disabled_integrations(env) -> None:
    """v1.10: a disabled integration isn't in the usage rollup
    — the budget check isn't meaningful for an integration that
    can't be called."""
    client = env["client"]
    db = env["db"]
    headers = await _admin_headers(client)

    # Seed two integrations: one enabled, one disabled.
    from app.models.integration import Integration

    async with db.session() as sess:
        sess.add(
            Integration(
                id="enabled-id",
                name="Enabled",
                kind="ai-provider",
                enabled=True,
                config={"provider_kind": "openai", "daily_call_budget": 10},
            )
        )
        sess.add(
            Integration(
                id="disabled-id",
                name="Disabled",
                kind="ai-provider",
                enabled=False,
                config={"provider_kind": "openai", "daily_call_budget": 10},
            )
        )
        await sess.commit()

    response = await client.get(
        "/api/v1/rules/suggestions/ai-usage", headers=headers
    )
    body = response.json()
    ids = [r["integration_id"] for r in body["integrations"]]
    assert "enabled-id" in ids
    assert "disabled-id" not in ids


@pytest.mark.asyncio
async def test_ai_usage_requires_admin(env) -> None:
    """v1.10: the usage endpoint is admin-only (matches
    /suggestions/ai-generate)."""
    client = env["client"]
    db = env["db"]
    await _seed_ai_integration(db, budget=10)

    # Register a non-admin user.
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "u1",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "u1", "password": PASSWORD},
    )
    headers = {
        "authorization": f"Bearer {login.json()['access_token']}"
    }

    response = await client.get(
        "/api/v1/rules/suggestions/ai-usage", headers=headers
    )
    assert response.status_code == 403
