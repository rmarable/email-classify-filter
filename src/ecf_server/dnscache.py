"""DNS for sender authentication (SPEC §7.3): cached in SQLite, bounded in time.

Plain DNS (OD-049: it can be forged on a hostile network; SPEC states the limit). Answers are cached
in the `dns_cache` table, respecting their TTL but capped at the address's check interval; NXDOMAIN
and empty answers are cached too (negative caching). Errors (timeouts, SERVFAIL) are not cached, so
the next check tries again. Each query has a 1.5 s timeout and 3 s lifetime, and a check has a DNS
budget (30 s) after which lookups return `error` without querying, which leads to `none`, never
`pass`. `prefetch` resolves many names in parallel; only the calling thread touches SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, cast

import dns.exception
import dns.rdatatype
import dns.resolver

from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

TIMEOUT_S = 1.5
LIFETIME_S = 3.0
BUDGET_S = 30.0  # per check [proposed in SPEC §7.3]
NEGATIVE_TTL_S = 300
WORKERS = 8
MAX_PREFETCH = 32  # names per prefetch; a message can't make a check query hundreds at once

Status = Literal["ok", "nxdomain", "nodata", "error"]


@dataclass(frozen=True)
class Answer:
    status: Status
    records: tuple[str, ...] = ()
    ttl: int = 0


Lookup = Callable[[str, str], Answer]


def network_lookup(name: str, rtype: str) -> Answer:
    """One query over plain DNS with SPEC §7.3's timeouts."""
    r = dns.resolver.Resolver()
    r.timeout, r.lifetime = TIMEOUT_S, LIFETIME_S
    try:
        ans = r.resolve(name, rtype, raise_on_no_answer=False, search=False)
    except dns.resolver.NXDOMAIN:
        return Answer("nxdomain", ttl=NEGATIVE_TTL_S)
    except (dns.exception.DNSException, OSError):
        return Answer("error")
    if ans.rrset is None:
        return Answer("nodata", ttl=NEGATIVE_TTL_S)
    records: list[str] = []
    for rd in cast("Iterable[Any]", ans.rrset):
        if rd.rdtype == dns.rdatatype.TXT:
            records.append(b"".join(cast("list[bytes]", rd.strings)).decode("utf-8", "replace"))
        else:
            records.append(str(rd.to_text()))
    return Answer("ok", tuple(records), int(ans.rrset.ttl))


class DnsCache:
    """One per check: its budget starts when it is created."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        *,
        cap_s: int = 600,
        budget_s: float = BUDGET_S,
        lookup: Lookup = network_lookup,
    ) -> None:
        self._conn, self._clock, self._cap, self._lookup = conn, clock, cap_s, lookup
        self._deadline = clock.monotonic() + budget_s
        self._memo: dict[tuple[str, str], Answer] = {}
        self.queries = 0  # network queries made (for tests and the check's log line)

    def over_budget(self) -> bool:
        return self._clock.monotonic() > self._deadline

    def remaining(self) -> float:
        """Seconds of the check's DNS budget left (passed to the isolation child)."""
        return max(0.0, self._deadline - self._clock.monotonic())

    def get(self, name: str, rtype: str = "TXT") -> Answer:
        key = (name.lower().rstrip("."), rtype.upper())
        if key in self._memo:
            return self._memo[key]
        cached = self._load(key)
        if cached is not None:
            self._memo[key] = cached
            return cached
        if self.over_budget():
            return Answer("error")
        return self._keep(key, self._query(key))

    def prefetch(self, keys: Iterable[tuple[str, str]]) -> None:
        """Resolve names not yet known, in parallel; results are stored on this thread."""
        todo: list[tuple[str, str]] = []
        for key in sorted({(n.lower().rstrip("."), t.upper()) for n, t in keys}):
            if key in self._memo:
                continue
            cached = self._load(key)
            if cached is not None:
                self._memo[key] = cached
            else:
                todo.append(key)
        todo = todo[:MAX_PREFETCH]  # the rest are looked up one by one, within the budget
        if not todo or self.over_budget():
            return
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="dns") as pool:
            answers = list(pool.map(self._network_in_budget, todo))
        for k, a in zip(todo, answers, strict=True):
            if a is not None:
                self._keep(k, a)

    def _network_in_budget(self, key: tuple[str, str]) -> Answer | None:
        """Each name checks the budget itself, so a long list stops when the budget ends."""
        if self.over_budget():
            return None
        self.queries += 1  # a race here only miscounts the log line
        return self._network(key)

    def _query(self, key: tuple[str, str]) -> Answer:
        self.queries += 1
        return self._network(key)

    def _network(self, key: tuple[str, str]) -> Answer:
        return self._lookup(key[0], key[1])

    def _keep(self, key: tuple[str, str], a: Answer) -> Answer:
        self._memo[key] = a
        if a.status == "error" or (a.status == "ok" and a.ttl == 0):
            return a  # not cached: errors are retried next check; TTL 0 means don't cache
        ttl = max(1, min(a.ttl or NEGATIVE_TTL_S, self._cap))
        expires = to_ts(self._clock.now() + timedelta(seconds=ttl))
        with write_tx(self._conn):
            self._conn.execute(
                "INSERT INTO dns_cache (name, rtype, answer, negative, expires_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (name, rtype) DO UPDATE SET"
                " answer = excluded.answer, negative = excluded.negative,"
                " expires_at = excluded.expires_at",
                (
                    key[0],
                    key[1],
                    json.dumps({"status": a.status, "records": list(a.records)}),
                    int(a.status != "ok"),
                    expires,
                ),
            )
        return a

    def _load(self, key: tuple[str, str]) -> Answer | None:
        row = self._conn.execute(
            "SELECT answer, expires_at FROM dns_cache WHERE name = ? AND rtype = ?", key
        ).fetchone()
        if row is None or row["expires_at"] <= to_ts(self._clock.now()):
            return None
        data = json.loads(row["answer"])
        return Answer(data["status"], tuple(data["records"]))
