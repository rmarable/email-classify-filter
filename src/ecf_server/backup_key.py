"""The backup key (SPEC §11.9; OD-327, OD-339 to OD-341; V1.5 step 8a). Pure functions, no state.

One 32-byte root value, shown once as text for a password manager. HKDF-SHA256 derives from it the
`age` X25519 identity that bundles are encrypted to and the Ed25519 seed they are signed with. The
running install keeps only the seed (secret store) and the public halves (settings), so it can
sign bundles but never read them; restore re-derives everything from the typed key.

**Key text** (OD-339): `ECF1-` then the root in Crockford base32 (52 characters) and a 4-character
checksum, in groups of 4. Parsing ignores case, spaces and hyphens and reads O as 0 and I or L as
1; the checksum catches a mistyped character.

**Fingerprint** (OD-341): the first 80 bits of SHA-256 over the age recipient and the Ed25519
public key, as 4 groups of 4 Crockford characters. Step-ups for key and `export_dir` changes show
it and ask you to type it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from nacl.signing import SigningKey, VerifyKey

from ecf.errors import InvalidInputError
from ecf_server import _age

ROOT_BYTES = 32
PREFIX = "ECF1"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)} | {"O": 0, "I": 1, "L": 1}
_LOOKALIKES = str.maketrans("OIL", "011")
_ROOT_CHARS = 52  # ceil(256 / 5)
_CHECK_CHARS = 4
_SALT = b"ecf backup key v1"
_INFO_AGE = b"ecf age x25519 identity"
_INFO_SIGN = b"ecf ed25519 signing seed"


@dataclass(frozen=True)
class PublicKeys:
    """What the running install keeps in settings: the halves that can't read a bundle."""

    recipient: str  # age1...
    verify_key: str  # Ed25519 public key, hex

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.recipient, bytes.fromhex(self.verify_key))


@dataclass(frozen=True)
class Derived:
    identity: str  # AGE-SECRET-KEY-1...: decrypts bundles; never stored by the service
    signing_seed: bytes  # stored in the secret store as `export-signing-seed`
    public: PublicKeys


def new_root() -> bytes:
    return secrets.token_bytes(ROOT_BYTES)


def derive(root: bytes) -> Derived:
    if len(root) != ROOT_BYTES:
        raise ValueError("a backup key root is 32 bytes")
    prk = hmac.digest(_SALT, root, "sha256")  # HKDF-Extract (RFC 5869)
    age_secret = _expand(prk, _INFO_AGE)
    seed = _expand(prk, _INFO_SIGN)
    identity = _bech32("age-secret-key-", age_secret).upper()
    verify = SigningKey(seed).verify_key
    return Derived(identity, seed, PublicKeys(_age.recipient_of(identity), bytes(verify).hex()))


def public_from_seed(seed: bytes, recipient: str) -> PublicKeys:
    return PublicKeys(recipient, bytes(SigningKey(seed).verify_key).hex())


def verify_key(public: PublicKeys) -> VerifyKey:
    return VerifyKey(bytes.fromhex(public.verify_key))


def fingerprint(recipient: str, verify: bytes) -> str:
    raw = hashlib.sha256(recipient.encode() + verify).digest()[:10]
    return _group(_b32(raw))


def same_fingerprint(typed: str, fp: str) -> bool:
    return hmac.compare_digest(_normalize(typed), _normalize(fp))


def key_text(root: bytes) -> str:
    return _group(PREFIX + _b32(root) + _check(root))


def parse_key_text(text: str) -> bytes:
    """The root from typed key text; InvalidInputError names what's wrong, never the text."""
    s = _normalize(text).translate(_LOOKALIKES)
    if not s.startswith(PREFIX):
        raise InvalidInputError(f"a backup key starts with {PREFIX}-")
    body = s[len(PREFIX) :]
    if len(body) != _ROOT_CHARS + _CHECK_CHARS:
        raise InvalidInputError("that backup key has the wrong length; check it against the copy"
                                " in your password manager")  # fmt: skip
    try:
        root = _unb32(body[:_ROOT_CHARS], ROOT_BYTES)
    except ValueError:
        raise InvalidInputError("that backup key has a character it can't contain") from None
    if body[_ROOT_CHARS:] != _check(root):
        raise InvalidInputError("that backup key has a typo (its checksum doesn't match)")
    return root


# ---- helpers ------------------------------------------------------------------------------------


def _expand(prk: bytes, info: bytes) -> bytes:
    """HKDF-Expand for one 32-byte block (RFC 5869: T(1) = HMAC(PRK, info || 0x01))."""
    return hmac.digest(prk, info + b"\x01", "sha256")


def _check(root: bytes) -> str:
    return _b32(hashlib.sha256(b"ecf backup key check" + root).digest()[:3])[:_CHECK_CHARS]


def _normalize(text: str) -> str:
    return "".join(text.split()).replace("-", "").upper()


def _group(s: str) -> str:
    return "-".join(s[i : i + 4] for i in range(0, len(s), 4))


def _b32(data: bytes) -> str:
    n, bits = int.from_bytes(data), len(data) * 8
    chars = -(-bits // 5)
    n <<= chars * 5 - bits
    return "".join(_CROCKFORD[(n >> (5 * (chars - 1 - i))) & 31] for i in range(chars))


def _unb32(s: str, size: int) -> bytes:
    n = 0
    for c in s:
        if c not in _DECODE:
            raise ValueError(c)
        n = n << 5 | _DECODE[c]
    pad = len(s) * 5 - size * 8
    if n & ((1 << pad) - 1):
        raise ValueError("padding bits set")
    return (n >> pad).to_bytes(size)


# Bech32 (BIP 173), as age encodes its keys; age sets no length limit.
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _bech32(hrp: str, data: bytes) -> str:
    acc, bits, words = 0, 0, list[int]()
    for b in data:
        acc, bits = acc << 8 | b, bits + 8
        while bits >= 5:
            bits -= 5
            words.append(acc >> bits & 31)
    if bits:
        words.append(acc << (5 - bits) & 31)
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    mod = _polymod([*expanded, *words, 0, 0, 0, 0, 0, 0]) ^ 1
    check = [mod >> 5 * (5 - i) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32[w] for w in words + check)
