"""Pause and resume (SPEC §5.4, §10.1, §13; V1.2 step 8a).

A paused address keeps its pre-check: mail is still fetched, fraud and regulator triggers still
label, flag and escalate (§5.4, "pause never stops the pre-check or fraud flagging"). What waits:
approved actions (the action runner holds their jobs, not counting an attempt), delayed sends that
ran out (they start on resume), and, from V1.3, model checks. Pausing and resuming are instant and
need no step-up (§9.1); they are audited. `ecf pause|resume <address>|--all`, and the Pause and
Resume buttons (per address on digests, for all addresses on "Needs you").

**Consent** (v3.0.0, ADR 0031, P3; operator decision 2026-10-14): an address migration 0038 (or an
import or restore of an older bundle) converted from preset A or B to C was paused, and resuming
it is the consent act (OD-427): the request must say the account's owner agreed to its mail, and
the derived signals, going to Anthropic (`owner_agreed`, which only `ecf resume <address>` sends,
after asking in a terminal). Without it the address stays paused: a single resume is refused, and
`--all` and the Slack buttons skip it. The agreement is recorded once (`CONSENTED`, and an
`address.consent_confirmed` audit row with the address and time only); later resumes are ordinary.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError, NotFoundError
from ecf_server import addresses, restore, slack_in, slack_out
from ecf_server.chat import RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

PAUSE, RESUME = "pause", "resume"
ALL = "*"  # a button's ref for every address (the pinned "Needs you")
CONVERTED = addresses.CONVERTED  # setting, written by 0038: {address_id: old preset} (P3)
CONSENTED = addresses.CONSENTED  # setting: the converted ids whose owner agreed


def is_paused(conn: sqlite3.Connection, address_id: str) -> bool:
    r = conn.execute("SELECT paused FROM addresses WHERE address_id = ?", (address_id,)).fetchone()
    return bool(r and r["paused"])


def paused_addresses(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT address_id FROM addresses WHERE paused = 1 AND removed_at IS NULL"
        " ORDER BY address_id"
    )
    return [r[0] for r in rows]


def _setting(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def needs_consent(conn: sqlite3.Connection, address_id: str) -> bool:
    """A converted address whose owner's agreement hasn't been confirmed yet (P3)."""
    was: dict[str, Any] = _setting(conn, CONVERTED) or {}
    return address_id in was and address_id not in (_setting(conn, CONSENTED) or [])


def awaiting_consent(conn: sqlite3.Connection) -> list[str]:
    """Paused converted addresses, not removed, that only `ecf resume <address>` can resume."""
    return [aid for aid in paused_addresses(conn) if needs_consent(conn, aid)]


def consent_text(address_id: str) -> str:
    return (f"{address_id} was converted to preset C in v3.0.0, so its mail goes to Anthropic once"
            f" resumed: run `ecf resume {address_id}` in a terminal, which asks whether the"
            " account's owner agreed (OD-427), or remove it (ecf address remove"
            f" {address_id})")  # fmt: skip


def set_paused(
    conn: sqlite3.Connection, clock: Clock, ref: str, paused: bool, *, actor: str,
    owner_agreed: bool = False,
) -> list[str]:  # fmt: skip
    """Pause or resume one address (by ID or email), or every address with `ALL`; returns the
    addresses that changed. A converted address resumes only with `owner_agreed` and by its own
    ref (P3)."""
    if ref == ALL:
        targets = [r[0] for r in conn.execute(
            "SELECT address_id FROM addresses WHERE removed_at IS NULL AND paused = ?",
            (0 if paused else 1,))]  # fmt: skip
    else:
        a = addresses.get_address(conn, ref)  # raises NotFoundError
        targets = [a["address_id"]] if a["paused"] != paused else []
    if not paused:  # after a restore, an address waits for a passing mail check (OD-366)
        held = {aid: why for aid in targets if (why := restore.resume_blocked(conn, aid))}
        if held and ref != ALL:
            raise ConflictError(next(iter(held.values())))
        targets = [aid for aid in targets if aid not in held]
        asked = {aid for aid in targets if needs_consent(conn, aid)}
        if asked and (ref == ALL or not owner_agreed):  # consent is typed, one address at a time
            if ref != ALL:
                raise ConflictError(consent_text(next(iter(asked))))
            targets = [aid for aid in targets if aid not in asked]
    else:
        asked = set[str]()
    now = to_ts(clock.now())
    event = "address.paused" if paused else "address.resumed"
    with write_tx(conn):
        for aid in targets:
            conn.execute("UPDATE addresses SET paused = ? WHERE address_id = ?",
                         (int(paused), aid))  # fmt: skip
            if not paused:
                restore.resumed(conn, aid)
            if aid in asked:  # the owner agreed (OD-427): the address and time only
                agreed: list[str] = _setting(conn, CONSENTED) or []
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                    " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                    (CONSENTED, json.dumps(sorted({*agreed, aid})), now, actor))  # fmt: skip
                conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                             " VALUES (?, ?, 'address.consent_confirmed', ?, 'ok', '{}')",
                             (now, aid, actor))  # fmt: skip
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, ?, 'ok', ?)",
                (now, aid, event, actor, json.dumps({"all": ref == ALL})),
            )
    return targets


def describe(changed: list[str], paused: bool, waiting: list[str] | None = None) -> str:
    """What changed; for a resume, also the converted addresses still waiting for their owner's
    agreement (`awaiting_consent`, P3)."""
    later = (f" Still paused until the account's owner agrees (ecf resume <address> asks):"
             f" {', '.join(waiting)}." if waiting and not paused else "")  # fmt: skip
    if not changed:
        return ("Nothing changed." if paused else "Nothing was paused.") + later
    names = ", ".join(changed)
    if paused:
        return (f"Paused {names}. ecf still checks new mail for fraud and regulator mail;"
                " it takes no other action until you resume.")  # fmt: skip
    return f"Resumed {names}." + later


# Slack buttons: Pause and Resume clicks.


@slack_in.handles(PAUSE)
def _pause_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    _click(conn, clock, click, paused=True)


@slack_in.handles(RESUME)
def _resume_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    _click(conn, clock, click, paused=False)


def _click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, *, paused: bool) -> None:
    try:
        changed = set_paused(conn, clock, click.ref, paused, actor=f"slack:{click.user}")
        text = describe(changed, paused, awaiting_consent(conn) if click.ref == ALL else None)
    except (NotFoundError, ConflictError) as exc:  # unknown address; held after a restore
        _tell(conn, clock, click, f"Not done: {exc.detail}")
        raise
    _tell(conn, clock, click, text)


def _tell(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip
