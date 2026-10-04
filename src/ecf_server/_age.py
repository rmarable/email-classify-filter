"""A typed facade over the parts of `pyrage` ecf uses (SPEC §17.2: one facade per untyped library).

pyrage 1.4.0 (binds the `age` crate 0.12.1; read 2026-10-03; `passphrase.encrypt|decrypt` added
in step 9a): `x25519.Identity.from_str` takes an
`AGE-SECRET-KEY-1...` string and `to_public()` gives the `age1...` recipient; `encrypt(data,
[recipients])` and `decrypt(data, [identities])` work on whole buffers and raise
`EncryptError`/`DecryptError`. Keys pass through this module as their age strings.
"""

from __future__ import annotations

import importlib
from typing import Any

_pyrage: Any = importlib.import_module("pyrage")
_x25519: Any = importlib.import_module("pyrage.x25519")
_passphrase: Any = importlib.import_module("pyrage.passphrase")


class AgeError(Exception):
    """Encryption or decryption failed (wrong key, damaged data)."""


def recipient_of(identity: str) -> str:
    """The `age1...` recipient for an `AGE-SECRET-KEY-1...` identity."""
    try:
        return str(_x25519.Identity.from_str(identity).to_public())
    except Exception as exc:  # pyrage raises its own IdentityError
        raise AgeError("not an age identity") from exc


def encrypt(data: bytes, recipient: str) -> bytes:
    try:
        return bytes(_pyrage.encrypt(data, [_x25519.Recipient.from_str(recipient)]))
    except Exception as exc:
        raise AgeError(f"encryption failed: {type(exc).__name__}") from exc


def decrypt(data: bytes, identity: str) -> bytes:
    try:
        return bytes(_pyrage.decrypt(data, [_x25519.Identity.from_str(identity)]))
    except Exception as exc:
        raise AgeError(f"decryption failed: {type(exc).__name__}") from exc


def encrypt_passphrase(data: bytes, passphrase: str) -> bytes:
    """age's scrypt recipient; pyrage 1.4.0 doesn't expose the work factor (age's default)."""
    try:
        return bytes(_passphrase.encrypt(data, passphrase))
    except Exception as exc:
        raise AgeError(f"encryption failed: {type(exc).__name__}") from exc


def decrypt_passphrase(data: bytes, passphrase: str) -> bytes:
    try:
        return bytes(_passphrase.decrypt(data, passphrase))
    except Exception as exc:
        raise AgeError(f"decryption failed: {type(exc).__name__}") from exc
