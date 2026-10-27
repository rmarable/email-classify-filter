"""What preset D's readers need without importing `jev` (v3.0.0 step 4; OD-509; plan
planning-docs/v3.0.0-STEP4.md §2.2, P6).

Most of the service (cards, gate, claude_pins, settings, importer, stages, ...) is in the
classifier's import closure, and the classifier never imports `jev` (an import-linter contract).
`jev_dispatch`, imported only by `service.py` and `api.py`, computes D's gate key and prompt digest
from `jev.py` and stores them in the `jev.keys` setting; these modules read them here, with the
pin from `jev.lock` and the digest of step 3's `jev_mapping.json` when the release ships one.

- **`jev.keys`** (written by `jev_dispatch.refresh_keys`, at service start and each tick):
  `{pin, gate_key, prompt_digest, schema, rules}`, for the effective schema and rules digests
  named. Absent before the service first ran this version: D's pin key then holds "unknown", so
  no D gate passes on it.
- **`jev.state`** (one writer: the tick, `jev_dispatch.tick`): Jev up or down for the install,
  with the reason and the probe's ladder (OD-510).
- **The consent marker** (P9): `addresses.overrides.jev_consent_at`, set when the account's owner
  agreed to their mail going to TypeSafe (address add, or `ecf resume` of a D address without
  one); import and restore clear it.
- **`WAKE`**: `claude_queue.route_new` (and a D reprocess) sets it; the service's timer looks at it
  every second or so between ticks (`jev_dispatch.wake`), so new mail starts the drain at once.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any, cast

PRESET = "D"
KEY_SECRET = "jev-api-key"  # noqa: S105 - the secret store's entry name (jev.KEY_SECRET)
HOST = "api.typesafe.ai"
LOCK = "jev.lock"
MAPPING = "jev_mapping.json"  # step 3's mapping; absent until it ships
KEYS = "jev.keys"
STATE = "jev.state"
CONSENT = "jev_consent_at"
UNKNOWN = "unknown"
_PIN = re.compile(r"^jev-(\d+\.\d+\.\d+)$")
WAKE = threading.Event()


@cache
def _data(name: str) -> str | None:
    """A shipped data file, read once: it changes only with an upgrade, which restarts us."""
    f = resources.files("ecf_server.data").joinpath(name)
    return f.read_text("utf-8") if f.is_file() else None


def is_version(model: str) -> bool:
    """A versioned Jev ID (`jev-1.14.0`), never an alias."""
    return bool(_PIN.fullmatch(model))


def pin() -> str:
    """The pinned Jev version from `jev.lock` ("unknown" when the file can't be read)."""
    raw = _data(LOCK)
    doc: Any = json.loads(raw) if raw else {}
    model = cast("dict[str, Any]", doc).get("model") if isinstance(doc, dict) else None
    return model if isinstance(model, str) and _PIN.fullmatch(model) else UNKNOWN


def mapping_digest() -> str | None:
    """The digest of the shipped `jev_mapping.json` as Jev's gate key has it
    (`jev_mapping.digest`: the parsed mapping, canonical), or None when this release ships none."""
    if _data(MAPPING) is None:
        return None
    from ecf_server import jev_mapping  # noqa: PLC0415 - jev_mapping imports policy, which reads us

    return jev_mapping.digest()


def display_name(model_id: str | None) -> str | None:
    """`jev-1.13.0` as a person reads it ("TypeSafe Jev 1.13.0"); None for any other ID."""
    m = _PIN.fullmatch(model_id or "")
    return f"TypeSafe Jev {m.group(1)}" if m else None


def _setting(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    doc: Any = json.loads(row[0]) if row else {}
    return cast("dict[str, Any]", doc) if isinstance(doc, dict) else {}


def stored(conn: sqlite3.Connection) -> dict[str, Any]:
    """`jev.keys`: D's gate key and prompt digest as the service last computed them ({} before)."""
    return _setting(conn, KEYS)


def state(conn: sqlite3.Connection) -> dict[str, Any]:
    """`jev.state`: up or down, why, since when ({} before Jev was first used: up)."""
    return _setting(conn, STATE)


def consented(overrides: str | None) -> bool:
    """Whether an address's `overrides` hold the owner's agreement to TypeSafe (P9)."""
    doc: Any = json.loads(overrides or "{}")
    return isinstance(doc, dict) and bool(cast("dict[str, Any]", doc).get(CONSENT))


def has_key(get: Callable[[str], str | None]) -> bool:
    """Whether the TypeSafe key is stored, by its secret's name (`get`: a secret store's `get`);
    a locked store reads as no key."""
    try:
        return bool(get(KEY_SECRET))
    except Exception:  # a locked or missing store
        return False
