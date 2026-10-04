"""Passphrases for manual exports (SPEC §11.9; OD-326, OD-349; V1.5 step 9a).

ecf offers six words drawn at random from the BIP 39 English list (2048 words, 66 bits;
`data/wordlist/`, MIT). A passphrase you type instead needs at least 20 characters or at least 5
different words. The service checks it; the CLI reads it without echo.
"""

from __future__ import annotations

import secrets
from functools import cache
from importlib import resources

from ecf.errors import InvalidInputError

WORDS = 6
MIN_CHARS = 20
MIN_WORDS = 5
LIST_SIZE = 2048


@cache
def wordlist() -> tuple[str, ...]:
    text = resources.files("ecf_server.data.wordlist").joinpath("bip39-english.txt")
    words = tuple(w for w in text.read_text("ascii").split() if w)
    if len(words) != LIST_SIZE or len(set(words)) != LIST_SIZE:
        raise RuntimeError("the passphrase wordlist is damaged; reinstall ecf")
    return words


def generate() -> str:
    words = wordlist()
    return " ".join(secrets.choice(words) for _ in range(WORDS))


def check(passphrase: str) -> str:
    """The passphrase when strong enough (OD-326); else InvalidInputError, never echoing it."""
    if any(ord(c) < 32 or ord(c) == 127 for c in passphrase):
        raise InvalidInputError("the passphrase has a control character")
    if len(passphrase) >= MIN_CHARS or len(set(passphrase.split())) >= MIN_WORDS:
        return passphrase
    raise InvalidInputError(f"too weak: use at least {MIN_CHARS} characters or {MIN_WORDS}"
                            " different words, or the passphrase ecf offers")  # fmt: skip
