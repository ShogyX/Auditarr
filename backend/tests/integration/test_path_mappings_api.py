"""Tests for the cross-integration path-mappings surface (Stage 21).

These pin:

- The list endpoint aggregates mappings across every integration row.
- Empty integration rows (no mappings configured) still appear so
  the UI can render them as "configurable but empty".
- Reads are non-admin-visible (debugging convenience); writes are
  admin-only.
- Writes round-trip through the same parser the scanner uses, so
  the stored value matches what the scanner reads back.
- Malformed entries (empty 'from' / 'to') are rejected as 422
  rather than silently dropped.
- Non-existent integration IDs return 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.main import create_app
from app.models.integration import Integration
from app.models.user import User
from app.storage.base import Base
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "path_mappings.db"
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()

    app = create_app()
    db = get_database()
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


async def _login(client: AsyncClient, *, admin: bool) -> dict[str, str]:
    email = f"{'admin' if admin else 'user'}@example.com"
    username = "adminuser" if admin else "regularuser"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    if admin:
        async with get_database().session() as sess:
            await sess.execute(
                update(User).where(User.id == user_id).values(role="admin")
            )
            await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": PASSWORD},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_integration(
    *, name: str, kind: str, mappings: list[dict[str, str]] | None = None
) -> str:
    async with get_database().session() as sess:
        ig = Integration(
            name=name,
            kind=kind,
            config={"path_mappings": mappings} if mappings is not None else {},
        )
        sess.add(ig)
        await sess.commit()
        await sess.refresh(ig)
        return ig.id


# ── Read endpoint ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_read_is_visible_to_non_admin(client: AsyncClient) -> None:
    """Path mappings are operational debugging info — operators
    triaging "why didn't Plex resolve this path" should be able to
    see them without admin. Pin that decision."""
    await _seed_integration(
        name="Plex Home",
        kind="plex",
        mappings=[{"from": "/data/movies", "to": "/mnt/media/Movies"}],
    )
    headers = await _login(client, admin=False)
    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_read_aggregates_across_integrations(
    client: AsyncClient,
) -> None:
    await _seed_integration(
        name="Plex Home", kind="plex",
        mappings=[{"from": "/data/movies", "to": "/mnt/media/Movies"}],
    )
    await _seed_integration(
        name="Sonarr", kind="sonarr",
        mappings=[
            {"from": "/tv", "to": "/mnt/media/TV"},
            {"from": "/data/anime", "to": "/mnt/media/Anime"},
        ],
    )
    await _seed_integration(name="Empty", kind="radarr", mappings=[])

    headers = await _login(client, admin=True)
    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    assert r.status_code == 200
    by_name = {e["name"]: e for e in r.json()["integrations"]}

    # Every integration appears, including the empty one.
    assert set(by_name.keys()) == {"Empty", "Plex Home", "Sonarr"}
    assert by_name["Empty"]["mappings"] == []
    assert len(by_name["Sonarr"]["mappings"]) == 2


@pytest.mark.asyncio
async def test_read_includes_kind_and_id(client: AsyncClient) -> None:
    """The UI uses kind to pick the right icon, and id to navigate
    to the integration page for deeper editing."""
    ig_id = await _seed_integration(name="Plex", kind="plex", mappings=[])
    headers = await _login(client, admin=True)
    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    entries = r.json()["integrations"]
    plex = next(e for e in entries if e["name"] == "Plex")
    assert plex["integration_id"] == ig_id
    assert plex["kind"] == "plex"


# ── Write endpoint — admin gating ─────────────────────────
@pytest.mark.asyncio
async def test_write_requires_admin(client: AsyncClient) -> None:
    ig_id = await _seed_integration(name="Plex", kind="plex")
    headers = await _login(client, admin=False)
    r = await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={"mappings": [{"from": "/a", "to": "/b"}]},
    )
    assert r.status_code == 403


# ── Write endpoint — happy path ───────────────────────────
@pytest.mark.asyncio
async def test_write_replaces_mappings(client: AsyncClient) -> None:
    ig_id = await _seed_integration(
        name="Plex", kind="plex",
        mappings=[{"from": "/old", "to": "/new"}],
    )
    headers = await _login(client, admin=True)
    r = await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={
            "mappings": [
                {"from": "/data/movies", "to": "/mnt/media/Movies"},
                {"from": "/data/tv", "to": "/mnt/media/TV"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["mappings"]) == 2

    # Confirm the new mappings are what reads back — and the old one
    # is gone.
    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    plex = next(e for e in r.json()["integrations"] if e["name"] == "Plex")
    paths = {m["from"] for m in plex["mappings"]}
    assert paths == {"/data/movies", "/data/tv"}
    assert "/old" not in paths


@pytest.mark.asyncio
async def test_write_empty_list_clears_mappings(client: AsyncClient) -> None:
    ig_id = await _seed_integration(
        name="Plex", kind="plex",
        mappings=[{"from": "/a", "to": "/b"}],
    )
    headers = await _login(client, admin=True)
    r = await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={"mappings": []},
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    plex = next(e for e in r.json()["integrations"] if e["name"] == "Plex")
    assert plex["mappings"] == []


# ── Write endpoint — validation ───────────────────────────
@pytest.mark.asyncio
async def test_write_rejects_empty_from(client: AsyncClient) -> None:
    """The schema enforces min_length=1 on each field — empty 'from'
    is rejected at the pydantic layer."""
    ig_id = await _seed_integration(name="Plex", kind="plex")
    headers = await _login(client, admin=True)
    r = await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={"mappings": [{"from": "", "to": "/somewhere"}]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_write_rejects_slash_only_path(client: AsyncClient) -> None:
    """``/`` becomes empty after trim — parse_mappings drops it, but
    we want the API to reject rather than silently apply a partial
    update."""
    ig_id = await _seed_integration(name="Plex", kind="plex")
    headers = await _login(client, admin=True)
    r = await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={"mappings": [{"from": "/", "to": "/somewhere"}]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_write_unknown_integration_returns_404(
    client: AsyncClient,
) -> None:
    headers = await _login(client, admin=True)
    r = await client.put(
        "/api/v1/system/path-mappings/does-not-exist",
        headers=headers,
        json={"mappings": []},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_write_preserves_other_config_keys(
    client: AsyncClient,
) -> None:
    """The endpoint must not blow away other config keys (e.g.
    base_url, libraries) when it rewrites path_mappings."""
    async with get_database().session() as sess:
        ig = Integration(
            name="Plex",
            kind="plex",
            config={
                "base_url": "http://plex.lan:32400",
                "library_section_ids": [1, 2],
                "path_mappings": [{"from": "/old", "to": "/new"}],
            },
        )
        sess.add(ig)
        await sess.commit()
        ig_id = ig.id

    headers = await _login(client, admin=True)
    await client.put(
        f"/api/v1/system/path-mappings/{ig_id}",
        headers=headers,
        json={"mappings": [{"from": "/a", "to": "/b"}]},
    )
    # Verify the other config keys survived.
    async with get_database().session() as sess:
        ig = await sess.get(Integration, ig_id)
        assert ig is not None
        assert ig.config["base_url"] == "http://plex.lan:32400"
        assert ig.config["library_section_ids"] == [1, 2]
        assert ig.config["path_mappings"] == [{"from": "/a", "to": "/b"}]
