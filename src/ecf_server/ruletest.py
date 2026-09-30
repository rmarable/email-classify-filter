"""`ecf rules test <file>` (SPEC §8.6; V1.2 step 10b): runs proposed rules against the synthetic
set and shows which outcomes change from the rules in force.

Each case is analyzed offline, in a scratch in-memory database, as a message to `ap@acme.example`
(a `standard` address of the synthetic set's fictitious org, `org_domains: [acme.example]`) from a
first-time sender, with no DNS (so `auth_result` is `none` unless the case's expected facts say
otherwise). The case's expected facts then override the computed ones, and its expected labels
stand in for the classifier's output. Nothing is written to the live database, and no mail, Slack
or network is touched. Cases whose `.eml` isn't built (large ones, `ecf eval build`) are skipped
and listed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.schema import load_schema_v1
from ecf_server import db, precheck, rules
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import Clock
from ecf_server.dnscache import Answer, DnsCache
from ecf_server.facts import AddressInfo
from ecf_server.message import parse
from ecf_server.settings import MAX_MB, MB

ADDRESS = AddressInfo("ap", "ap@acme.example", "standard")
ORG_DOMAINS = ["acme.example"]
MAX_CASES = 1000


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


def load_cases(root: Path) -> tuple[list[Case], list[str]]:
    """The cases listed in `labels.jsonl` under `root`, and the IDs whose file isn't built."""
    root = root.resolve()
    index = root / "labels.jsonl"
    if not index.is_file():
        raise InvalidInputError(f"no labels.jsonl in {root} (the synthetic set's folder)")
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
                        dict(expected.get("facts") or {}), expected.get("rule"))  # fmt: skip
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidInputError(f"labels.jsonl line {n}: {exc}") from None
        if not path.is_relative_to(root):
            raise InvalidInputError(f"labels.jsonl line {n}: {row['file']} is outside {root}")
        if path.is_file():
            cases.append(case)
        else:
            missing.append(case.id)
        if len(cases) + len(missing) > MAX_CASES:
            raise InvalidInputError(f"more than {MAX_CASES} cases")
    return cases, missing


class _Scratch:
    """A throwaway database holding only the synthetic address and its org domains."""

    def __init__(self, clock: Clock) -> None:
        conn = sqlite3.connect(":memory:", autocommit=True)
        conn.row_factory = sqlite3.Row
        db.migrate(conn)
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES (?, ?, ?, 'A', 'now')",
                     (ADDRESS.address_id, ADDRESS.email, ADDRESS.sensitivity))  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('org_domains', ?, 'now', 'ruletest')",
                     (json.dumps(ORG_DOMAINS),))  # fmt: skip
        self.conn, self.clock = conn, clock

    def facts(self, raw: bytes) -> dict[str, Any]:
        analyzer = MessageAnalyzer(self.conn, self.clock, ADDRESS,
                                   DnsCache(self.conn, self.clock, lookup=_no_dns))  # fmt: skip
        return analyzer.analyze(parse(raw), raw)

    def close(self) -> None:
        self.conn.close()


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
    proposed = rules.compile_rules(proposed_text, load_schema_v1(), source="proposed rules")
    cases, missing = load_cases(root)
    scratch = _Scratch(clock)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.path.stat().st_size > MAX_MB * MB:
                missing.append(case.id)
                continue
            found = scratch.facts(case.path.read_bytes()) | case.facts
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
