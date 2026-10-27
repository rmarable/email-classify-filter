"""`ecf rules test <file>` (SPEC §8.6; V1.2 step 10b): runs proposed rules against the synthetic
set and shows which outcomes change from the rules in force.

Each case is analyzed offline, in a scratch in-memory database, as a message from a first-time
sender to its card's profile (OD-443): `org`, `ap@acme.example` (a `standard` address of the
synthetic set's fictitious org, `org_domains: [acme.example]`, one named staff member in
`org_addresses`), or `freemail`, `pat-lee@freemail.example` (a personal account, no org domains,
two named people in `org_addresses`; the scratch alone treats `freemail.example` as a public
provider, standing in for gmail.com). There is no DNS (so `auth_result` is `none` unless the
case's expected facts say otherwise). The case's expected facts then override the computed ones,
except the internal set's (`sender_origin`, `from_org_address`, `impersonates_internal`), and the
derived `bulk_corroborates` is recomputed from the result (`with_expected`, shared with `ecf eval
run`); its expected labels stand in for the classifier's output, with the operator's hand-set
fixed-item labels (`fixed_labels.jsonl`) and the fixed items its labels imply (`derive_labels`)
added, through the label map (a value schema v3 removed is left unlabelled once its items are
derived, `ecf.schema.unlabel_removed`; v3.0.0 S3, OD-500). The proposed rules are composed
with ecf's fixed rules first (`rules.compose`), as the rules in force are. Nothing is written to
the live database, and no mail, Slack or network is touched. From v3.0.0 S4 the proposed file
is layered as an applied one is (your rules, then the default rules with your same-id rules in
their place and `default_rules_off` left out; a file ending in a rule with no `when` replaces
them), and a layering `ecf config apply` would refuse is refused here too (`check_layers`).
Cases whose `.eml` isn't built (large ones, `ecf eval build`) are skipped and listed. A card
over a `standard` address's limit (16 MB) is read as the service reads such a message
(`read_case`), not skipped.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels
from ecf.eval.cards import DERIVED_FACTS, INTERNAL_FACTS, PROFILE_TO
from ecf.schema import derive_labels, unlabel_removed
from ecf_server import db, fetch, internal, precheck, rules, triggers
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import Clock
from ecf_server.dnscache import Answer, DnsCache
from ecf_server.facts import PUBLIC_DOMAINS, AddressInfo, corroborates
from ecf_server.mail.fake import fetch_part_of, structure_of
from ecf_server.message import ParsedMessage, parse, parse_partial

# how the scratch computes a case's facts; a result records it, and compare warns across a change.
# 2: the fetch's own facts (`from_count`, `thread_root`, ...) and the money and authentication
# triggers recomputed after a card's expected facts (M8 plan R2); 1: before, unrecorded
FACTS_VERSION = 2
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
MAX_CASE_BYTES = 64 * 1024 * 1024  # larger cards are skipped and listed (read whole into memory)


@dataclass(frozen=True)
class Read:
    """A card as the service reads it: the parsed message, what sender authentication sees
    (the whole message, or only the header block when partial) and the facts the fetch adds."""

    parsed: ParsedMessage
    raw: bytes
    extra: dict[str, Any]


def read_case(raw: bytes) -> Read:
    """A card as a `standard` address reads it: whole, or, over that address's limit (16 MB), as
    `fetch._process_partial` does: headers, the attachment list and the start of each text part,
    with `oversized` and `content_unscanned` set and never a DKIM pass (SPEC §5.1). The parts are
    rebuilt from the file as BODYSTRUCTURE and partial fetches would give them (`mail.fake`); the
    IMAP fetch itself isn't exercised."""
    if len(raw) <= fetch.DEFAULT_MAX[ADDRESS.sensitivity]:
        return Read(parse(raw), raw, {})
    parts = structure_of(raw)
    header = fetch_part_of(raw, "HEADER", fetch.HEADER_LIMIT) or b""
    texts = {p.section: fetch_part_of(raw, p.section, fetch.DEFAULT_SCAN) or b""
             for p in [p for p in parts if p.content_type.startswith("text/")]
             [:fetch.MAX_PARTIAL_TEXTS]}  # fmt: skip
    parsed = parse_partial(header, parts, texts, size=len(raw), max_scan_bytes=fetch.DEFAULT_SCAN)
    return Read(parsed, header, {"oversized": True, "content_unscanned": True})


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
    raw: dict[str, Any] | None = None  # the card's own labels, before `stand_in`
    hand: dict[str, bool] | None = None  # its hand-set fixed labels


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
    hand = _hand_set(root)
    for n, line in enumerate(index.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            path = (root / row["file"]).resolve()
            expected = row["expected"]
            raw = dict(expected.get("labels") or {})
            own = hand.get(str(row["id"]))
            case = Case(str(row["id"]), path, stand_in(raw, own),
                        dict(expected.get("facts") or {}), expected.get("rule"),
                        str(row.get("profile") or "org"), raw, own)  # fmt: skip
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidInputError(f"labels.jsonl line {n}: {exc}") from None
        if case.profile not in PROFILES:
            raise InvalidInputError(f"labels.jsonl line {n}: no profile {case.profile!r}")
        if bad := sorted(DERIVED_FACTS & case.facts.keys()):
            raise InvalidInputError(f"labels.jsonl line {n}: {', '.join(bad)} can't be set by"
                                    " a case (computed from the message)")  # fmt: skip
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


def _hand_set(root: Path) -> dict[str, dict[str, bool]]:
    """The operator's hand-set fixed items by case; a stale row's are left out (S2)."""
    try:
        return fixed_labels.current(root)[0]
    except (InvalidInputError, ValueError, KeyError, TypeError, OSError):
        return {}  # a malformed labels.jsonl is reported by load_cases, line by line


def stand_in(labels: dict[str, Any], hand: dict[str, bool] | None) -> dict[str, Any]:
    """A card's labels as the classifier's stand-in: card-stated > hand-set > derived, then the
    label map (as `ecf eval run` loads them, S2 review F4; S3b)."""
    out, _ = unlabel_removed({**(hand or {}), **labels})
    return out | derive_labels(out)


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
                     " VALUES (?, ?, ?, 'C', 'now')",
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
        return self.analyze(read_case(raw), profile)

    def analyze(self, read: Read, profile: str = "org") -> dict[str, Any]:
        """The facts as `fetch._store` records them: the fetch's own, then the analysis's."""
        conn = self._conn(profile)
        analyzer = MessageAnalyzer(conn, self.clock, PROFILES[profile].address,
                                   DnsCache(conn, self.clock, lookup=_no_dns),
                                   public_domains=PUBLIC_DOMAINS | {FREEMAIL})  # fmt: skip
        found = analyzer.analyze(read.parsed, read.raw)
        return fetch.message_facts(read.parsed) | read.extra | found

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()


def with_expected(found: dict[str, Any], expected: dict[str, Any], *,
                  from_addr: str | None) -> dict[str, Any]:  # fmt: skip
    """Computed facts with a case's expected ones over them, the internal set's excepted
    (OD-443), and `bulk_corroborates` recomputed unless the case sets it: it is derived in
    `facts.compute` before any override, so a card's `auth_result: pass` or `sender_seen_before`
    would otherwise never reach the hide path (Q1). Likewise `known_sender_auth_regressed` (from
    the expected `sender_seen_before` and `auth_result`), and the routes are computed again over
    the result (OD-490), so a card's expected facts reach them. So are the fraud triggers and
    `unverified_payment` that read authentication and sender history (`triggers.with_money`, M8
    plan R2), before the routes; `from_addr` is the message's From address, which trigger 6b
    names."""
    out = found | {k: v for k, v in expected.items() if k not in INTERNAL_FACTS}
    if "bulk_corroborates" not in expected:
        out["bulk_corroborates"] = corroborates(bool(out.get("bulk_signal")),
                                                out.get("auth_result"),
                                                bool(out.get("sender_seen_before")))  # fmt: skip
    if "known_sender_auth_regressed" not in expected:
        # live also needs DMARC-pass history; a card's `sender_seen_before` stands in for it
        out["known_sender_auth_regressed"] = (bool(out.get("sender_seen_before"))
                                              and out.get("auth_result") == "fail"
                                              and not out.get("shared_platform"))  # fmt: skip
    out = triggers.with_money(out, from_addr=from_addr)
    return triggers.with_routes(out, public_domains=PUBLIC_DOMAINS | {FREEMAIL})


def _outcome(compiled: rules.CompiledRules, inp: rules.RuleInput) -> dict[str, Any]:
    try:
        d = compiled.evaluate(inp)
    except InvalidInputError as exc:
        return {"rule": None, "actions": [], "actor": False, "error": str(exc)}
    actions = [a.name if a.target is None else f"{a.name}({a.target})" for a in d.actions]
    return {"rule": d.rule_id, "actions": actions, "actor": d.to_actor}


def _row(current: rules.CompiledRules, inp: rules.RuleInput,
         labels: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    got = _outcome(current, inp)
    names = sorted({a.split("(", 1)[0] for a in got["actions"]})
    return {"rule": got["rule"], "actions": names, "labels": labels,
            **({"error": got["error"]} if "error" in got else {})}  # fmt: skip


def outcomes(clock: Clock, current: rules.CompiledRules, root: Path,
             variants: dict[str, list[dict[str, Any]]] | None = None,
             ) -> list[dict[str, Any]]:  # fmt: skip
    """The rules on each built case, as `run` computes `current` (`ecf eval expect`, eval
    generate plan decision 8): its id, rule, action names and the labels that stood in; and for a
    case in `variants`, the same for each combination of labels, merged into the card's own
    labels before they stand in (draft 21)."""
    cases, _missing = load_cases(root)
    scratch = Scratch(clock)
    out: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.path.stat().st_size > MAX_CASE_BYTES:
                continue
            read = read_case(case.path.read_bytes())
            found = with_expected(scratch.analyze(read, case.profile), case.facts,
                                  from_addr=read.parsed.from_addr)  # fmt: skip
            inp = rules.RuleInput(case.labels, found, frozenset(precheck.fired(found)),
                                  {"sensitivity": ADDRESS.sensitivity})  # fmt: skip
            row = _row(current, inp, case.labels) | {"id": case.id}
            if case.id in (variants or {}):
                row["variants"] = [
                    _row(current, rules.RuleInput(labels, inp.facts, inp.triggers, inp.address),
                         labels) | {"variant": v}
                    for v in (variants or {})[case.id]
                    for labels in [stand_in((case.raw or {}) | v, case.hand)]]  # fmt: skip
            out.append(row)
    finally:
        scratch.close()
    return out


def run(
    clock: Clock, current: rules.CompiledRules, proposed_text: str, root: Path
) -> dict[str, Any]:
    # what `ecf config apply` would refuse in its layering, then the composed set (S4, P2, P17)
    rules.check_layers(proposed_text, current.schema, source="proposed rules")
    proposed = rules.compose(current.schema, proposed_text, source="proposed rules")
    cases, missing = load_cases(root)
    scratch = Scratch(clock)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.path.stat().st_size > MAX_CASE_BYTES:  # skipped, named (V1.2 review)
                missing.append(case.id)
                continue
            read = read_case(case.path.read_bytes())
            found = with_expected(scratch.analyze(read, case.profile), case.facts,
                                  from_addr=read.parsed.from_addr)  # fmt: skip
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
