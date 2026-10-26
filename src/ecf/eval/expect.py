"""`ecf eval expect`: a generated card's expected rule and safety, from ecf's rules engine on its
labels (eval generate plan decision 8; P2; draft 21, OD-549).

The service runs the starter rules (as `ecf eval run` does) on each built card, as `ecf rules
test` does, with the card's labels standing in for the classifier and its facts over the
computed ones, and returns the rule and the safety flags that outcome implies. A card you
labelled needs all 13 labels; you confirm it again with `ecf eval label` after the rebuild.

A card the judges labelled holds the 11 asked fields: sender_type and priority come from each
judge's answer in the grade files `ecf eval agree` recorded, and the rules run once per
combination. The rule and both flags are written only when every combination gives the same
ones; otherwise the rule is null and the case goes to `ecf eval settle` for the fields that decide
it. A rules error leaves the case undecided. Judged rows whose labels are unchanged since their
confirmation are confirmed again in one atomic write with their `labelled_by`; a row sent to
settle is unconfirmed in the same write.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations, product
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import agree as ag
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import review
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.eval.results import load_result
from ecf.schema import FieldKind, load_schema

UNASKED = label_file.UNASKED
DEFAULT_SAFETY = review.DEFAULT_SAFETY
MAX_VARIANTS = label_file.MAX_VARIANTS
DECIDES = "decides the rule"
LOG = "expect"
AGREEMENT = "agreement"  # agree's log of the judge files it read (`agree.AGREEMENT`)

# the card edits live in review (agree and settle use them too)
write = review.write_expected
write_labels = review.write_labels


def labelled(root: Path) -> set[str]:
    """The cards whose 13 schema fields are all labelled."""
    fields = set(load_schema().fields)
    out: set[str] = set()
    for path in sorted((root / "cases").glob("*.md")):
        card = parse_card(path.read_text(encoding="utf-8"), source=path.name)
        if fields <= set(card.expected.labels):
            out.add(card.id)
    return out


@dataclass
class Plan:
    """What `ecf eval expect` asks the service and how to read its answer: per judged case the
    values of sender_type and priority to combine (`options`), the cases with a field no judge
    answered validly (`missing`), each case's values by judge (`values`, for settle), and the
    judged rows edited by hand since their confirmation (`edited`: run `ecf eval agree` first)."""

    options: dict[str, dict[str, list[Any]]] = field(
        default_factory=dict[str, dict[str, list[Any]]]
    )
    missing: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    values: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict[str, dict[str, dict[str, Any]]]
    )
    edited: list[str] = field(default_factory=list[str])
    waiting: list[str] = field(default_factory=list[str])  # judged rows with an open dispute

    def judged(self) -> bool:
        return bool(self.options or self.missing or self.edited or self.waiting)


def judge_values(root: Path) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    """The judges of the latest agreement entry, and each case's sender_type and priority by
    judge from their grade files; each file must be there with the digest agree recorded."""
    try:
        entries = gen.Work(root).read(AGREEMENT)
    except ValueError as exc:
        raise InvalidInputError(f"work/{AGREEMENT}.jsonl is malformed ({exc}); run `ecf eval"
                                " agree` again") from None  # fmt: skip
    if not entries:
        return [], {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        named = [(str(j["judge"]), str(j["file"]), str(j["sha256"]))
                 for j in entries[-1]["judges"]]  # fmt: skip
    except (KeyError, TypeError) as exc:
        raise InvalidInputError(f"work/{AGREEMENT}.jsonl is malformed ({exc}); run `ecf eval"
                                " agree` again") from None  # fmt: skip
    for judge, name, digest in named:
        path = root / grading.GRADES / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise InvalidInputError(f"{name}: missing or changed since `ecf eval agree`;"
                                    " run `ecf eval agree` again")  # fmt: skip
        for c in load_result(path).cases:
            if c.got:
                out.setdefault(c.id, {})[judge] = {f: c.got.get(f) for f in UNASKED}
    return [j for j, _n, _d in named], out


def plan(root: Path) -> Plan:
    """For each judged card ready for the rules (the 11 asked fields, no open dispute, labels
    unchanged since its confirmation): the values of sender_type and priority to combine. A
    value on the card (your answer) is used alone; otherwise each valid value the row's judges
    gave."""
    spec = load_schema().fields
    asked = {n for n in spec if n not in UNASKED}
    named, values = judge_values(root)
    waiting = {d["id"] for d in ag.open_disputes(root)}
    out = Plan(values=values)
    for row in label_file.read(root):
        cid = row["id"]
        if not label_file.judged(row):
            continue
        if cid in waiting:
            out.waiting.append(cid)
            continue
        lb: dict[str, Any] = row[label_file.LABELLED_BY]
        path = root / "cases" / f"{cid}.md"
        labels = parse_card(path.read_text(encoding="utf-8"), source=path.name).expected.labels
        if label_file.edited(row, labels):
            out.edited.append(cid)  # a hand edit: agree decides it again first
            continue
        listed: list[Any] = lb.get("judges") or []
        judges = [str(j) for j in listed]
        if unknown := [j for j in judges if j not in named]:
            raise InvalidInputError(f"{cid} was labelled by {unknown[0]}, not a judge of the"
                                    " latest `ecf eval agree`; run it again")  # fmt: skip
        if not asked <= set(labels):
            continue
        options: dict[str, list[Any]] = {}
        for f in UNASKED:
            allowed = {True, False} if spec[f].kind is FieldKind.BOOLEAN else set(spec[f].values)
            got = [values.get(cid, {}).get(j, {}).get(f) for j in judges]
            options[f] = [labels[f]] if f in labels else list(dict.fromkeys(
                v for v in got if v in allowed))  # fmt: skip
        if no := [f for f in UNASKED if not options[f]]:
            out.missing[cid] = no
            continue
        if len(combos(options)) > MAX_VARIANTS:
            raise InvalidInputError(f"{cid}: more than {MAX_VARIANTS} combinations (at most three"
                                    " judges, OD-547)")  # fmt: skip
        out.options[cid] = options
    return out


def combos(options: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Every combination of the fields' values, in field order (the service builds the same)."""
    fields = list(options)
    return [dict(zip(fields, t, strict=True)) for t in product(*(options[f] for f in fields))]


def _decide(options: dict[str, list[Any]],
            got: list[dict[str, Any]]) -> tuple[tuple[str, dict[str, bool]] | None, list[str],
                                                str | None]:  # fmt: skip
    """(outcome, deciding fields, error): the outcome when every combination gives the same rule
    and flags; else the fields that decide it (two combinations differing only in it differ).
    A reply that doesn't evaluate exactly these combinations is refused: a service older than
    this ecf ignores them."""
    want = combos(options)
    if sorted(json.dumps(v, sort_keys=True) for v in want) != sorted(
            json.dumps(g.get("labels"), sort_keys=True) for g in got):  # fmt: skip
        raise InvalidInputError("the service didn't evaluate the combinations: restart it so it"
                                " runs this version of ecf")  # fmt: skip
    if bad := next((g.get("error") for g in got if g.get("error")), None):
        return None, [], str(bad)
    labels = [cast(dict[str, Any], g["labels"]) for g in got]
    outs = [(g["rule"], json.dumps(g["safety"], sort_keys=True)) for g in got]
    if len(set(outs)) == 1:
        return (got[0]["rule"], dict(got[0]["safety"])), [], None
    deciding: set[str] = set()
    for (a, oa), (b, ob) in combinations(zip(labels, outs, strict=True), 2):
        diff = [f for f in UNASKED if a.get(f) != b.get(f)]
        if len(diff) == 1 and oa != ob:
            deciding.add(diff[0])
    if not deciding:  # only jointly: ask every field that varies
        deciding = {f for f in UNASKED if len(options.get(f, [])) > 1}
    return None, sorted(deciding), None


def apply(root: Path, cases: list[dict[str, Any]], on: date | None = None, *,
          plan: Plan | None = None, digest: str | None = None) -> dict[str, int]:  # fmt: skip
    """Fill the cards from the service's answer, then rebuild the set. A card you labelled gets
    the base answer; a judged card in `plan` gets the rule and flags only when every combination
    agrees, else `rule: null` and both flags false; a judged case whose combinations differ, or
    with a field no judge answered validly, goes to settle; a rules error leaves it undecided.
    Then judged rows whose labels are unchanged since their confirmation are confirmed again, and
    the rows sent to settle unconfirmed, in one write."""
    gen.verify(root)
    today = on or date.today()
    ready = labelled(root)
    p = plan or Plan()
    held = {*p.edited, *p.waiting}
    counts = {"filled": 0, "unchanged": 0, "unlabelled": 0, "errors": 0, "to_settle": 0,
              "undecided": 0, "edited": len(p.edited), "waiting": len(p.waiting)}  # fmt: skip
    seen: set[str] = set()
    to_settle: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def ask(cid: str, fields: list[str]) -> None:
        counts["to_settle"] += 1
        to_settle.append({"id": cid, "whole": False, "fields": fields, "reason": DECIDES,
                          "judges": {f: {j: v[f] for j, v in p.values.get(cid, {}).items()}
                                     for f in fields}})  # fmt: skip

    for cid, fields in p.missing.items():
        ask(cid, fields)
        write(root / "cases" / f"{cid}.md", None, DEFAULT_SAFETY)
    for c in cases:
        cid = c["id"]
        seen.add(cid)
        path = root / "cases" / f"{cid}.md"
        if cid in p.missing or cid in held:
            continue
        if cid in p.options:
            out, deciding, err = _decide(p.options[cid], list(c.get("variants") or []))
            if out is not None:
                counts["filled" if write(path, *out) else "unchanged"] += 1
                continue
            if err or not deciding:
                counts["undecided"] += 1
                errors.append({"id": cid, "error": (err or "no deciding field")[:200]})
            else:
                ask(cid, deciding)
            write(path, None, DEFAULT_SAFETY)
            continue
        if c.get("error"):
            counts["errors"] += 1
            continue
        if cid not in ready:
            counts["unlabelled"] += 1
            continue
        changed = write(path, c["rule"], dict(c["safety"]))
        counts["filled" if changed else "unchanged"] += 1
    for cid in sorted(set(p.options) - seen):  # the service didn't evaluate it (Y5)
        counts["undecided"] += 1
        errors.append({"id": cid, "error": "not evaluated by the service"})
        write(root / "cases" / f"{cid}.md", None, DEFAULT_SAFETY)
    report = build_all(root)
    if report.findings:
        first = report.findings[0]
        raise InvalidInputError(f"hygiene scan failed after writing the expected outcomes:"
                                f" {first.where}: {first.kind}; nothing more written")  # fmt: skip
    if p.judged():
        _finish(root, to_settle, today, digest, counts, p.edited, errors)
    return counts


def _finish(root: Path, to_settle: list[dict[str, Any]], today: date, digest: str | None,
            counts: dict[str, int], edited: list[str],
            errors: list[dict[str, str]]) -> None:  # fmt: skip
    """After the rebuild: re-confirm or unconfirm the judged rows, then log the disputes and the
    run (`work/expect.jsonl`)."""
    _reconfirm(root, {d["id"] for d in to_settle}, today)
    work = gen.Work(root)
    work.append("disputes", [d | {"on": today.isoformat()} for d in to_settle])
    work.append(LOG, [{"on": today.isoformat(), "rules_digest": digest,
                       "written": counts["filled"] + counts["unchanged"],
                       "to_settle": counts["to_settle"], "undecided": counts["undecided"],
                       "edited": edited, "errors": errors}])  # fmt: skip


def _reconfirm(root: Path, to_settle: set[str], on: date) -> None:
    """One atomic write: a judged row whose labels are unchanged since its confirmation, with no
    open dispute and not sent to settle now, is confirmed again with its `labelled_by`; a row
    sent to settle loses its confirmation. A row edited by hand (labels differ) stays as it is."""
    waiting = {d["id"] for d in ag.open_disputes(root)}
    rows = label_file.read(root)
    for i, r in enumerate(rows):
        lb = r.get(label_file.LABELLED_BY)
        if not lb:
            continue
        if r["id"] in to_settle:
            rows[i] = {k: v for k, v in r.items() if k != "confirmed"}
        elif lb.get("labels_sha256") == label_file.labels_hash(r) and r["id"] not in waiting:
            rows[i] = r | {"confirmed": {"sha256": r["sha256"],
                                         "expected": label_file.expected_hash(r["expected"]),
                                         "on": on.isoformat()}}  # fmt: skip
    label_file.write(root, rows)
