"""Symmetric secret box tests."""

from __future__ import annotations

import pytest

from app.core.settings import Settings
from app.security.secrets import SecretBox, SecretDecryptionError


def _box(key: str = "x" * 32) -> SecretBox:
    return SecretBox(Settings(secret_key=key))


def test_round_trip() -> None:
    box = _box()
    cipher = box.encrypt_dict({"token": "shh"})
    assert isinstance(cipher, str) and len(cipher) > 0
    assert box.decrypt_dict(cipher) == {"token": "shh"}


def test_different_nonce_per_encryption() -> None:
    box = _box()
    a = box.encrypt_dict({"k": "v"})
    b = box.encrypt_dict({"k": "v"})
    # Same plaintext, different ciphertext (fresh nonce each time).
    assert a != b


def test_wrong_key_rejected() -> None:
    a = _box(key="a" * 32)
    b = _box(key="b" * 32)
    cipher = a.encrypt_dict({"x": 1})
    with pytest.raises(SecretDecryptionError):
        b.decrypt_dict(cipher)


def test_tampered_ciphertext_rejected() -> None:
    """Flipping a byte in the GCM-protected region MUST cause
    decryption to fail. We mutate at base64 index -4 (which falls
    inside the ciphertext + auth tag, not in the trailing ``=``
    padding) and pick a replacement character DIFFERENT from what's
    actually there.

    The original test compared ``cipher[-1] != "A"`` to pick the
    replacement, but ``cipher[-1]`` is always the base64 ``=``
    padding byte for this plaintext size — so the replacement was
    effectively hard-coded to ``"A"`` and produced a no-op flip
    roughly 1/64 of the time (whenever ``cipher[-4]`` happened to
    already be ``"A"``). That manifested as a ~1.5%-rate flake.
    """
    box = _box()
    cipher = box.encrypt_dict({"x": 1})
    target = cipher[-4]
    replacement = "A" if target != "A" else "B"
    assert replacement != target, "test bug: replacement char must differ"
    bad = cipher[:-4] + replacement + cipher[-3:]
    assert bad != cipher, "test bug: tampered ciphertext must differ"
    with pytest.raises(SecretDecryptionError):
        box.decrypt_dict(bad)


def test_garbage_input_rejected() -> None:
    box = _box()
    with pytest.raises(SecretDecryptionError):
        box.decrypt_dict("not base64!!!")


def test_empty_token_returns_empty_dict() -> None:
    box = _box()
    assert box.decrypt_dict("") == {}
