"""Password hashing using argon2id.

Argon2id is mandated by the project specification. We tune for the
"sensitive" preset from the argon2 reference (RFC 9106) but keep parameters
adjustable through settings for future tuning without breaking existing hashes
(the encoded hash carries its own parameters).
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.logging import get_logger

log = get_logger("auditarr.security.passwords", category="security")

# Argon2id parameters. Values balance security and login latency on commodity
# hardware; the produced hash is self-describing so changing these later does
# not invalidate older hashes.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plaintext: str) -> str:
    """Return a self-contained argon2id hash for *plaintext*."""
    if not plaintext:
        raise ValueError("password must not be empty")
    return _HASHER.hash(plaintext)


def verify_password(plaintext: str, encoded: str) -> bool:
    """Return True iff *plaintext* matches *encoded*."""
    try:
        return _HASHER.verify(encoded, plaintext)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        log.warning("password.invalid_hash_format")
        return False


def needs_rehash(encoded: str) -> bool:
    """Whether the stored hash should be upgraded with current parameters."""
    try:
        return _HASHER.check_needs_rehash(encoded)
    except InvalidHashError:
        return True
