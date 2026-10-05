"""Computed facts (SPEC §7.2): derived by the service, never sent to or taken from a model.

`compute` reads; `record_sender` writes the sender's history and runs inside the item's
transaction, so a crash can't count a message twice or lose it. A sender is identified by the
SHA-256 of its lowercased From address, per monitored address (the `senders` table; exempt from
retention, OD-040). Times are the service's receipt times, not the forgeable Date header.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ecf_server import internal
from ecf_server.clock import from_ts
from ecf_server.message import ParsedMessage

# Public mailbox providers can't be org domains (SPEC §7.2): anyone can get an address there.
# Nor is one a lookalike of another (OD-430): `ymail.com` isn't impersonating `gmail.com`.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com",
    "fastmail.com", "hey.com", "tutanota.com", "tuta.io", "purelymail.com",
})  # fmt: skip
SEEN_COUNT = 3  # OD-043: at least 3 earlier DMARC-pass messages ...
SEEN_SPREAD = timedelta(days=14)  # ... spread over 14 days or more
# Shared platforms send for many unrelated customers, so their senders never count as seen.
# A subdomain of a listed domain counts as listed. Initial list OD-158; finalized 2026-09-29
# (OD-197) from each vendor's sending domains, with sources in SPEC §7.2. Some of these services
# can also send from the customer's own domain, which no list can recognise.
SHARED_PLATFORMS = (
    "adobesign.com",
    "bill.com",
    "docusign.net",
    "echosign.com",
    "freshbooks.com",
    "getpandadoc.com",
    "hellosign.com",
    "intuit.com",
    "pandadoc.email",
    "pandadoc.net",
    "paypal.com",
    "squareup.com",
    "stripe.com",
    "waveapps.com",
    "xero.com",
    "zohoinvoice.com",
)
NO_REPLY = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|mailer-daemon|bounces?)([+._-].*)?$")
BULK_PRECEDENCE = ("bulk", "list", "junk")
DOCUMENT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/rtf",
    "text/rtf",
    "application/vnd.oasis.opendocument",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/zip",
    "application/x-zip-compressed",
)
DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".ppt",
    ".pptx",
    ".zip",
    ".html",
    ".htm",
)


@dataclass(frozen=True)
class AddressInfo:
    address_id: str
    email: str
    sensitivity: str


def sender_hash(addr: str) -> str:
    return hashlib.sha256(addr.strip().lower().encode()).hexdigest()


def domain_of(addr: str | None) -> str | None:
    if not addr or "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].rstrip(".").lower() or None


def in_domains(domain: str | None, domains: list[str] | tuple[str, ...]) -> bool:
    """`domain` equals one of `domains` or is a subdomain of one (OD-193)."""
    return domain is not None and any(domain == d or domain.endswith("." + d) for d in domains)


def compute(
    conn: sqlite3.Connection,
    address: AddressInfo,
    parsed: ParsedMessage,
    auth_result: str,
    org_domains: list[str],
    *,
    payment_keyword: bool = False,
    org_addresses: tuple[internal.OrgAddress, ...] = (),
    gmail_labels: frozenset[str] | None = None,
) -> dict[str, Any]:
    """`gmail_labels` is the message's `X-GM-LABELS` on Gmail, None elsewhere (V1.6)."""
    from_domain = domain_of(parsed.from_addr)
    org_domain = in_domains(from_domain, org_domains)
    # the internal set is org_domains plus exact org_addresses; no Gmail folding (OD-431, OD-432)
    internal_sender = org_domain or internal.exactly_listed(parsed.from_addr, org_addresses)
    history = _history(conn, address.address_id, parsed.from_addr)
    shared = in_domains(from_domain, SHARED_PLATFORMS)
    confirmed = history is not None and history["confirmed_category"] is not None
    by_history = history is not None and _enough_history(history)
    seen = (confirmed or by_history) and not shared
    expected = history["expected_reply_to_domain"] if history else None
    bulk = bulk_signal(parsed)
    first_time = not seen
    unscanned = _unscanned_reasons(parsed, address, payment_keyword, first_time)
    return {
        "from_domain": from_domain,
        # the sender record's key, so a later decision can read a confirmed category (V1.3)
        "sender_hash": sender_hash(parsed.from_addr) if parsed.from_addr else None,
        "sender_origin": "internal" if internal_sender and auth_result == "pass" else "external",
        "from_org_domain": org_domain,
        "from_org_address": internal.listed(parsed.from_addr, org_addresses) is not None,
        "self_sent": self_sent(parsed, address, gmail_labels),
        "sender_seen_before": seen,
        "sender_confirmed": confirmed and not shared,
        # human-verified for rule 1a (`ecf sender set-verified`, OD-065); fraud triggers ignore it
        "sender_verified": history is not None and bool(history["verified_rule1a"]),
        "shared_platform": shared,
        "reply_to_mismatch": reply_to_mismatch(parsed, from_domain, expected),
        "recipient_mismatch": address.email.lower() not in {*parsed.to, *parsed.cc},
        "bulk_signal": bulk,
        # bulk mail corroborates hiding only when authenticated and not first-time (OD-045)
        "bulk_corroborates": bulk and auth_result == "pass" and seen,
        "content_unscanned": bool(unscanned),
        "unscanned_reasons": unscanned,
    }


def record_sender(
    conn: sqlite3.Connection, address_id: str, from_addr: str | None, auth_result: str, now: str
) -> None:
    """Count a DMARC-pass message toward the sender's history (inside the item's transaction)."""
    if auth_result != "pass" or not from_addr:
        return
    conn.execute(
        "INSERT INTO senders (address_id, sender_hash, domain, dmarc_pass_count, first_pass_at,"
        " last_pass_at) VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT (address_id, sender_hash) DO UPDATE"
        " SET dmarc_pass_count = dmarc_pass_count + 1, domain = excluded.domain,"
        " first_pass_at = coalesce(first_pass_at, excluded.first_pass_at),"
        " last_pass_at = excluded.last_pass_at",
        (address_id, sender_hash(from_addr), domain_of(from_addr), now, now),
    )


def self_sent(
    parsed: ParsedMessage, address: AddressInfo, gmail_labels: frozenset[str] | None
) -> bool:
    """Gmail delivers an account's mail to itself unsigned, and labels it `\\Sent`: the mailbox,
    not the message, says it came from the account; no sender can set a label there (OD-446)."""
    return (gmail_labels is not None and "\\Sent" in gmail_labels
            and parsed.from_addr == address.email.lower())  # fmt: skip


def known_vendor_domains(conn: sqlite3.Connection, address_id: str) -> list[str]:
    """Domains of this address's known senders (confirmed, or enough passing history), for the
    lookalike trigger."""
    out: set[str] = set()
    for row in conn.execute(
        "SELECT * FROM senders WHERE address_id = ? AND domain IS NOT NULL", (address_id,)
    ):
        if row["confirmed_category"] is not None or _enough_history(row):
            out.add(row["domain"])
    return sorted(d for d in out if not in_domains(d, SHARED_PLATFORMS))


def reply_to_mismatch(p: ParsedMessage, from_domain: str | None, expected: str | None) -> bool:
    """Any Reply-To address outside the From domain, unless it is the sender's recorded
    expected Reply-To domain (`ecf sender set-reply-to`)."""
    allowed = {d for d in (from_domain, expected) if d}
    return any(domain_of(r) not in allowed for r in p.reply_to)


def bulk_signal(p: ParsedMessage) -> bool:
    h = p.headers
    auto = [v.strip().lower() for v in h.get("auto-submitted", ())]
    precedence = [v.strip().lower() for v in h.get("precedence", ())]
    local = (p.from_addr or "").split("@", 1)[0]
    return_path = [v.strip() for v in h.get("return-path", ())]
    return (
        bool(h.get("list-id") or h.get("list-unsubscribe"))
        or any(a and a != "no" for a in auto)
        or bool(NO_REPLY.match(local))
        or any(v in BULK_PRECEDENCE for v in precedence)
        or bool(h.get("x-autoreply"))
        or any(v in ("<>", "") for v in return_path)
    )


def _unscanned_reasons(
    p: ParsedMessage, address: AddressInfo, payment_keyword: bool, first_time: bool
) -> list[str]:
    reasons: list[str] = []
    if p.hash_version == 0:
        reasons.append("over the size limit")
    if p.any_truncated:
        reasons.append("a text part over the scan limit")
    # unnamed inline parts (logos and images in signatures) aren't attachments a person sent
    real = [a for a in p.attachments if not a.inline or a.name]
    if real and payment_keyword:
        reasons.append("attachments on a payment item")
    if first_time and any(_is_document(a.content_type, a.name) for a in real):
        reasons.append("a document from a first-time sender")
    if real and address.sensitivity == "high":
        reasons.append("an attachment on a high address")
    return reasons


def _is_document(content_type: str, name: str) -> bool:
    return content_type.startswith(DOCUMENT_TYPES) or name.lower().endswith(DOCUMENT_EXTENSIONS)


def _history(
    conn: sqlite3.Connection, address_id: str, from_addr: str | None
) -> sqlite3.Row | None:
    if not from_addr:
        return None
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM senders WHERE address_id = ? AND sender_hash = ?",
        (address_id, sender_hash(from_addr)),
    ).fetchone()
    return row


def _enough_history(row: sqlite3.Row) -> bool:
    if row["dmarc_pass_count"] < SEEN_COUNT or not row["first_pass_at"] or not row["last_pass_at"]:
        return False
    first: datetime = from_ts(row["first_pass_at"])
    last: datetime = from_ts(row["last_pass_at"])
    return last - first >= SEEN_SPREAD
