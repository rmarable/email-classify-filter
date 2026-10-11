"""The analysis run on every message as it is fetched (SPEC §5.1 step 4): sender authentication
(§7.3), computed facts (§7.2) and the deterministic triggers (§8.5).

One `MessageAnalyzer` per check and address: it holds that check's DNS cache (and so its DNS
budget) and the address's settings as they were when the check started.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf_server import facts, internal, own_mail, senderauth, triggers
from ecf_server.addresses import get_org_domains
from ecf_server.clock import Clock, to_ts
from ecf_server.dnscache import DnsCache
from ecf_server.fetch import message_id_reused
from ecf_server.message import ParsedMessage, identity_digest


class MessageAnalyzer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        address: facts.AddressInfo,
        dns: DnsCache,
        *,
        public_domains: frozenset[str] = facts.PUBLIC_DOMAINS,
    ) -> None:
        """`public_domains` differs only in the eval scratch (ruletest, OD-443)."""
        self._conn, self._clock, self._address, self._dns = conn, clock, address, dns
        self._org = get_org_domains(conn)
        self._org_addresses = internal.org_addresses(conn)
        self._public = public_domains
        self._providers = watched_providers(conn, public_domains)
        self._vendors = facts.known_vendor_domains(conn, address.address_id)

    @classmethod
    def for_address(
        cls, conn: sqlite3.Connection, clock: Clock, address_id: str, dns: DnsCache
    ) -> MessageAnalyzer:
        row = conn.execute(
            "SELECT email, sensitivity FROM addresses WHERE address_id = ?", (address_id,)
        ).fetchone()
        return cls(
            conn, clock, facts.AddressInfo(address_id, row["email"], row["sensitivity"]), dns
        )

    def analyze(
        self,
        parsed: ParsedMessage,
        raw: bytes,
        auth: senderauth.AuthOutcome | None = None,
        gmail_labels: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """`auth` is given when a child process already checked the message (isolate.py);
        `gmail_labels` is the message's `X-GM-LABELS` on Gmail (V1.6)."""
        if auth is None:
            auth = senderauth.evaluate(raw, parsed, self._dns)
        keywords = triggers.scan(triggers.texts_of(parsed))
        computed = facts.compute(
            self._conn,
            self._address,
            parsed,
            auth.result,
            self._org,
            payment_keyword=bool(keywords["payment"]),
            org_addresses=self._org_addresses,
            gmail_labels=gmail_labels,
            scan_unscanned=tuple(keywords["unscanned"]),
        )
        found = auth.facts() | computed
        found["ecf_mail"] = own_mail.classify(self._conn, parsed, auth.result)
        found["alert_echo"] = own_mail.alert_echo(self._conn, parsed, raw)
        fired = triggers.evaluate(
            parsed,
            keywords,
            found,
            org_domains=self._org,
            known_vendors=self._vendors,
            duplicate_message_id=self._reused(parsed),
            org_addresses=self._org_addresses,
            watched_providers=self._providers,
            public_domains=self._public,
        )
        return triggers.with_routes(found | fired.facts(), public_domains=self._public)

    def _reused(self, parsed: ParsedMessage) -> bool:
        """The Message-ID already belongs to an item with different content or identity
        headers (trigger 5)."""
        return message_id_reused(
            self._conn,
            self._address.address_id,
            parsed.message_id,
            parsed.content_hash,
            identity_digest(parsed),
        )

    def record(
        self, conn: sqlite3.Connection, parsed: ParsedMessage, found: dict[str, Any]
    ) -> None:
        """Count the message toward its sender's history, unless it had a fraud reason, a
        lookalike domain or a weak route reason: a scam must not build the history that softens
        the routes (OD-490). Trigger 2's weak "first-time sender with a payment keyword" still
        counts, or a new vendor's invoices would never make it known (operator decision
        2026-10-10); so does a route whose only signal is that the sender has no history, or a
        genuine sender sending such requests would never become known (operator decision
        2026-10-10, pre-gate review G1). `record_sender` counts only DMARC-pass mail."""
        t: dict[str, Any] = found.get("triggers") or {}
        routed: dict[str, Any] = found.get("routes") or {}
        reasons: list[str] = t.get("fraud") or []
        fraud = [r for r in reasons if r not in triggers.HISTORY_ONLY]
        if fraud or t.get("lookalikes") or routed.get("fraud_weak"):
            return
        facts.record_sender(
            conn,
            self._address.address_id,
            parsed.from_addr,
            found["auth_result"],
            to_ts(self._clock.now()),
            request=bool(reasons),  # all of them requests from a sender with no history
        )


def watched_providers(
    conn: sqlite3.Connection, public_domains: frozenset[str] = facts.PUBLIC_DOMAINS
) -> tuple[str, ...]:
    """Public provider domains this install watches an address at: lookalike targets (OD-434)."""
    rows = conn.execute("SELECT email FROM addresses WHERE removed_at IS NULL").fetchall()
    domains = {facts.domain_of(str(r[0])) for r in rows}
    return tuple(sorted(d for d in domains if d in public_domains))
