"""The analysis run on every message as it is fetched (SPEC §5.1 step 4): sender authentication
(§7.3) and computed facts (§7.2); the deterministic triggers (§8.5) join in step 9.

One `MessageAnalyzer` per check and address: it holds that check's DNS cache (and so its DNS
budget) and the address's settings as they were when the check started.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf_server import facts, senderauth
from ecf_server.addresses import get_org_domains
from ecf_server.clock import Clock, to_ts
from ecf_server.dnscache import DnsCache
from ecf_server.message import ParsedMessage


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

    def analyze(self, parsed: ParsedMessage, raw: bytes) -> dict[str, Any]:
        auth = senderauth.evaluate(raw, parsed, self._dns)
        computed = facts.compute(self._conn, self._address, parsed, auth.result, self._org)
        return auth.facts() | computed

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
