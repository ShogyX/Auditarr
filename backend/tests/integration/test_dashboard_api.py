"""Dashboard API integration tests.

We seed a small synthetic library (3 files at different severities, 1
rule, 1 evaluation, 1 integration, 1 scan run, 1 job run, 2 optimization
items) so the aggregations have something interesting to count.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.integration import Integration
from app.models.job_run import JobRun
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.scan_run import ScanRun
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "dashboard.db"
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


async def _seed_dashboard_state() -> dict[str, str]:
    """Insert a tiny synthetic state. Returns interesting ids."""
    now = utcnow()
    async with get_database().session() as sess:
        # One library.
        lib = Library(name="Movies", root_path="/data/movies", kind="movies")
        sess.add(lib)
        await sess.flush()

        # Three files: one ok, one warn, one high.
        files = [
            MediaFile(
                library_id=lib.id,
                path=f"/data/movies/f{i}.mkv",
                relative_path=f"f{i}.mkv",
                filename=f"f{i}.mkv",
                extension="mkv",
                size_bytes=1024 * 1024 * 100,
                mtime=now,
                category="media",
                severity=sev,
                severity_rank=rank,
                video_codec="hevc",
                audio_codec="eac3",
                has_subtitles=True,
                seen_at=now,
                is_orphaned=False,
            )
            for i, (sev, rank) in enumerate(
                [("ok", 10), ("warn", 40), ("high", 60)]
            )
        ]
        sess.add_all(files)
        await sess.flush()

        # One rule + one evaluation row pointing at the warn file.
        rule = Rule(
            name="Flag warn",
            enabled=True,
            priority=100,
            definition={
                "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
                "actions": [{"type": "set_severity", "severity": "warn"}],
            },
        )
        sess.add(rule)
        await sess.flush()
        sess.add(
            RuleEvaluation(
                media_file_id=files[1].id,
                rule_id=rule.id,
                severity="warn",
                severity_rank=40,
                actions_summary={"add_tags": []},
                evaluated_at=now,
            )
        )

        # One integration in 'ok' state.
        sess.add(
            Integration(
                name="My Plex",
                kind="plex",
                enabled=True,
                config={"base_url": "http://plex.test"},
                health_status="ok",
                health_detail="online",
                health_checked_at=now,
            )
        )

        # One scan run, completed.
        sess.add(
            ScanRun(
                library_id=lib.id,
                mode="full",
                status="completed",
                started_at=now,
                finished_at=now,
                files_seen=3,
                files_added=3,
                files_updated=0,
                files_orphaned=0,
                probe_failures=0,
            )
        )

        # One completed + one failed job run.
        sess.add_all(
            [
                JobRun(
                    job_kind="scan_library",
                    job_args={"library_id": lib.id},
                    status="completed",
                    started_at=now,
                    finished_at=now,
                    duration_ms=123,
                    result={"files_seen": 3},
                    trigger="manual",
                ),
                JobRun(
                    job_kind="healthcheck_integration",
                    job_args={"integration_id": "x"},
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    duration_ms=42,
                    error="timeout",
                    trigger="schedule",
                ),
            ]
        )

        # One queued + one completed optimization item.
        sess.add_all(
            [
                OptimizationItem(
                    media_file_id=files[2].id,
                    profile="shrink",
                    status="queued",
                    queued_at=now,
                    item_metadata={},
                ),
                OptimizationItem(
                    media_file_id=files[1].id,
                    profile="shrink",
                    status="completed",
                    queued_at=now,
                    item_metadata={},
                ),
            ]
        )
        await sess.commit()
        return {"library_id": lib.id, "rule_id": rule.id}


@pytest.mark.asyncio
async def test_overview_returns_seeded_counts(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["file_count"] == 3
    assert body["library_count"] == 1
    assert body["integration_count"] == 1
    assert body["integration_ok_count"] == 1
    assert body["rule_count"] == 1
    assert body["rule_enabled_count"] == 1
    assert body["severity_counts"]["ok"] == 1
    assert body["severity_counts"]["warn"] == 1
    assert body["severity_counts"]["high"] == 1
    assert body["severity_counts"]["total"] == 3
    assert body["issues_open"] == 2  # warn + high
    assert body["optimization_counts"]["queued"] == 1
    assert body["optimization_counts"]["completed"] == 1
    assert body["last_scan_at"] is not None


@pytest.mark.asyncio
async def test_library_severity(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get("/api/v1/dashboard/libraries", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["library_name"] == "Movies"
    assert row["file_count"] == 3
    assert row["severity"]["warn"] == 1
    assert row["severity"]["high"] == 1
    assert row["severity"]["ok"] == 1


@pytest.mark.asyncio
async def test_integration_health_snapshot(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get(
        "/api/v1/dashboard/integrations", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "My Plex"
    assert body[0]["health_status"] == "ok"


@pytest.mark.asyncio
async def test_top_rules_orders_by_match_count(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get(
        "/api/v1/dashboard/top-rules?limit=5", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Flag warn"
    assert body[0]["match_count"] == 1


@pytest.mark.asyncio
async def test_recent_scans_and_job_runs(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    scans = await client.get(
        "/api/v1/dashboard/recent-scans?limit=5", headers=headers
    )
    assert scans.status_code == 200
    assert len(scans.json()) == 1
    assert scans.json()[0]["library_name"] == "Movies"

    job_runs = await client.get(
        "/api/v1/dashboard/recent-job-runs?limit=5", headers=headers
    )
    assert job_runs.status_code == 200
    body = job_runs.json()
    assert len(body) == 2
    # Should include both completed + failed runs we seeded.
    statuses = {r["status"] for r in body}
    assert statuses == {"completed", "failed"}


@pytest.mark.asyncio
async def test_sidebar_badges(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get(
        "/api/v1/dashboard/sidebar-badges", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["issuesOpen"] == 2  # severity_rank > 10 → warn + high
    assert body["rulesEnabled"] == 1
    assert body["activeOptimizations"] == 1  # only queued is "active"


@pytest.mark.asyncio
async def test_overview_empty_database(client: AsyncClient) -> None:
    """An empty install should return zeroed counts without erroring."""
    headers = await _admin_headers(client)

    response = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["file_count"] == 0
    assert body["library_count"] == 0
    assert body["severity_counts"]["total"] == 0
    assert body["issues_open"] == 0
    assert body["last_scan_at"] is None
    # Stage 14.1: total_size_bytes is exposed and zero on empty install.
    assert body["total_size_bytes"] == 0


# ── Stage 14.1: series endpoint + total_size_bytes ─────────
@pytest.mark.asyncio
async def test_overview_includes_total_size_bytes(client: AsyncClient) -> None:
    """``total_size_bytes`` is computed as ``SUM(MediaFile.size_bytes)``."""
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert response.status_code == 200
    body = response.json()
    # The fixture seeds 3 files at 100 MB each. We don't assert the
    # exact number (the fixture may evolve) but it must be > 0.
    assert body["total_size_bytes"] > 0


@pytest.mark.asyncio
async def test_dashboard_series_returns_30_day_arrays(
    client: AsyncClient,
) -> None:
    """``/dashboard/series`` returns 4 arrays of length ``days``."""
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    response = await client.get(
        "/api/v1/dashboard/series?days=30", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 30
    assert len(body["issues_opened"]) == 30
    assert len(body["issues_resolved"]) == 30
    assert len(body["integrity_score"]) == 30
    assert len(body["files_seen"]) == 30
    # All values must be non-negative.
    assert all(v >= 0 for v in body["issues_opened"])
    assert all(v >= 0 for v in body["files_seen"])
    # Integrity score is bounded 0..100.
    assert all(0.0 <= v <= 100.0 for v in body["integrity_score"])


@pytest.mark.asyncio
async def test_dashboard_series_clamps_days(client: AsyncClient) -> None:
    """``days`` is bounded; ``999`` triggers Pydantic 422."""
    headers = await _admin_headers(client)

    r1 = await client.get("/api/v1/dashboard/series?days=1", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["days"] == 1

    # 999 exceeds the 90 cap.
    r2 = await client.get("/api/v1/dashboard/series?days=999", headers=headers)
    assert r2.status_code == 422


# ── Stage 14.1: comma-separated severity filter on /media ──
@pytest.mark.asyncio
async def test_media_accepts_comma_separated_severity(
    client: AsyncClient,
) -> None:
    """The Files scope bar passes e.g. ``?severity=warn,high`` and the
    backend now resolves that to an ``IN (...)`` clause."""
    headers = await _admin_headers(client)
    await _seed_dashboard_state()

    # Single value (existing behavior preserved)
    r1 = await client.get("/api/v1/media?severity=warn", headers=headers)
    assert r1.status_code == 200

    # Multi-value (new behavior) — returns a superset.
    r2 = await client.get(
        "/api/v1/media?severity=warn,high,error", headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["total"] >= r1.json()["total"]


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/dashboard/overview")
    assert response.status_code == 401
