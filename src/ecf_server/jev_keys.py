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
- **`WAKE`**: `claude_queue.route_new` sets it for a D item; the tick reads it, so new mail waits at
  most one tick for the drain.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
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


def _data(name: str) -> str | None:
    f = resources.files("ecf_server.data").joinpath(name)
    return f.read_text("utf-8") if f.is_file() else None


def pin() -> str:
    """The pinned Jev version from `jev.lock` ("unknown" when the file can't be read)."""
    raw = _data(LOCK)
    doc: Any = json.loads(raw) if raw else {}
    model = cast("dict[str, Any]", doc).get("model") if isinstance(doc, dict) else None
    return model if isinstance(model, str) and _PIN.fullmatch(model) else UNKNOWN


def mapping_digest() -> str | None:
    """The canonical digest of the shipped `jev_mapping.json` (sorted keys, compact), or None when
    this release ships none (step 3 adds it)."""
    raw = _data(MAPPING)
    if raw is None:
        return None
    doc = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


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
