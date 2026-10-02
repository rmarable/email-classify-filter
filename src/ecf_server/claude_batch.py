"""`classifier_high_batch` (SPEC §7.5, §14.2; OD-052; V1.4 step 9): how many emails one
`ecf:classifier-high` spawn takes on a preset C address (the only preset where Claude classifies).

- **Range** 1-5, default 1. Above 1, the spawn reads its emails one after another in one context:
  it saves plan usage, and lets one email's text reach the next one's classification (cross-item
  injection). The items of one spawn form a batch, so the hide guard (§5.6) covers them.
- **Setting:** `ecf settings set classifier_high_batch <1-5> --address <address>`. Raising it needs
  step-up and sends a Security Notice; lowering it needs neither. Kept in the address's overrides.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import addresses, slack_admin, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

KEY = "classifier_high_batch"
DEFAULT, MAX = 1, 5


def size(conn: sqlite3.Connection, address_id: str) -> int:
    row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    overrides: dict[str, Any] = json.loads(row["overrides"]) if row else {}
    return int(overrides.get(KEY, DEFAULT))


def parse(value: str) -> int:
    try:
        n = int(value.strip())
    except ValueError:
        n = 0
    if not DEFAULT <= n <= MAX:
        raise InvalidInputError(f"{KEY}: a whole number from {DEFAULT} to {MAX}")
    return n


def _address(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    a = addresses.get_address(conn, ref)
    if a["preset"] != "C":
        raise InvalidInputError(f"{KEY} is for preset C addresses, where Claude classifies;"
                                f" {a['address_id']} uses preset {a['preset']}")  # fmt: skip
    return a


@stepup.purpose(KEY)
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = _address(conn, str(target.get("address", "")))
    n = parse(str(target.get("value", "")))
    return stepup.Bound(stepup.digest(KEY, a["address_id"], n),
                        f"ecf: let one Claude classifier read {n} of {a['email']}'s emails in"
                        " one context")  # fmt: skip


def set_size(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, ref: str, value: str,
             *, nonce: str | None, actor: str = "os_user") -> dict[str, Any]:  # fmt: skip
    a = _address(conn, ref)
    aid = str(a["address_id"])
    new, old = parse(value), size(conn, aid)
    if new == old:
        raise ConflictError(f"{KEY} is {old} for {aid} already")
    if new > old:  # more emails share one context: more room for cross-item injection
        stepup.consume(conn, clock, KEY, {"address": aid, "value": str(new)}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                           (aid,)).fetchone()  # fmt: skip
        overrides: dict[str, Any] = json.loads(row["overrides"]) | {KEY: new}
        conn.execute("UPDATE addresses SET overrides = ? WHERE address_id = ?",
                     (json.dumps(overrides, sort_keys=True), aid))  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'settings.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps({"key": KEY, "value": new, "from": old})),
        )
    if new > old:
        ident = slack_admin.identity(conn)
        slack_admin.notice(
            conn, clock, notifier,
            f"{KEY} raised from {old} to {new} for {a['email']}: one Claude classifier now reads"
            f" up to {new} of its emails in one context, so one email's text can affect the"
            " next one's classification. Hide actions in such a batch need your approval when"
            " any of its emails looks risky.",
            dms=[ident.member] if ident and ident.member else [],
        )  # fmt: skip
    return {"key": KEY, "value": new, "address_id": aid, "restart": False}
