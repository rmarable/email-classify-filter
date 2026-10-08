"""`ecf rules test <file>` (SPEC §8.6; V1.2 step 10b): runs proposed rules against the synthetic
set and shows which outcomes change from the rules in force.

Each case is analyzed offline, in a scratch in-memory database, as a message from a first-time
sender to its card's profile (OD-443): `org`, `ap@acme.example` (a `standard` address of the
synthetic set's fictitious org, `org_domains: [acme.example]`, one named staff member in
`org_addresses`), or `freemail`, `pat-lee@freemail.example` (a personal account, no org domains,
two named people in `org_addresses`; the scratch alone treats `freemail.example` as a public
provider, standing in for gmail.com). There is no DNS (so `auth_result` is `none` unless the
case's expected facts say otherwise). The case's expected facts then override the computed ones,
except the internal set's (`sender_origin`, `from_org_address`, `impersonates_internal`), and its
expected labels stand in for the classifier's output. Nothing is written to the live database,
and no mail, Slack or network is touched. Cases whose `.eml` isn't built (large ones, `ecf eval
build`) are skipped and listed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval.cards import INTERNAL_FACTS, PROFILE_TO
from ecf_server import db, internal, precheck, rules
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import Clock
from ecf_server.dnscache import Answer, DnsCache
from ecf_server.facts import PUBLIC_DOMAINS, AddressInfo
from ecf_server.message import parse

FREEMAIL = "freemail.example"  # only the scratch treats it as public (OD-443)


@dataclass(frozen=True)
class Profile:
    address: AddressInfo
    org_domains: tuple[str, ...]
    org_addresses: tuple[internal.OrgAddress, ...]


PROFILES = {
    "org": Profile(AddressInfo("ap", PROFILE_TO["org"], "standard"), ("acme.example",),
                   (internal.OrgAddress("dana-chief@acme.example", "Dana Chief"),)),
    "freemail": Profile(AddressInfo("pat", PROFILE_TO["freemail"], "standard"), (),
                        (internal.OrgAddress(PROFILE_TO["freemail"], "Pat Lee"),
                         internal.OrgAddress("sam-rivera@freemail.example", "Sam Rivera"))),
}  # fmt: skip
ADDRESS = PROFILES["org"].address  # both profiles are `standard`
MAX_CASES = 1000
MAX_INDEX_BYTES = 4 * 1024 * 1024  # labels.jsonl
MAX_CASE_BYTES = 16 * 1024 * 1024  # larger ones are parsed in a child in a check (OD-195)


def _no_dns(name: str, rtype: str) -> Answer:
    del name, rtype
    return Answer("error")


@dataclass(frozen=True)
class Case:
    id: str
    path: Path
    labels: dict[str, Any]
    facts: dict[str, Any]
    expected_rule: str | None
    profile: str = "org"


def load_cases(root: Path) -> tuple[list[Case], list[str]]:
    """The cases listed in `labels.jsonl` under `root`, and the IDs whose file isn't built."""
    root = root.resolve()
    index = root / "labels.jsonl"
    if not index.is_file():
        raise InvalidInputError(f"no labels.jsonl in {root} (the synthetic set's folder)")
    if index.stat().st_size > MAX_INDEX_BYTES:
        raise InvalidInputError(f"labels.jsonl is over {MAX_INDEX_BYTES // 1024 // 1024} MB")
    cases: list[Case] = []
    missing: list[str] = []
    for n, line in enumerate(index.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            path = (root / row["file"]).resolve()
            expected = row["expected"]
            case = Case(str(row["id"]), path, dict(expected.get("labels") or {}),
                        dict(expected.get("facts") or {}), expected.get("rule"),
                        str(row.get("profile") or "org"))  # fmt: skip
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidInputError(f"labels.jsonl line {n}: {exc}") from None
        if case.profile not in PROFILES:
            raise InvalidInputError(f"labels.jsonl line {n}: no profile {case.profile!r}")
        if bad := sorted(INTERNAL_FACTS & case.facts.keys()):
            raise InvalidInputError(f"labels.jsonl line {n}: {', '.join(bad)} can't be set by"
                                    " a case (OD-443)")  # fmt: skip
        if not path.is_relative_to(root):
            raise InvalidInputError(f"labels.jsonl line {n}: {row['file']} is outside {root}")
        if path.is_file():
            cases.append(case)
        else:
            missing.append(case.id)
        if len(cases) + len(missing) > MAX_CASES:
            raise InvalidInputError(f"more than {MAX_CASES} cases")
    return cases, missing


class Scratch:
    """Throwaway databases, one per profile, each holding only its address and internal set."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._conns: dict[str, sqlite3.Connection] = {}

    def _conn(self, name: str) -> sqlite3.Connection:
        if (conn := self._conns.get(name)) is not None:
            return conn
        p = PROFILES[name]
        conn = sqlite3.connect(":memory:", autocommit=True)
        conn.row_factory = sqlite3.Row
        db.migrate(conn)
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES (?, ?, ?, 'A', 'now')",
                     (p.address.address_id, p.address.email, p.address.sensitivity))  # fmt: skip
        conn.executemany(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 'now',"
            " 'ruletest')",
            [("org_domains", json.dumps(list(p.org_domains))),
             (internal.ORG_ADDRESSES_KEY, json.dumps([{"address": e.address, "name": e.name}
                                                      for e in p.org_addresses]))],
        )  # fmt: skip
        self._conns[name] = conn
        return conn

    def facts(self, raw: bytes, profile: str = "org") -> dict[str, Any]:
        conn = self._conn(profile)
        analyzer = MessageAnalyzer(conn, self.clock, PROFILES[profile].address,
                                   DnsCache(conn, self.clock, lookup=_no_dns),
                                   public_domains=PUBLIC_DOMAINS | {FREEMAIL})  # fmt: skip
        return analyzer.analyze(parse(raw), raw)

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()


def _outcome(compiled: rules.CompiledRules, inp: rules.RuleInput) -> dict[str, Any]:
    try:
        d = compiled.evaluate(inp)
    except InvalidInputError as exc:
        return {"rule": None, "actions": [], "actor": False, "error": str(exc)}
    actions = [a.name if a.target is None else f"{a.name}({a.target})" for a in d.actions]
    return {"rule": d.rule_id, "actions": actions, "actor": d.to_actor}


def run(
    clock: Clock, current: rules.CompiledRules, proposed_text: str, root: Path
) -> dict[str, Any]:
    proposed = rules.compile_rules(proposed_text, current.schema, source="proposed rules")
    cases, missing = load_cases(root)
    scratch = Scratch(clock)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.path.stat().st_size > MAX_CASE_BYTES:  # skipped, named (V1.2 review)
                missing.append(case.id)
                continue
            found = scratch.facts(case.path.read_bytes(), case.profile) | case.facts
            inp = rules.RuleInput(case.labels, found, frozenset(precheck.fired(found)),
                                  {"sensitivity": ADDRESS.sensitivity})  # fmt: skip
            before, after = _outcome(current, inp), _outcome(proposed, inp)
            rows.append({
                "id": case.id,
                "expected_rule": case.expected_rule,
                "current": before,
                "proposed": after,
                "changed": before != after,
            })  # fmt: skip
    finally:
        scratch.close()

    def matches(which: str) -> int:
        return sum(1 for r in rows if r["expected_rule"] and r[which]["rule"] == r["expected_rule"])

    return {
        "cases": rows,
        "skipped": missing,
        "changed": sum(1 for r in rows if r["changed"]),
        "with_expected_rule": sum(1 for r in rows if r["expected_rule"]),
        "expected_matched": {"current": matches("current"), "proposed": matches("proposed")},
    }
