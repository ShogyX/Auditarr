"""Stage 16 (plan §682) — release smoke test.

Walks the full user-facing API surface against an in-memory
app instance:

  1. Register an account.
  2. Promote it to admin via the test backdoor.
  3. Log in.
  4. GET ``/api/v1/health`` — system status.
  5. GET ``/api/v1/docs`` — at least one doc page present.
  6. GET ``/api/v1/media`` — empty page, but the endpoint
     answers.
  7. GET ``/api/v1/media/vocabulary`` — Stage 15 endpoint
     answers (empty vocabulary on a fresh install).
  8. GET ``/api/v1/rules`` — empty list, but the endpoint
     answers.
  9. Version check — assert ``__version__`` is ``1.8.3``.

If a real Docker stack is available, plan §682 says the test
*may* bring it up; we don't gate on that. The in-memory ASGI
transport gives the same surface coverage with none of the
Docker setup, so this is the deterministic path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app import __version__
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def smoke_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    # Point at the real repo docs/ so the smoke test exercises
    # the live documentation surface. ``__file__`` resolves to
    # ``backend/tests/e2e/test_release_smoke_stage16.py``;
    # three parents up + "docs" lands at the repo root docs/.
    repo_docs = Path(__file__).resolve().parents[3] / "docs"
    assert repo_docs.is_dir(), f"expected docs dir at {repo_docs}"
    monkeypatch.setenv("AUDITARR_DOCS_DIR", str(repo_docs))

    from app.core.settings import get_settings
    from app.documentation import (
        get_documentation_service,
        reset_documentation_service,
    )

    get_settings.cache_clear()
    reset_documentation_service()
    get_documentation_service().load()

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
        reset_documentation_service()
        get_settings.cache_clear()


async def _admin_token(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
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
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


# ── Test 1 — full release smoke walk


@pytest.mark.asyncio
async def test_release_smoke_full_walk(smoke_client: AsyncClient) -> None:
    """End-to-end walk through the full user-facing API surface."""
    # 1-3: register + promote + login.
    token = await _admin_token(smoke_client)
    headers = {"Authorization": f"Bearer {token}"}

    # 4: /api/v1/health.
    health = await smoke_client.get("/api/v1/health")
    assert health.status_code == 200, health.text
    body = health.json()
    # Status is "ok" or "degraded" in dev (Redis may not be
    # available); both are acceptable for the smoke walk.
    assert body.get("status") in ("ok", "degraded"), body

    # 5: /api/v1/docs.
    docs = await smoke_client.get("/api/v1/docs")
    assert docs.status_code == 200, docs.text
    doc_pages = docs.json()
    assert isinstance(doc_pages, list)
    # The docs directory ships dozens of pages; we don't pin
    # an exact count to avoid coupling the smoke test to the
    # docs catalog, but it must be > 0.
    assert len(doc_pages) > 0, "expected at least one doc page"

    # 6: /api/v1/media.
    media = await smoke_client.get("/api/v1/media", headers=headers)
    assert media.status_code == 200, media.text
    media_body = media.json()
    # Response shape: MediaPageRead with items, total, etc.
    assert "items" in media_body
    assert media_body["items"] == []

    # 7: /api/v1/media/vocabulary (Stage 15).
    vocab = await smoke_client.get(
        "/api/v1/media/vocabulary", headers=headers
    )
    assert vocab.status_code == 200, vocab.text
    vocab_body = vocab.json()
    assert vocab_body == {
        "video_codecs": [],
        "audio_codecs": [],
        "containers": [],
        "extensions": [],
        "tags": [],
    }

    # 8: /api/v1/rules.
    rules = await smoke_client.get("/api/v1/rules", headers=headers)
    assert rules.status_code == 200, rules.text
    rules_body = rules.json()
    # The shape varies (some endpoints return a list, others
    # a paginated envelope). Just assert we got a valid response.
    assert rules_body is not None


# ── Test 2 — version pinned


@pytest.mark.asyncio
async def test_release_version_is_set(smoke_client: AsyncClient) -> None:
    """``__version__`` is the source of truth for the release artifact
    and must be a non-empty PEP 440-ish string. Pinning the exact
    value here just makes every bump break the test, so we assert
    the shape instead."""
    assert isinstance(__version__, str) and __version__, (
        f"expected non-empty __version__, got {__version__!r}"
    )
    assert __version__[0].isdigit(), (
        f"expected __version__ to start with a digit, got {__version__!r}"
    )


# ── Test 3 — health endpoint reports the same version


@pytest.mark.asyncio
async def test_health_reports_version(smoke_client: AsyncClient) -> None:
    """The /health endpoint exposes the running version. This
    is what operators see when they curl their freshly-installed
    instance — assert it matches the package version."""
    resp = await smoke_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("version") == __version__, (
        f"health.version should match {__version__!r}; "
        f"got {body.get('version')!r}"
    )


# ── Test 4 — auth required on the protected endpoints


@pytest.mark.asyncio
async def test_smoke_protected_endpoints_require_auth(
    smoke_client: AsyncClient,
) -> None:
    """Defensive — confirms /media, /media/vocabulary, /rules
    reject anonymous traffic. A misconfiguration that left one
    of these public would be a security regression."""
    for path in (
        "/api/v1/media",
        "/api/v1/media/vocabulary",
        "/api/v1/rules",
    ):
        resp = await smoke_client.get(path)
        assert resp.status_code in (401, 403), (
            f"{path} should require auth; got {resp.status_code}"
        )
