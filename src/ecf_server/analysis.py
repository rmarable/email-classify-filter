"""The analysis run on every message as it is fetched (SPEC §5.1 step 4): sender authentication
(§7.3), computed facts (§7.2) and the deterministic triggers (§8.5).

One `MessageAnalyzer` per check and address: it holds that check's DNS cache (and so its DNS
budget) and the address's settings as they were when the check started.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf_server import facts, own_mail, senderauth, triggers
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
    ) -> None:
        self._conn, self._clock, self._address, self._dns = conn, clock, address, dns
        self._org = get_org_domains(conn)
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
        self, parsed: ParsedMessage, raw: bytes, auth: senderauth.AuthOutcome | None = None
    ) -> dict[str, Any]:
        """`auth` is given when a child process already checked the message (isolate.py)."""
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
        )
        found = auth.facts() | computed
        found["ecf_mail"] = own_mail.classify(self._conn, parsed, auth.result)
        fired = triggers.evaluate(
            parsed,
            keywords,
            found,
            org_domains=self._org,
            known_vendors=self._vendors,
            duplicate_message_id=self._reused(parsed),
        )
        return found | fired.facts()

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
        facts.record_sender(
            conn,
            self._address.address_id,
            parsed.from_addr,
            found["auth_result"],
            to_ts(self._clock.now()),
        )
