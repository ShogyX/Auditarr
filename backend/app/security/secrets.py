"""Symmetric encryption for integration secrets.

Uses AES-256-GCM. The key is derived from ``settings.secret_key`` via HKDF
with a domain-separation context, so the same Auditarr instance can encrypt
secrets without an additional configuration step. Rotation can later swap
the version byte and add a key-id table.

Wire format::

    base64( v=0x01 || nonce(12B) || ciphertext_with_tag )

A ``ServiceUnavailableError`` is raised if encryption parameters are not
available; the secret is never silently stored in plaintext.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.exceptions import AuditarrError, ServiceUnavailableError
from app.core.settings import Settings

_VERSION = 0x01
_NONCE_LEN = 12
_HKDF_INFO = b"auditarr/integrations/secrets/v1"


class SecretDecryptionError(AuditarrError):
    """Raised when a secret blob cannot be decrypted (corrupt or wrong key)."""

    code = "secret_decryption_failed"
    status_code = 500


class SecretBox:
    """Encrypt and decrypt small JSON blobs using AES-256-GCM."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key = self._derive_key(settings.secret_key)

    @staticmethod
    def _derive_key(secret_key: str) -> bytes:
        if not secret_key or len(secret_key) < 16:
            raise ServiceUnavailableError(
                "AUDITARR_SECRET_KEY is not configured strongly enough for "
                "secret encryption (minimum 16 characters)."
            )
        # HKDF-Extract+Expand to 32 bytes for AES-256.
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"auditarr/v1",
            info=_HKDF_INFO,
        ).derive(secret_key.encode("utf-8"))

    def encrypt_dict(self, data: dict[str, Any]) -> str:
        """Encrypt a dict; returns a base64 token suitable for DB storage."""
        plaintext = json.dumps(data, sort_keys=True).encode("utf-8")
        nonce = os.urandom(_NONCE_LEN)
        aesgcm = AESGCM(self._key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        blob = bytes([_VERSION]) + nonce + ciphertext
        return base64.b64encode(blob).decode("ascii")

    def decrypt_dict(self, token: str) -> dict[str, Any]:
        if not token:
            return {}
        try:
            blob = base64.b64decode(token, validate=True)
        except (ValueError, TypeError) as exc:
            raise SecretDecryptionError(
                "Invalid secret encoding"
            ) from exc
        if len(blob) < 1 + _NONCE_LEN + 16:
            raise SecretDecryptionError("Secret blob too short")
        version = blob[0]
        if version != _VERSION:
            raise SecretDecryptionError(
                f"Unsupported secret version 0x{version:02x}"
            )
        nonce = blob[1 : 1 + _NONCE_LEN]
        ciphertext = blob[1 + _NONCE_LEN :]
        aesgcm = AESGCM(self._key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise SecretDecryptionError(
                "Secret authentication failed (wrong key or tampered data)"
            ) from exc
        try:
            return json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise SecretDecryptionError("Secret payload is not JSON") from exc

    # ── Stage 21: bytes-in/bytes-out helpers ─────────────────
    # The encrypted_secrets table stores opaque ciphertext bytes
    # (LargeBinary column), not a base64-encoded dict blob. These
    # helpers expose the raw AES-GCM box without the JSON+base64
    # wrapper used by integration secrets.
    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        """Encrypt raw bytes. Returns the wire-format blob:
        ``version || nonce || ciphertext_with_tag``."""
        nonce = os.urandom(_NONCE_LEN)
        aesgcm = AESGCM(self._key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return bytes([_VERSION]) + nonce + ciphertext

    def decrypt_bytes(self, blob: bytes) -> bytes:
        """Decrypt a blob produced by :meth:`encrypt_bytes`."""
        if not blob or len(blob) < 1 + _NONCE_LEN + 16:
            raise SecretDecryptionError("Secret blob too short")
        version = blob[0]
        if version != _VERSION:
            raise SecretDecryptionError(
                f"Unsupported secret version 0x{version:02x}"
            )
        nonce = blob[1 : 1 + _NONCE_LEN]
        ciphertext = blob[1 + _NONCE_LEN :]
        aesgcm = AESGCM(self._key)
        try:
            return aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise SecretDecryptionError(
                "Secret authentication failed (wrong key or tampered data)"
            ) from exc


_box: SecretBox | None = None


def get_secret_box() -> SecretBox:
    global _box
    if _box is None:
        from app.core.settings import get_settings

        _box = SecretBox(get_settings())
    return _box


def reset_secret_box() -> None:
    """Test helper."""
    global _box
    _box = None
