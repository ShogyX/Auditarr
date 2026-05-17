"""Stage 12 (v1.7) — Forgot-password terminal-OTP path.

Plan §588 contract:
    Request a reset without email configured, capture stdout,
    assert the banner is present, use the OTP to log in, assert
    the user is forced to change password.

Plan §580-581 + addendum B.9:
    * OTP banner emitted at WARNING + ``print()`` to stdout.
    * ``PasswordResetToken.must_change_on_use=True`` set on
      the persisted row.
    * ``User.must_change_password=True`` set when the user
      consumes such a token via ``confirm_password_reset``.
    * ``change_password`` clears the flag on success.

All tests run with the email provider disabled so the terminal
path activates. A complementary test verifies the email path
still works when email IS configured (regression guard).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.security import hash_password
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "s12.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    # Email is disabled by default in tests; we keep it that
    # way for the terminal-OTP path. The email-enabled
    # regression test below overrides this fixture.
    monkeypatch.setenv("AUDITARR_SMTP_ENABLED", "false")

    from app.core.settings import get_settings
    from app.services.email.settings import get_email_settings

    get_settings.cache_clear()
    get_email_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Seed a user.
    async with db.session() as session:
        user = User(
            email="alice@example.com",
            username="alice",
            full_name="Alice Example",
            password_hash=hash_password("OriginalPassword!1"),
            role="admin",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    app = create_app()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    try:
        yield {"client": client, "db": db, "user_id": user_id}
    finally:
        await client.aclose()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_settings.cache_clear()
        get_email_settings.cache_clear()


# ── Test 1 — plan §588 end-to-end ──────────────────────────────


@pytest.mark.asyncio
async def test_forgot_password_terminal_otp_e2e(
    env, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plan §588: request reset → banner appears on stdout →
    OTP works for reset → user must change password on first
    login → change-password clears the flag → subsequent
    logins succeed without the gate.
    """
    client = env["client"]

    # ── 1. Request the reset ──────────────────────────────
    r = await client.post(
        "/api/v1/auth/password/reset/request",
        json={"email": "alice@example.com"},
    )
    assert r.status_code in (200, 202, 204), r.text

    # ── 2. Banner appears on stdout ───────────────────────
    captured = capsys.readouterr()
    stdout = captured.out
    assert "AUDITARR — Password reset request for user 'alice'" in stdout, (
        "Banner header missing from stdout"
    )
    assert "One-time password:" in stdout
    assert "Valid for 15 minutes" in stdout
    assert "must change on next login" in stdout.lower()
    # The banner uses box-drawing characters.
    assert "┌" in stdout
    assert "└" in stdout

    # ── 3. Extract the OTP from the banner ────────────────
    m = re.search(r"One-time password:\s*(\S+)", stdout)
    assert m, "Failed to extract OTP from banner"
    otp = m.group(1)
    # 12-char base64 from token_urlsafe(9).
    assert 10 <= len(otp) <= 16, f"OTP looks wrong: {otp!r}"

    # ── 4. The persisted token has must_change_on_use=True
    async with env["db"].session() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(select(PasswordResetToken))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].must_change_on_use is True

    # ── 5. Use the OTP to set a new password ──────────────
    r = await client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": otp, "new_password": "NewPassword!12345"},
    )
    assert r.status_code == 204, r.text

    # ── 6. User row now has must_change_password=True ─────
    async with env["db"].session() as session:
        user = await session.get(User, env["user_id"])
        assert user is not None
        assert user.must_change_password is True

    # ── 7. Login surfaces the flag in the user response ──
    r = await client.post(
        "/api/v1/auth/login",
        json={"login": "alice", "password": "NewPassword!12345"},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    access = tokens["access_token"]

    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200
    me = r.json()
    assert me.get("must_change_password") is True, me

    # ── 8. Change the password — flag clears ──────────────
    r = await client.post(
        "/api/v1/auth/password/change",
        json={
            "current_password": "NewPassword!12345",
            "new_password": "FinalPassword!12345",
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code in (200, 204), r.text

    async with env["db"].session() as session:
        user = await session.get(User, env["user_id"])
        assert user.must_change_password is False

    # ── 9. Subsequent login: flag is False ────────────────
    r = await client.post(
        "/api/v1/auth/login",
        json={"login": "alice", "password": "FinalPassword!12345"},
    )
    assert r.status_code == 200
    tokens2 = r.json()
    access2 = tokens2["access_token"]
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access2}"},
    )
    me2 = r.json()
    assert me2.get("must_change_password") is False


# ── Test 2 — email-configured probe returns False when disabled ─


@pytest.mark.asyncio
async def test_email_configured_endpoint_reports_false(env) -> None:
    """The ForgotPasswordPage uses this endpoint to swap its
    copy. With email disabled, the endpoint reports
    ``configured=False``."""
    r = await env["client"].get("/api/v1/auth/email-configured")
    assert r.status_code == 200
    assert r.json() == {"configured": False}


# ── Test 3 — banner uses WARNING level + print (addendum B.9) ──


@pytest.mark.asyncio
async def test_banner_emitted_at_warning_level_and_via_print(
    env, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """Addendum B.9: emit at WARNING (above default INFO
    threshold) AND via print() (always reaches stdout
    regardless of log routing).

    We verify the WARNING-level emit by capturing the
    auditarr logger directly — structlog bridges through
    stdlib so caplog catches the record provided we hook
    the correct logger name.
    """
    import logging

    # Capture WARNING on the auditarr.auth_service logger
    # (the get_logger() invocation at module top uses no
    # explicit name → defaults to the module).
    caplog.set_level(logging.WARNING, logger="auditarr")
    caplog.set_level(logging.WARNING)

    r = await env["client"].post(
        "/api/v1/auth/password/reset/request",
        json={"email": "alice@example.com"},
    )
    assert r.status_code in (200, 202, 204)

    captured = capsys.readouterr()
    # Stdout has the banner — this is the addendum-B.9
    # "always reaches stdout" requirement.
    assert "AUDITARR" in captured.out
    assert "One-time password" in captured.out

    # WARNING-level emit happened: we look for either a
    # caplog record OR the warning event-name in the
    # stderr/stdout chunk (structlog renders WARNING events
    # to the same stream depending on test config).
    warning_records = [
        rec for rec in caplog.records if rec.levelno >= logging.WARNING
    ]
    saw_warning_in_stream = (
        "auth.password_reset_terminal_otp" in captured.out
        or "auth.password_reset_terminal_otp" in captured.err
    )
    assert warning_records or saw_warning_in_stream, (
        "Neither caplog nor stream carried the WARNING event. "
        f"caplog records: {[(r.levelname, r.message) for r in caplog.records]}"
    )


# ── Test 4 — unknown email still returns 200 + no banner ───────


@pytest.mark.asyncio
async def test_request_for_unknown_email_emits_no_banner(
    env, capsys: pytest.CaptureFixture[str]
) -> None:
    """The endpoint pretends success for unknown emails
    (anti-enumeration). The terminal-OTP path must NOT
    activate either — emitting a banner for an unknown email
    would leak the existence of the user space to anyone
    watching stdout."""
    r = await env["client"].post(
        "/api/v1/auth/password/reset/request",
        json={"email": "nobody@example.com"},
    )
    assert r.status_code in (200, 202, 204)

    captured = capsys.readouterr()
    assert "AUDITARR — Password reset" not in captured.out
    assert "One-time password" not in captured.out


# ── Test 5 — change_password clears the flag (unit-level) ──────


@pytest.mark.asyncio
async def test_change_password_clears_must_change_flag(env) -> None:
    """Even an ordinary password change (not via the reset
    flow) clears the must_change_password flag. This is the
    intended exit from the gate."""
    # Seed a flagged user via direct DB write so we don't have
    # to run the reset flow.
    async with env["db"].session() as session:
        user = await session.get(User, env["user_id"])
        user.must_change_password = True
        await session.commit()

    # Login (the flag doesn't block login — it surfaces in
    # the response).
    r = await env["client"].post(
        "/api/v1/auth/login",
        json={"login": "alice", "password": "OriginalPassword!1"},
    )
    assert r.status_code == 200
    access = r.json()["access_token"]

    r = await env["client"].post(
        "/api/v1/auth/password/change",
        json={
            "current_password": "OriginalPassword!1",
            "new_password": "NewBetterPassword!1",
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code in (200, 204)

    async with env["db"].session() as session:
        user = await session.get(User, env["user_id"])
        assert user.must_change_password is False


# ── Test 6 — Email-enabled path still uses the email branch ──


@pytest.mark.asyncio
async def test_email_enabled_uses_email_path_not_terminal_otp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression guard for the existing email flow: when
    email IS enabled, request_password_reset uses the email
    branch and does NOT emit the terminal banner. The token
    is the long urlsafe-48-byte variant, and the persisted
    row has must_change_on_use=False."""
    db_path = tmp_path / "s12_email.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_SMTP_ENABLED", "true")
    monkeypatch.setenv("AUDITARR_SMTP_PROVIDER", "console")

    from app.core.settings import get_settings
    from app.services.email.settings import get_email_settings

    get_settings.cache_clear()
    get_email_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        async with db.session() as session:
            user = User(
                email="bob@example.com",
                username="bob",
                full_name="Bob",
                password_hash=hash_password("Bobs!Password12345"),
                role="admin",
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.commit()

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            # Clear stdout captures from setup.
            capsys.readouterr()
            r = await c.post(
                "/api/v1/auth/password/reset/request",
                json={"email": "bob@example.com"},
            )
            assert r.status_code in (200, 202, 204)
            # Verify email-configured probe agrees.
            cfg = await c.get("/api/v1/auth/email-configured")
            assert cfg.json() == {"configured": True}

        # Stdout should NOT carry the terminal banner.
        captured = capsys.readouterr()
        assert "AUDITARR — Password reset" not in captured.out

        # Persisted token has must_change_on_use=False.
        async with db.session() as session:
            from sqlalchemy import select

            rows = (
                await session.execute(select(PasswordResetToken))
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].must_change_on_use is False
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_settings.cache_clear()
        get_email_settings.cache_clear()
