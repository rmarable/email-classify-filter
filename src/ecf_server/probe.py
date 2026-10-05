"""The per-address probe (SPEC §13.1, §18): what a mailbox supports, stored in the `probe` table.

Runs when an address is added or its app password changes (and after mail errors, step 13).
Read-only: it logs in, reads capabilities, folder roles and INBOX's permanent flags, and changes
nothing. Two facts can't be read over IMAP without sending mail: whether the provider saves sent
mail itself, and its message size limit (unless it advertises APPENDLIMIT). Those come from the
provider table (SPEC §18) for known providers; otherwise ecf learns whether sent mail is saved at
the first send. From V1.5 the probe also checks the address's SMTP server (TLS and login, no send;
OD-324); a failure there is a warning, since outbound is off until you turn it on.

Gmail (V1.6, OD-438, OD-440): Gmail mode is the `X-GM-EXT-1` capability, never the host, and its
provider facts follow it (Gmail saves sent mail itself; tested 2026-10-05). The probe records
whether All Mail is shown over IMAP (archive on Gmail needs it) and compares the messages INBOX
shows with Gmail's own `in:inbox` count: fewer means the IMAP folder size limit setting hides the
rest, which would make ecf think the hidden mail was handled elsewhere. Both are warnings.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ecf.errors import MailUnavailableError
from ecf_server.clock import Clock, to_ts
from ecf_server.mail import MailSource
from ecf_server.mail.smtp import Sender


@dataclass(frozen=True)
class Known:
    saves_sent: bool
    max_message_bytes: int | None  # the receiving limit; None when not known
    source: str


# Provider facts from SPEC §18, keyed by IMAP host. Only tested rows belong here.
KNOWN: dict[str, Known] = {
    "imap.purelymail.com": Known(
        saves_sent=False, max_message_bytes=51_200_000, source="provider table (tested 2026-09-28)"
    ),
}
# Gmail's facts, by capability (OD-438). Its receiving limit isn't known (APPENDLIMIT is the upload
# limit, OD-200), so there is none here.
GMAIL = Known(saves_sent=True, max_message_bytes=None, source="Gmail (tested 2026-10-05)")
GMAIL_NO_ALL_MAIL = (
    "Gmail's All Mail isn't shown over IMAP: archive actions will be held for a person (Gmail"
    " settings, Labels: Show in IMAP for All Mail)"
)
GMAIL_LIMITED = (
    "Gmail shows {shown} of the {found} messages in your inbox over IMAP: its folder size limit"
    " setting hides the rest, and ecf would treat them as handled elsewhere (Gmail settings,"
    " Forwarding and POP/IMAP: Folder size limits, Do not limit)"
)
LIMIT_SLACK = 10
ROLES_NEEDED = {
    "\\Archive": "archive actions will be held for a person (no Archive folder)",
    "\\Junk": "junk actions will be held for a person (no Junk folder)",
    "\\Sent": "no Sent folder: sent copies can't be saved",
    "\\Drafts": "no Drafts folder: draft replies can't be saved",
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
    smtp: dict[str, Any] | None = None  # check_smtp's result; None when not checked
    gmail: bool = False  # X-GM-EXT-1 (OD-438)
    gmail_inbox: tuple[int, int] | None = None  # (shown, found), Gmail only (OD-440)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def probe(src: MailSource, host: str) -> ProbeResult:
    caps = src.capabilities()
    roles: dict[str, str] = {}
    for f in src.folders():
        for r in sorted(f.roles):
            roles.setdefault(r, f.name)  # the first folder with a role wins
    known = GMAIL if caps.gmail else KNOWN.get(host.lower())
    if caps.append_limit is not None:
        max_bytes, source = caps.append_limit, "APPENDLIMIT"
    elif known is not None and known.max_message_bytes is not None:
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
        gmail=caps.gmail,
        gmail_inbox=src.gmail_inbox_counts() if caps.gmail else None,
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
    # Gmail has no Archive folder; its archive uses All Mail (OD-438), warned about below
    out += [msg for role, msg in ROLES_NEEDED.items()
            if role not in r.roles and not (r.gmail and role == "\\Archive")]  # fmt: skip
    if not r.move and not r.uidplus:
        out.append(
            "no MOVE or UIDPLUS: moving a message could also expunge others marked deleted; "
            "moves will be held for a person"
        )
    if r.max_message_bytes is None:
        out.append("the provider's message size limit is unknown")
    if r.saves_sent is None:
        out.append(
            "whether the provider saves sent mail is unknown (ecf learns it at the first send)"
        )
    if r.gmail and "\\All" not in r.roles:
        out.append(GMAIL_NO_ALL_MAIL)
    if inbox_limited(r.gmail_inbox):
        shown, found = r.gmail_inbox or (0, 0)
        out.append(GMAIL_LIMITED.format(shown=shown, found=found))
    if r.smtp is not None and not r.smtp["ok"]:
        out.append(f"SMTP: {r.smtp['error']}; sending won't work until this is fixed")
    return out


def inbox_limited(counts: tuple[int, int] | None) -> bool:
    """Gmail's search finds more inbox mail than INBOX shows: the folder size limit is on. A few
    apart is mail arriving between the two counts (unverified on real Gmail, V1.6 step 9)."""
    return counts is not None and counts[1] - counts[0] > LIMIT_SLACK


def check_smtp(sender: Sender) -> dict[str, Any]:
    """Log in to the SMTP server and quit (no send). The error text names the host and the
    failure, never the password."""
    try:
        info = sender.check()
    except MailUnavailableError as exc:
        return {"ok": False, "error": str(exc.detail)[:200]}
    return {"ok": True, "host": info.host, "port": info.port, "size": info.size,
            "eight_bit": info.eight_bit}  # fmt: skip


def store(
    conn: sqlite3.Connection, clock: Clock, address_id: str, host: str, r: ProbeResult
) -> None:
    """Write the probe row; call inside the caller's write transaction."""
    caps = {"move": r.move, "uidplus": r.uidplus, "condstore": r.condstore,
            "append_limit": r.append_limit, "gmail": r.gmail,
            "gmail_inbox": None if r.gmail_inbox is None else list(r.gmail_inbox)}  # fmt: skip
    conn.execute(
        "INSERT INTO probe (address_id, special_use, permanent_keywords, saves_sent,"
        " max_message_bytes, host, probed_at, capabilities, max_size_source, warnings, smtp)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (address_id) DO UPDATE SET special_use = excluded.special_use,"
        " permanent_keywords = excluded.permanent_keywords, saves_sent = excluded.saves_sent,"
        " max_message_bytes = excluded.max_message_bytes, host = excluded.host,"
        " probed_at = excluded.probed_at, capabilities = excluded.capabilities,"
        " max_size_source = excluded.max_size_source, warnings = excluded.warnings,"
        " smtp = excluded.smtp",
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
            None if r.smtp is None else json.dumps(r.smtp, sort_keys=True),
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


def keywords_stored(conn: sqlite3.Connection, address_id: str) -> bool:
    """The mailbox keeps custom keywords (`\\*` in PERMANENTFLAGS at its last probe). Without a
    probe on record, assume it does (OD-439 only skips labels a probe showed can't be kept)."""
    row = conn.execute("SELECT permanent_keywords FROM probe WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    return row is None or bool(row["permanent_keywords"])


def is_gmail(conn: sqlite3.Connection, address_id: str) -> bool:
    """The address's mailbox was in Gmail mode at its last probe (OD-438)."""
    row = conn.execute("SELECT json_extract(capabilities, '$.gmail') FROM probe"
                       " WHERE address_id = ?", (address_id,)).fetchone()  # fmt: skip
    return bool(row and row[0])


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
        "smtp": None if row["smtp"] is None else json.loads(row["smtp"]),
    }
