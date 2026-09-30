"""`ecf backfill <address> --since <date> [--act]` (SPEC §5.1, §10.2; V1.2 step 11b).

ecf's first check starts from now (`start_from: now`). A backfill reads the mail that arrived in
INBOX since a date, up to where that first position was, through the same path as a check: the
same page and time limits, sender authentication, facts, triggers and pre-check decision, and
sender history (a DMARC-passing message counts toward a sender being known).

Records only by default (OD-216): each backfilled item is decided and recorded, then closed as
`observed` through the backfill-only edge `new → observed` (OD-221): nothing is done to the
mailbox, nothing is escalated, and the item never reaches the classifier or "Needs you". With
`--act` the pre-check acts as it would on new mail, within the address's stage (shadow still does
nothing), and escalations post like any others (bursts merge, §10.1).

The backfill runs inside the address's checks, after the page of new mail, while the lease is
held; progress is kept in the settings table (`backfill.<address_id>`), so it survives restarts.
While mail remains, the scheduler treats it like a backlog (catch-up rules: 30 s pauses on AC
power, the time cap and cooldown; `ecf check --until-empty` drives it too). A mailbox reset
(UIDVALIDITY change) ends it; start it again afterwards.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import addresses, jobs, precheck
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.fetch import Backfill, load_cursor
from ecf_server.items import transition
from ecf_server.mail import MailSource
from ecf_server.state_machine import TransitionContext

KEY = "backfill.{}"
CHECK_TIMEOUT_S = 900  # as schedule.py's check jobs


def _key(address_id: str) -> str:
    return KEY.format(address_id)


def load(conn: sqlite3.Connection, address_id: str) -> Backfill | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (_key(address_id),)).fetchone()
    return Backfill(**json.loads(row[0])) if row else None


def save(conn: sqlite3.Connection, clock: Clock, address_id: str, bf: Backfill) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (_key(address_id), json.dumps(bf.to_json()), now),
        )


def finish(
    conn: sqlite3.Connection, clock: Clock, address_id: str, bf: Backfill, outcome: str
) -> None:
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (_key(address_id),))
        _audit(conn, clock, address_id, "backfill.finished", bf.to_json() | {"outcome": outcome})


def _audit(
    conn: sqlite3.Connection, clock: Clock, address_id: str, event: str, data: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, 'service', 'ok', ?)",
        (to_ts(clock.now()), address_id, event, json.dumps(data)),
    )


def start(
    conn: sqlite3.Connection, clock: Clock, ref: str, since: str, *, act: bool
) -> dict[str, Any]:
    try:
        day = date.fromisoformat(since)
    except ValueError:
        raise InvalidInputError("--since is a date like 2026-09-01") from None
    if day > clock.now().date():
        raise InvalidInputError("--since is in the future")
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    cur = load_cursor(conn, aid)
    if cur is None or cur.uidvalidity is None:
        raise ConflictError(f"{aid} hasn't been checked yet: run `ecf check {aid}` first")
    running = load(conn, aid)
    if running is not None:
        raise ConflictError(f"a backfill of {aid} since {running.since} is already running")
    bf = Backfill(day.isoformat(), act, cur.uidvalidity, cur.last_uid)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')",
            (_key(aid), json.dumps(bf.to_json()), to_ts(clock.now())),
        )
        _audit(conn, clock, aid, "backfill.started", bf.to_json())
    queued = conn.execute(
        "SELECT 1 FROM jobs WHERE address_id = ? AND queue = 'fetch'"
        " AND state IN ('queued', 'claimed')",
        (aid,),
    ).fetchone()
    if queued is None:  # start now rather than at the next scheduled check
        jobs.enqueue(conn, clock, jobs.Queue.FETCH, AddressId(aid), {"reason": "backfill"},
                     timeout_s=CHECK_TIMEOUT_S)  # fmt: skip
    return {"address_id": aid, "since": bf.since, "act": act}


def status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in addresses.list_addresses(conn):
        bf = load(conn, a["address_id"])
        if bf is None:
            continue
        found = conn.execute(
            "SELECT count(*) FROM items WHERE address_id = ? AND json_extract(facts,"
            " '$.backfill') = 1",
            (a["address_id"],),
        ).fetchone()[0]
        out.append({"address_id": a["address_id"], "since": bf.since, "act": bf.act,
                    "items": found})  # fmt: skip
    return out


def undecided(
    conn: sqlite3.Connection, address_id: str, bf: Backfill, skip: list[str]
) -> list[str]:
    """Backfilled mail a failed pass left behind in the backfill's range: created but never
    pre-checked, or pre-checked records-only but not yet closed (V1.2 review, 2026-09-30). New mail
    is above `end_uid`; `--act` items stay `new` after the pre-check by design, and deciding them
    again does nothing (the pre-check skips what it has seen)."""
    rows = conn.execute(
        "SELECT stable_id FROM items WHERE address_id = ? AND status = 'new'"
        " AND (prechecked = 0 OR (? AND json_extract(facts, '$.backfill') = 1))"
        " AND json_extract(locator, '$.uidvalidity') = ?"
        " AND json_extract(locator, '$.uid') <= ?",
        (address_id, int(not bf.act), bf.uidvalidity, bf.end_uid),
    ).fetchall()
    return [r[0] for r in rows if r[0] not in skip]


def decide(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    address_id: str,
    stable_ids: list[str],
    bf: Backfill,
    *,
    install: str,
    max_scan_bytes: int,
) -> list[precheck.Outcome]:
    """The pre-check for backfilled items: as for new mail with --act; otherwise decided,
    recorded and closed as `observed` without acting or escalating."""
    with write_tx(conn):
        for sid in stable_ids:
            conn.execute(
                "UPDATE items SET facts = json_set(facts, '$.backfill', 1) WHERE stable_id = ?",
                (sid,),
            )
    if bf.act:
        return precheck.run(conn, clock, src, address_id, stable_ids, install=install,
                            max_scan_bytes=max_scan_bytes)  # fmt: skip
    out = precheck.record_only(conn, clock, stable_ids, "backfill: recorded only (use --act)")
    for sid in stable_ids:
        transition(conn, clock, StableId(sid), Status.OBSERVED,
                   TransitionContext(backfill=True), actor="service")  # fmt: skip
    return out
