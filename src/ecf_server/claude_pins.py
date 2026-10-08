"""The pinned Claude models and what an address's go-live gate is bound to (SPEC §7.5, §9.3;
OD-014, OD-273, OD-461; V1.4 step 2).

- **`data/models.lock`** names the Claude model for each role: the main session, `classifier`,
  `classifier_high` and `actor` (Sonnet), `actor_high` (Opus); no Haiku from v1.0.0 (OD-461). It
  ships in the wheel, like `ollama.lock`; only an ecf release changes it. Its `lifecycle` records
  each pinned ID's state and retirement date from Anthropic's deprecations page (V1.4 step 10;
  OD-299), which the weekly model watch (`model_watch.py`) announces and the CI canary keeps
  current.
- **Override** (`ecf settings set claude_model_override <id>|none`; operator decision 2026-10-02):
  an ID replaces every pin in its family (a `claude-sonnet-*` ID replaces the Sonnet pins), one
  override per family; `none` clears them all. An ID whose family ecf doesn't pin is refused. It
  needs step-up and sends a Security Notice, and changes the pins of every B and C address, which
  drop to assist until their gate passes again (§7.5).
- **Pins per address** (operator decision 2026-10-02): everything its preset uses, whatever its
  sensitivity. A: the Ollama digest; B: the digest and both actors; C: both classifiers and both
  actors. The main session classifies nothing, so it isn't a gate pin.
- **Key**: what reviews, eval runs and the gate row are matched on. For A it is the digest itself
  (so records made before V1.4 still count); otherwise a short hash of the pins.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import addresses, ollama, slack_admin, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

ROLES = ("main_session", "classifier", "classifier_high", "actor", "actor_high")
GATE_ROLES = {"A": (), "B": ("actor", "actor_high"),
              "C": ("classifier", "classifier_high", "actor", "actor_high")}  # fmt: skip
LOCAL_PRESETS = frozenset({"A", "B"})  # the gate binds the Ollama digest too
PAIR = {"A": "gemma4-12b/local", "B": "gemma4-12b/claude", "C": "claude/claude"}  # gate.pair_key
OVERRIDE_KEY = "claude_model_override"  # settings row: {family: id}
_ID = re.compile(r"claude-(haiku|sonnet|opus)-[0-9][a-z0-9-]{0,40}")


def family(model_id: str) -> str | None:
    m = _ID.fullmatch(model_id)
    return m.group(1) if m else None


def load_lock() -> dict[str, str]:
    """The role -> model ID map from `models.lock`."""
    raw = json.loads(resources.files("ecf_server.data").joinpath("models.lock").read_text("utf-8"))
    lock = {r: str(raw[r]) for r in ROLES}
    bad = [i for i in lock.values() if family(i) is None]
    if bad:
        raise ValueError(f"models.lock: not a Claude model ID: {', '.join(bad)}")
    return lock


@dataclass(frozen=True)
class Lifecycle:
    """One pinned ID's state on Anthropic's deprecations page when the release was made (§7.6,
    OD-299): `retires` is a firm date (deprecated); `not_sooner_than` the tentative one."""

    state: str  # Active | Legacy | Deprecated
    retires: date | None = None
    not_sooner_than: date | None = None
    replacement: str | None = None


def lifecycle() -> dict[str, Lifecycle]:
    """`models.lock`'s `lifecycle`; every pinned ID has an entry."""
    raw = json.loads(resources.files("ecf_server.data").joinpath("models.lock").read_text("utf-8"))
    out: dict[str, Lifecycle] = {}
    for mid, e in dict(raw.get("lifecycle", {})).items():
        day = {k: date.fromisoformat(e[k]) if e.get(k) else None
               for k in ("retires", "not_sooner_than")}  # fmt: skip
        out[str(mid)] = Lifecycle(str(e["state"]), day["retires"], day["not_sooner_than"],
                                  e.get("replacement"))  # fmt: skip
    missing = sorted(set(load_lock().values()) - set(out))
    if missing:
        raise ValueError(f"models.lock: no lifecycle for {', '.join(missing)}")
    return out


def overrides(conn: sqlite3.Connection) -> dict[str, str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (OVERRIDE_KEY,)).fetchone()
    return dict(json.loads(row[0])) if row else {}


def effective(conn: sqlite3.Connection) -> dict[str, str]:
    """The pins in force: `models.lock` with any family override applied."""
    over = overrides(conn)
    return {r: over.get(family(i) or "", i) for r, i in load_lock().items()}


def in_use(conn: sqlite3.Connection) -> bool:
    """Whether any address uses Claude (preset B or C)."""
    return conn.execute("SELECT 1 FROM addresses WHERE removed_at IS NULL AND preset IN"
                        " ('B', 'C') LIMIT 1").fetchone() is not None  # fmt: skip


def pins(conn: sqlite3.Connection, preset: str) -> dict[str, str]:
    eff = effective(conn)
    out = {r: eff[r] for r in GATE_ROLES[preset]}
    if preset in LOCAL_PRESETS:
        out["digest"] = ollama.load_pin().digest
    return out


def key(p: dict[str, str]) -> str:
    if set(p) == {"digest"}:
        return p["digest"]
    return "pins-" + stepup.digest(sorted(p.items()))[:16]


def for_address(conn: sqlite3.Connection, address_id: str) -> tuple[str, dict[str, str]]:
    """(preset, pins) for this address now."""
    row = conn.execute("SELECT preset FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    preset = str(row["preset"]) if row else "A"
    return preset, pins(conn, preset)


def address_key(conn: sqlite3.Connection, address_id: str) -> str:
    return key(for_address(conn, address_id)[1])


def item_key(pinned_models: str | None) -> str | None:
    """The key an item was recorded under (`pin_key` from V1.4; the digest before)."""
    p: dict[str, Any] = json.loads(pinned_models or "{}")
    k = p.get("pin_key", p.get("digest"))
    return str(k) if k is not None else None


# ---------------------------------------------------------------------------- the override


def _proposed(conn: sqlite3.Connection, value: str) -> dict[str, str]:
    """The override map after `value` (an ID, or `none`), validated."""
    v = value.strip()
    if v.lower() == "none":
        return {}
    fam = family(v)
    if fam is None:
        raise InvalidInputError("a Claude model ID like claude-sonnet-5-5 (haiku, sonnet or opus"
                                " family), or none")  # fmt: skip
    pinned = {i for i in load_lock().values() if family(i) == fam}
    if not pinned:
        raise InvalidInputError(f"ecf pins no {fam} model, so there's nothing for {v} to replace")
    over = overrides(conn)
    if v in pinned:
        over.pop(fam, None)  # back to the release's pin for this family
    else:
        over[fam] = v
    return over


@stepup.purpose("claude_model_override")
def _describe_override(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    new = _proposed(conn, str(target.get("value", "")))
    what = ", ".join(f"{f} -> {i}" for f, i in sorted(new.items())) or "the pinned models"
    return stepup.Bound(stepup.digest("claude_model_override", overrides(conn), new),
                        f"ecf: run Claude reviews with {what}; live B and C addresses go back"
                        " to assist")  # fmt: skip


def set_override(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, value: str, *,
                 nonce: str | None, actor: str = "os_user") -> dict[str, Any]:  # fmt: skip
    old, new = overrides(conn), _proposed(conn, value)
    if new == old:
        raise ConflictError("that is the override already in force")
    stepup.consume(conn, clock, "claude_model_override", {"value": value.strip()}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        if new:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (OVERRIDE_KEY, json.dumps(new, sort_keys=True), now, actor),
            )
        else:
            conn.execute("DELETE FROM settings WHERE key = ?", (OVERRIDE_KEY,))
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'models.override_changed', ?, 'ok', ?)",
            (now, actor, json.dumps({"from": old, "to": new})),
        )
    affected = [a["email"] for a in addresses.list_addresses(conn) if a["preset"] in ("B", "C")]
    what = ", ".join(f"{f} -> {i}" for f, i in sorted(new.items()))
    text = (f"Claude model override: {what}." if new else
            "Claude model override cleared: the release's pinned models apply again.")  # fmt: skip
    if affected:
        text += (" The go-live gate of these addresses starts again with the new models, and any"
                 f" that are live go back to assist: {', '.join(affected)}.")  # fmt: skip
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, clock, notifier, text,
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip
    return {"overrides": new, "effective": effective(conn), "affected": affected}


def show(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"lock": load_lock(), "overrides": overrides(conn), "effective": effective(conn)}
