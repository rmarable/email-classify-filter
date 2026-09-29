"""The per-address probe (SPEC §13.1, §18): what a mailbox supports, stored in the `probe` table.

Runs when an address is added or its app password changes (and after mail errors, step 13).
Read-only: it logs in, reads capabilities, folder roles and INBOX's permanent flags, and changes
nothing. Two facts can't be read over IMAP without sending mail: whether the provider saves sent
mail itself, and its message size limit (unless it advertises APPENDLIMIT). Those come from the
provider table (SPEC §18) for known providers and are otherwise unknown until V1.5's sending
code can test them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ecf_server.clock import Clock, to_ts
from ecf_server.mail import MailSource


@dataclass(frozen=True)
class Known:
    saves_sent: bool
    max_message_bytes: int
    source: str


# Provider facts from SPEC §18, keyed by IMAP host. Only tested rows belong here.
KNOWN: dict[str, Known] = {
    "imap.purelymail.com": Known(
        saves_sent=False, max_message_bytes=51_200_000, source="provider table (tested 2026-09-28)"
    ),
}
ROLES_NEEDED = {
    "\\Archive": "archive actions will be held for a person (no Archive folder)",
    "\\Junk": "junk actions will be held for a person (no Junk folder)",
    "\\Sent": "no Sent folder: sent copies can't be saved (matters from V1.5)",
    "\\Drafts": "no Drafts folder: draft replies can't be saved (matters from V1.5)",
}


@dataclass
class ProbeResult:
    roles: dict[str, str]  # role flag -> folder name
    custom_keywords: bool
    move: bool
    uidplus: bool
    condstore: bool
    append_limit: int | None
    saves_sent: bool | None
    max_message_bytes: int | None
    max_size_source: str | None
    warnings: list[str] = field(default_factory=list[str])

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def probe(src: MailSource, host: str) -> ProbeResult:
    caps = src.capabilities()
    roles: dict[str, str] = {}
    for f in src.folders():
        for r in sorted(f.roles):
            roles.setdefault(r, f.name)  # the first folder with a role wins
    known = KNOWN.get(host.lower())
    if caps.append_limit is not None:
        max_bytes, source = caps.append_limit, "APPENDLIMIT"
    elif known is not None:
        max_bytes, source = known.max_message_bytes, known.source
    else:
        max_bytes, source = None, None
    result = ProbeResult(
        roles=roles,
        custom_keywords=caps.custom_keywords,
        move=caps.move,
        uidplus=caps.uidplus,
        condstore=caps.condstore,
        append_limit=caps.append_limit,
        saves_sent=known.saves_sent if known else None,
        max_message_bytes=max_bytes,
        max_size_source=source,
    )
    result.warnings = warnings(result)
    return result


def warnings(r: ProbeResult) -> list[str]:
    out: list[str] = []
    if not r.custom_keywords:
        out.append(
            "the server doesn't allow custom keywords: labels can't be stored on messages; "
            "flags and Slack still work"
        )
    out += [msg for role, msg in ROLES_NEEDED.items() if role not in r.roles]
    if not r.move and not r.uidplus:
        out.append(
            "no MOVE or UIDPLUS: moving a message could also expunge others marked deleted; "
            "moves will be held for a person"
        )
    if r.max_message_bytes is None:
        out.append("the provider's message size limit is unknown")
    if r.saves_sent is None:
        out.append("whether the provider saves sent mail is unknown (tested when sending, V1.5)")
    return out


def store(
    conn: sqlite3.Connection, clock: Clock, address_id: str, host: str, r: ProbeResult
) -> None:
    """Write the probe row; call inside the caller's write transaction."""
    caps = {"move": r.move, "uidplus": r.uidplus, "condstore": r.condstore,
            "append_limit": r.append_limit}  # fmt: skip
    conn.execute(
        "INSERT INTO probe (address_id, special_use, permanent_keywords, saves_sent,"
        " max_message_bytes, host, probed_at, capabilities, max_size_source, warnings)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (address_id) DO UPDATE SET special_use = excluded.special_use,"
        " permanent_keywords = excluded.permanent_keywords, saves_sent = excluded.saves_sent,"
        " max_message_bytes = excluded.max_message_bytes, host = excluded.host,"
        " probed_at = excluded.probed_at, capabilities = excluded.capabilities,"
        " max_size_source = excluded.max_size_source, warnings = excluded.warnings",
        (
            address_id,
            json.dumps(r.roles, sort_keys=True),
            int(r.custom_keywords),
            None if r.saves_sent is None else int(r.saves_sent),
            r.max_message_bytes,
            host,
            to_ts(clock.now()),
            json.dumps(caps, sort_keys=True),
            r.max_size_source,
            json.dumps(r.warnings),
        ),
    )


def cap_to_provider(conn: sqlite3.Connection, address_id: str, limit: int) -> int:
    """ecf's size limit, lowered to the provider's receiving limit when a tested provider-table
    row gives a smaller one (SPEC §5.1; OD-196). Only those rows: `APPENDLIMIT` bounds uploading
    into the mailbox, not receiving (Gmail advertises 34 MB and receives about 50 MB), so it
    isn't used (operator decision 2026-09-29, OD-200)."""
    row = conn.execute(
        "SELECT max_message_bytes FROM probe WHERE address_id = ?"
        " AND max_size_source LIKE 'provider table%'",
        (address_id,),
    ).fetchone()
    provider = row["max_message_bytes"] if row is not None else None
    return min(limit, int(provider)) if provider else limit


def load(conn: sqlite3.Connection, address_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM probe WHERE address_id = ?", (address_id,)).fetchone()
    if row is None:
        return None
    return {
        "host": row["host"],
        "probed_at": row["probed_at"],
        "roles": json.loads(row["special_use"]),
        "custom_keywords": bool(row["permanent_keywords"]),
        "saves_sent": None if row["saves_sent"] is None else bool(row["saves_sent"]),
        "max_message_bytes": row["max_message_bytes"],
        "max_size_source": row["max_size_source"],
        "capabilities": json.loads(row["capabilities"]),
        "warnings": json.loads(row["warnings"]),
    }
