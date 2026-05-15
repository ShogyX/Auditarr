"""Stage 4 (audit follow-up) — dashboard ``issues_open`` threshold.

Pins the new behaviour added in Stage 4:

  - Default (``warn``): files at severity ``ok`` and ``info`` do
    NOT count toward ``issues_open``. Files at ``warn`` and above
    do.
  - Setting ``dashboard_issue_min_severity`` via the runtime
    settings API updates the count on the next ``/dashboard/overview``
    and ``/dashboard/sidebar-badges`` call (no service restart).
  - The legacy ``total - ok`` shape (used by callers that don't
    pass a threshold rank) keeps working — verified by the
    unit-style direct-service test below.

Background — the user-reported issue: ``info`` files were treated
as "open issues" on the dashboard tile, drowning out the actually
actionable rows. The threshold lets the operator decide what
counts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.user import User
from app.services.dashboard import DashboardStats
from app.services.dashboard.stats import resolve_issue_min_severity_rank
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


# ── Unit-style: resolver + DashboardStats with seeded session ───
@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as sess:
        yield sess
    await engine.dispose()


def test_resolve_threshold_maps_labels_to_ranks() -> None:
    """The label-to-rank map must match the rules schema's
    ``SEVERITY_LEVELS``. Unknown labels collapse to ``warn`` (the
    default) rather than throwing — see the docstring on the
    resolver for why."""
    assert resolve_issue_min_severity_rank("info") == 20
    assert resolve_issue_min_severity_rank("warn") == 40
    assert resolve_issue_min_severity_rank("high") == 60
    assert resolve_issue_min_severity_rank("error") == 80
    assert resolve_issue_min_severity_rank("crit") == 100
    # Unknown ⇒ default to warn (rank 40).
    assert resolve_issue_min_severity_rank("nope") == 40
    assert resolve_issue_min_severity_rank("") == 40


async def _seed_one_row_per_severity(session: AsyncSession) -> None:
    """Add four files: ok, info, warn, high. The (label, rank) pairs
    mirror ``app.rules.schema.SEVERITY_LEVELS``."""
    library = Library(
        id="lib-1",
        name="Test",
        root_path="/srv/media",
        kind="movies",
        enabled=True,
    )
    session.add(library)
    await session.flush()
    rows = [
        ("ok", 10),
        ("info", 20),
        ("warn", 40),
        ("high", 60),
    ]
    for idx, (label, rank) in enumerate(rows):
        session.add(
            MediaFile(
                id=f"m-{idx}",
                library_id="lib-1",
                path=f"/srv/media/{idx}.mkv",
                relative_path=f"{idx}.mkv",
                filename=f"{idx}.mkv",
                extension=".mkv",
                size_bytes=1000,
                mtime=utcnow(),
                category="media",
                severity=label,
                severity_rank=rank,
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_overview_issues_open_default_threshold_excludes_ok_and_info(
    session: AsyncSession,
) -> None:
    """Default threshold ``warn`` (rank 40) excludes ok (10) AND info (20)."""
    await _seed_one_row_per_severity(session)
    stats = DashboardStats(
        session, issue_min_severity_rank=resolve_issue_min_severity_rank("warn")
    )
    overview = await stats.overview()
    # warn + high = 2 rows.
    assert overview.issues_open == 2


@pytest.mark.asyncio
async def test_overview_issues_open_info_threshold_includes_info(
    session: AsyncSession,
) -> None:
    """Setting threshold to ``info`` includes info+warn+high (but
    still excludes ok)."""
    await _seed_one_row_per_severity(session)
    stats = DashboardStats(
        session, issue_min_severity_rank=resolve_issue_min_severity_rank("info")
    )
    overview = await stats.overview()
    assert overview.issues_open == 3


@pytest.mark.asyncio
async def test_overview_issues_open_high_threshold_only_high(
    session: AsyncSession,
) -> None:
    """Setting threshold to ``high`` excludes warn — only high+error+crit
    rows count. With our seed: just the high row."""
    await _seed_one_row_per_severity(session)
    stats = DashboardStats(
        session, issue_min_severity_rank=resolve_issue_min_severity_rank("high")
    )
    overview = await stats.overview()
    assert overview.issues_open == 1


@pytest.mark.asyncio
async def test_sidebar_badges_honors_threshold(session: AsyncSession) -> None:
    """``sidebar_badges()`` and ``overview()`` must agree on what
    counts as an issue. Same seed, same threshold ⇒ same count."""
    await _seed_one_row_per_severity(session)
    stats = DashboardStats(
        session, issue_min_severity_rank=resolve_issue_min_severity_rank("warn")
    )
    badges = await stats.sidebar_badges()
    overview = await stats.overview()
    assert badges["issuesOpen"] == overview.issues_open == 2


@pytest.mark.asyncio
async def test_legacy_no_threshold_falls_back_to_total_minus_ok(
    session: AsyncSession,
) -> None:
    """Callers that don't pass a threshold (internal / pre-Stage-4)
    keep the original ``total - ok`` shape — info IS counted there.
    Pinned so any future migration to "threshold or bust" is a
    deliberate change."""
    await _seed_one_row_per_severity(session)
    stats = DashboardStats(session)  # no threshold ⇒ legacy path
    overview = await stats.overview()
    # info + warn + high = 3 (info counts under the legacy rule).
    assert overview.issues_open == 3


# ── Live API: runtime-settings override propagates ──────────────
@pytest_asyncio.fixture
async def api_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "dashboard_threshold.db"
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


@pytest.mark.asyncio
async def test_overview_endpoint_uses_default_warn_threshold(
    api_client: AsyncClient,
) -> None:
    headers = await _admin_headers(api_client)
    async with get_database().session() as sess:
        await _seed_one_row_per_severity(sess)

    r = await api_client.get("/api/v1/dashboard/overview", headers=headers)
    assert r.status_code == 200, r.text
    # Default threshold is "warn" ⇒ ok + info excluded ⇒ 2 issues.
    assert r.json()["issues_open"] == 2


@pytest.mark.asyncio
async def test_overview_endpoint_picks_up_runtime_override(
    api_client: AsyncClient,
) -> None:
    """Override the threshold via the runtime-settings API and
    confirm the next ``/dashboard/overview`` call returns the
    new count. This is the user-visible effect operators are
    after: change the slider, the badge updates without a
    restart."""
    headers = await _admin_headers(api_client)
    async with get_database().session() as sess:
        await _seed_one_row_per_severity(sess)

    # Baseline at default ⇒ 2.
    r = await api_client.get("/api/v1/dashboard/overview", headers=headers)
    assert r.json()["issues_open"] == 2

    # Override to "info" ⇒ info+warn+high all count ⇒ 3.
    put = await api_client.put(
        "/api/v1/system/runtime-settings/dashboard_issue_min_severity",
        headers=headers,
        json={"value": "info"},
    )
    assert put.status_code == 200, put.text

    r2 = await api_client.get("/api/v1/dashboard/overview", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["issues_open"] == 3

    # Override to "high" ⇒ only the one high row ⇒ 1.
    put_high = await api_client.put(
        "/api/v1/system/runtime-settings/dashboard_issue_min_severity",
        headers=headers,
        json={"value": "high"},
    )
    assert put_high.status_code == 200

    r3 = await api_client.get("/api/v1/dashboard/overview", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["issues_open"] == 1


@pytest.mark.asyncio
async def test_runtime_settings_rejects_invalid_threshold(
    api_client: AsyncClient,
) -> None:
    """The whitelist regex rejects anything outside info/warn/high/
    error/crit. ``ok`` is deliberately not in the set — if nothing
    counted as an issue, the entire concept would be meaningless."""
    headers = await _admin_headers(api_client)

    bad = await api_client.put(
        "/api/v1/system/runtime-settings/dashboard_issue_min_severity",
        headers=headers,
        json={"value": "ok"},
    )
    assert bad.status_code in (400, 422)

    bogus = await api_client.put(
        "/api/v1/system/runtime-settings/dashboard_issue_min_severity",
        headers=headers,
        json={"value": "bogus"},
    )
    assert bogus.status_code in (400, 422)


@pytest.mark.asyncio
async def test_sidebar_badges_endpoint_honors_threshold(
    api_client: AsyncClient,
) -> None:
    """Same contract on the sidebar-badges endpoint."""
    headers = await _admin_headers(api_client)
    async with get_database().session() as sess:
        await _seed_one_row_per_severity(sess)

    r = await api_client.get(
        "/api/v1/dashboard/sidebar-badges", headers=headers
    )
    assert r.status_code == 200
    # Default warn threshold ⇒ 2.
    assert r.json()["issuesOpen"] == 2
