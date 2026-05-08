"""
auth.py — Single-user authentication with optional API token.

Stores credentials in auth.json (separate from config.json so users can keep
config in version control without leaking the hash).

  {
    "username":  "shogyx",
    "pw_salt":   "<hex>",
    "pw_hash":   "<hex>",      # PBKDF2-HMAC-SHA256, 200000 iters
    "api_token": "<hex>",      # optional, for headless/script API access
    "created_at": "...",
    "updated_at": "..."
  }

Sessions: in-memory dict {session_token: {username, expires}}. Cookies are
HttpOnly, SameSite=Strict, Secure if served over HTTPS.
"""
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

AUTH_FILE = Path(__file__).parent / "auth.json"
SESSION_TTL = 60 * 60 * 24 * 14  # 14 days
PBKDF2_ITERATIONS = 200_000

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _read_auth() -> dict | None:
    if not AUTH_FILE.exists():
        return None
    try:
        return json.loads(AUTH_FILE.read_text())
    except Exception:
        return None


def _write_auth(d: dict):
    AUTH_FILE.write_text(json.dumps(d, indent=2))
    try:
        # Restrict perms so other users on the box can't read the hash
        AUTH_FILE.chmod(0o600)
    except Exception:
        pass


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def is_configured() -> bool:
    """Has the admin user been set up yet?"""
    a = _read_auth()
    return bool(a and a.get("username") and a.get("pw_hash"))


def setup(username: str, password: str) -> dict:
    """Create the single admin user. Generates an API token at the same time."""
    if not username or not password:
        raise ValueError("Username and password required")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    pw_hash = _hash_password(password, salt)
    api_token = secrets.token_hex(32)
    now = datetime.now().isoformat()
    data = {
        "username": username,
        "pw_salt": salt.hex(),
        "pw_hash": pw_hash.hex(),
        "api_token": api_token,
        "created_at": now,
        "updated_at": now,
    }
    _write_auth(data)
    return {"username": username, "api_token": api_token}


def verify_password(username: str, password: str) -> bool:
    a = _read_auth()
    if not a: return False
    if a.get("username") != username: return False
    try:
        salt = bytes.fromhex(a["pw_salt"])
        expected = bytes.fromhex(a["pw_hash"])
    except Exception:
        return False
    actual = _hash_password(password, salt)
    return secrets.compare_digest(actual, expected)


def change_password(old_password: str, new_password: str) -> bool:
    a = _read_auth()
    if not a: return False
    if not verify_password(a["username"], old_password): return False
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    pw_hash = _hash_password(new_password, salt)
    a["pw_salt"] = salt.hex()
    a["pw_hash"] = pw_hash.hex()
    a["updated_at"] = datetime.now().isoformat()
    _write_auth(a)
    # Invalidate all sessions
    with _sessions_lock:
        _sessions.clear()
    return True


def regenerate_api_token() -> str:
    a = _read_auth()
    if not a: raise RuntimeError("Auth not configured")
    a["api_token"] = secrets.token_hex(32)
    a["updated_at"] = datetime.now().isoformat()
    _write_auth(a)
    return a["api_token"]


def verify_api_token(token: str) -> bool:
    a = _read_auth()
    if not a: return False
    expected = a.get("api_token")
    if not expected: return False
    return secrets.compare_digest(token, expected)


def get_api_token() -> str | None:
    a = _read_auth()
    return a.get("api_token") if a else None


def get_username() -> str | None:
    a = _read_auth()
    return a.get("username") if a else None


# ─── Sessions ─────────────────────────────────────────────────────────────────

def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {"username": username, "expires": _now() + SESSION_TTL}
    return token


def get_session(token: str) -> dict | None:
    if not token: return None
    with _sessions_lock:
        s = _sessions.get(token)
        if not s: return None
        if s["expires"] < _now():
            _sessions.pop(token, None)
            return None
    return s


def destroy_session(token: str):
    with _sessions_lock:
        _sessions.pop(token, None)


def cleanup_expired_sessions():
    now = _now()
    with _sessions_lock:
        expired = [t for t, s in _sessions.items() if s["expires"] < now]
        for t in expired: _sessions.pop(t, None)


# ─── Flask helpers ────────────────────────────────────────────────────────────

def is_request_authenticated(request) -> tuple[bool, str | None]:
    """Returns (authenticated, username_or_None)."""
    # 1. API token via header (preferred for scripts)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if verify_api_token(token):
            return True, get_username()
    # Also accept X-API-Key header (common pattern)
    api_key = request.headers.get("X-API-Key")
    if api_key and verify_api_token(api_key):
        return True, get_username()

    # 2. Session cookie
    session_token = request.cookies.get("auditarr_session")
    s = get_session(session_token)
    if s:
        return True, s["username"]

    return False, None
