"""Monitored addresses and `org_domains` (SPEC §7.2, §9.4, §10.2, §14.2).

The service is the only writer of mailbox secrets (§11.6): the CLI sends the app password over the
0600 socket, the service checks it by logging in, then stores it as `mailbox/<address_id>`. New
addresses start in shadow with outbound off. Addresses are marked removed, never deleted, because
items refer to them (OD-167); adding a removed address again revives its row.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.ids import SLUG_PATTERN, StableId
from ecf.status import OPEN, Status
from ecf_server import items, probe, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import MailSource
from ecf_server.precheck import payment_or_fraud
from ecf_server.secretstore import SecretStore
from ecf_server.state_machine import TransitionContext

ORG_DOMAINS_KEY = "org_domains"
SENSITIVITIES = ("standard", "high")
PRESETS = ("A", "B", "C")
# Public mailbox providers can't be org domains (SPEC §7.2): anyone can get an address there.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com",
    "fastmail.com", "hey.com", "tutanota.com", "tuta.io", "purelymail.com",
})  # fmt: skip
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9.-]{1,253})$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

MailFactory = Callable[[str, str, Callable[[], str]], MailSource]  # (host, user, password)


def secret_name(address_id: str) -> str:
    return f"mailbox/{address_id}"


def normalize_domain(domain: str) -> str:
    d = domain.strip().rstrip(".").lower()
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidInputError(f"not a domain: {domain!r}") from exc
    if not _HOST.fullmatch(d) or "." not in d or ".." in d:
        raise InvalidInputError(f"not a domain: {domain!r}")
    return d


def parse_email(email: str) -> tuple[str, str]:
    """(local part, normalized domain); the local part keeps its case (SMTP allows it)."""
    m = _EMAIL.fullmatch(email.strip())
    if not m:
        raise InvalidInputError(f"not an email address: {email!r}")
    return email.strip().split("@", 1)[0], normalize_domain(m.group(1))


def default_address_id(email: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", parse_email(email)[0].lower()).strip("-")[:40].strip("-")
    return slug or "address"


def check_org_domains(domains: list[str]) -> list[str]:
    out = sorted({normalize_domain(d) for d in domains})
    if not out:
        raise InvalidInputError("org_domains needs at least one domain")
    public = [d for d in out if d in PUBLIC_DOMAINS]
    if public:
        raise InvalidInputError(
            f"public mailbox domains can't be org domains (anyone can get an address there): "
            f"{', '.join(public)}"
        )
    return out


def get_org_domains(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ORG_DOMAINS_KEY,)).fetchone()
    return list(json.loads(row["value"])) if row else []


@dataclass(frozen=True)
class AddRequest:
    email: str
    imap_host: str
    sensitivity: str
    preset: str
    app_password: str
    address_id: str | None = None
    org_domains: list[str] | None = None


def _check_password(pw: str) -> str:
    if not pw or len(pw) > 512 or any(c in pw for c in "\r\n\0"):
        raise InvalidInputError("the app password is empty, too long or has line breaks")
    return pw


def login_and_probe(factory: MailFactory, host: str, user: str, password: str) -> probe.ProbeResult:
    """Log in and probe the mailbox; raises MailUnavailableError or MailLoginRejectedError."""
    src = factory(host, user, lambda: password)
    try:
        src.inbox()
        return probe.probe(src, host)
    finally:
        src.close()


def add_address(
    conn: sqlite3.Connection,
    clock: Clock,
    secrets: SecretStore,
    factory: MailFactory,
    req: AddRequest,
    *,
    actor: str,
) -> dict[str, Any]:
    _local, domain = parse_email(req.email)
    email = req.email.strip()
    if req.sensitivity not in SENSITIVITIES:
        raise InvalidInputError(f"sensitivity must be one of {', '.join(SENSITIVITIES)}")
    if req.preset not in PRESETS:
        raise InvalidInputError(f"preset must be one of {', '.join(PRESETS)}")
    host = normalize_domain(req.imap_host)
    address_id = req.address_id or default_address_id(email)
    if not re.fullmatch(SLUG_PATTERN, address_id):
        raise InvalidInputError("address id: lowercase letters, digits and hyphens, at most 40")
    password = _check_password(req.app_password)
    org = get_org_domains(conn)
    new_org = check_org_domains(req.org_domains) if req.org_domains is not None else None
    if not org and new_org is None:
        raise InvalidInputError(f"org_domains isn't set; the first address must set it ({domain}?)")
    if org and new_org is not None and new_org != org:
        raise InvalidInputError(
            "org_domains is already set; changing it is security-relevant config "
            "(`ecf config apply`, with step-up)"
        )

    _refuse_duplicates(conn, address_id, email)
    found = login_and_probe(factory, host, email, password)  # network: outside any transaction

    now = to_ts(clock.now())
    secrets.set(secret_name(address_id), password)
    try:
        with write_tx(conn):
            _refuse_duplicates(conn, address_id, email)
            revived = conn.execute(
                "UPDATE addresses SET sensitivity = ?, stage = 'shadow', paused = 0, outbound = 0,"
                " preset = ?, removed_at = NULL WHERE lower(email) = lower(?) AND address_id = ?"
                " AND removed_at IS NOT NULL",
                (req.sensitivity, req.preset, email, address_id),
            ).rowcount
            if not revived:
                conn.execute(
                    "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (address_id, email, req.sensitivity, req.preset, now),
                )
            probe.store(conn, clock, address_id, host, found)
            if not org and new_org is not None:
                _set(conn, ORG_DOMAINS_KEY, new_org, now, actor)
                _audit(conn, now, None, "config.applied", actor, {"key": ORG_DOMAINS_KEY})
            _audit(
                conn,
                now,
                address_id,
                "address.added",
                actor,
                {"sensitivity": req.sensitivity, "preset": req.preset, "revived": bool(revived)},
            )
    except Exception:
        secrets.delete(secret_name(address_id))
        raise
    return get_address(conn, address_id)


def set_app_password(
    conn: sqlite3.Connection,
    clock: Clock,
    secrets: SecretStore,
    factory: MailFactory,
    ref: str,
    password: str,
    *,
    actor: str,
) -> dict[str, Any]:
    a = get_address(conn, ref)
    found = login_and_probe(factory, a["imap_host"], a["email"], _check_password(password))
    secrets.set(secret_name(a["address_id"]), password)
    with write_tx(conn):
        probe.store(conn, clock, a["address_id"], a["imap_host"], found)
        _due_now(conn, clock, a["address_id"], clear_login_failures=True)
        _audit(
            conn,
            to_ts(clock.now()),
            a["address_id"],
            "secret.written",
            actor,
            {"name": secret_name(a["address_id"])},
        )
    return get_address(conn, a["address_id"])


def retry(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    """Make the address due now, even while rejected logins are backing off to hourly."""
    a = get_address(conn, ref)
    with write_tx(conn):
        _due_now(conn, clock, a["address_id"], clear_login_failures=False)
        _audit(conn, to_ts(clock.now()), a["address_id"], "address.retry", actor, {})
    return a


def _due_now(
    conn: sqlite3.Connection, clock: Clock, address_id: str, *, clear_login_failures: bool
) -> None:
    now = to_ts(clock.now())
    conn.execute(
        "INSERT INTO check_state (address_id, next_due_at) VALUES (?, ?)"
        " ON CONFLICT (address_id) DO UPDATE SET next_due_at = excluded.next_due_at",
        (address_id, now),
    )
    if clear_login_failures:
        conn.execute(
            "UPDATE check_state SET login_failures = 0 WHERE address_id = ?", (address_id,)
        )


def remove_address(
    conn: sqlite3.Connection,
    clock: Clock,
    secrets: SecretStore,
    ref: str,
    *,
    actor: str,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Stop watching an address. Its open items are resolved first (`resolved_manual`), with
    step-up when any is a payment or fraud item (OD-218)."""
    a = get_address(conn, ref)
    aid = a["address_id"]
    items_open = _open_items(conn, aid)
    risky = sum(1 for _, pf in items_open if pf)
    if risky:
        stepup.consume(conn, clock, "address_remove", {"address_id": aid}, nonce)
    for stable_id, pf in items_open:
        ctx = TransitionContext(payment_or_fraud=pf, stepup_verified=bool(risky))
        items.transition(conn, clock, StableId(stable_id), Status.RESOLVED_MANUAL, ctx,
                         actor=actor)  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET removed_at = ? WHERE address_id = ?", (now, aid))
        # its alerts end with it, without a "Resolved" notification (V1.1 review, 2026-09-29)
        conn.execute(
            "UPDATE alerts SET resolved_at = ? WHERE address_id = ? AND resolved_at IS NULL",
            (now, aid),
        )
        _audit(conn, now, aid, "address.removed", actor, {"resolved": len(items_open)})
    secrets.delete(secret_name(aid))
    return {
        **a,
        "removed_at": now,
        "resolved": len(items_open),
        "residue": [
            f"revoke the app password for {a['email']} at your mail provider",
            "ecf keywords stay on messages already labelled",
        ],
    }


def _open_items(conn: sqlite3.Connection, address_id: str) -> list[tuple[str, bool]]:
    """(stable_id, payment or fraud) for each open item, in a stable order."""
    rows = conn.execute(
        "SELECT stable_id, facts FROM items WHERE address_id = ?"
        " AND status IN (SELECT value FROM json_each(?)) ORDER BY stable_id",
        (address_id, json.dumps(sorted(OPEN))),
    ).fetchall()
    return [(r["stable_id"], payment_or_fraud(json.loads(r["facts"] or "{}"))) for r in rows]


@stepup.purpose("address_remove")
def _describe_remove(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to the exact set of open items, so an item arriving meanwhile needs a new step-up."""
    a = get_address(conn, str(target.get("address_id", "")))
    open_ = _open_items(conn, a["address_id"])
    risky = sum(1 for _, pf in open_ if pf)
    prompt = (
        f"ecf: stop watching {a['email']} and resolve its {len(open_)} open item(s), "
        f"{risky} of them payment or fraud"
    )
    return stepup.Bound(stepup.digest("address_remove", a["address_id"], open_), prompt)


def list_addresses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"{_SELECT} WHERE a.removed_at IS NULL ORDER BY a.address_id").fetchall()
    return [_row(r) | {"probe": probe.load(conn, r["address_id"])} for r in rows]


def get_address(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    """By address id or email; removed addresses are not found."""
    row = conn.execute(
        f"{_SELECT} WHERE a.removed_at IS NULL AND (a.address_id = ? OR lower(a.email) = lower(?))",
        (ref, ref),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no address {ref!r}")
    return _row(row) | {"probe": probe.load(conn, row["address_id"])}


_SELECT = (
    "SELECT a.address_id, a.email, a.sensitivity, a.stage, a.paused, a.outbound, a.preset,"
    " a.created_at, p.host FROM addresses a LEFT JOIN probe p USING (address_id)"
)


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "address_id": r["address_id"],
        "email": r["email"],
        "sensitivity": r["sensitivity"],
        "stage": r["stage"],
        "paused": bool(r["paused"]),
        "outbound": bool(r["outbound"]),
        "preset": r["preset"],
        "imap_host": r["host"],
        "created_at": r["created_at"],
    }


def _refuse_duplicates(conn: sqlite3.Connection, address_id: str, email: str) -> None:
    for row in conn.execute(
        "SELECT address_id, email, removed_at FROM addresses"
        " WHERE address_id = ? OR lower(email) = lower(?)",
        (address_id, email),
    ):
        revivable = (
            row["removed_at"] is not None
            and row["address_id"] == address_id
            and (row["email"].lower() == email.lower())
        )
        if not revivable:
            raise ConflictError(
                f"{row['email']} already uses id {row['address_id']!r}"
                + (" (removed; add it again with that id)" if row["removed_at"] else "")
            )


def _set(conn: sqlite3.Connection, key: str, value: Any, now: str, actor: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now, actor),
    )


def _audit(
    conn: sqlite3.Connection,
    now: str,
    address_id: str | None,
    event: str,
    actor: str,
    data: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, ?, 'ok', ?)",
        (now, address_id, event, actor, json.dumps(data)),
    )
