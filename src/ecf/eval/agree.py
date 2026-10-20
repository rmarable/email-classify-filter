"""`ecf eval agree`: the judges set a generated set's labels; the operator settles only where they
disagree (eval generate plan, Judges; OD-547, Phase R J1-J20).

The judges are the set's grade files (`grades/*.json`), one per model, every judge a different
model and, in PROD, every pair of generator, seed writer and judges a different family. Per case
and field: a boolean or `fraud_risk` counts only when every judge gave the same valid value;
`category` and `sender_type` take a majority when three or more judges answered; `priority` is
report-only (too noisy even within one model) and never set. A case any judge refused, left out
or answered with a missing field goes to the operator whole. Agreed values are written into the
cards at once, the set rebuilt once, and fully agreed cases confirmed in one atomic write with
`by: "models"` (`labels.confirm_many`); the rest go to `work/disputes.jsonl` with the judges'
closed values, for `ecf eval settle`. `work/agreement.jsonl` records each judge file's digest so
the report can recompute the decision. Cases already confirmed are left alone, so a second run
never overwrites a value the operator settled.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import models, review
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.eval.results import ResultFile, load_result
from ecf.schema import FieldKind, load_schema

MAJORITY_FIELDS = ("category", "sender_type")
REPORT_ONLY = ("priority",)
DISPUTES = "disputes"
AGREEMENT = "agreement"


@dataclass
class Outcome:
    agreed: int = 0  # cases every counted field agreed on, confirmed by the judges
    disputed: int = 0  # cases with at least one field for the operator
    whole: int = 0  # cases sent whole (a judge refused, left it out or a field missing)
    skipped: int = 0  # already confirmed: left alone
    fields: Counter[str] = field(default_factory=Counter[str])  # disputed fields


def unanimous_fields() -> tuple[str, ...]:
    """Fields every judge must agree on: the booleans and `fraud_risk` (J5)."""
    spec = load_schema().fields
    return tuple(n for n, f in spec.items()
                 if f.kind is FieldKind.BOOLEAN or n == "fraud_risk")  # fmt: skip


def judges(root: Path, doc: dict[str, Any]) -> list[tuple[Path, ResultFile]]:
    """The set's grade files, checked: each made on this set, no model twice, and the role rules
    (J9, J11)."""
    found = [(p, load_result(p)) for p in sorted((root / grading.GRADES).glob("*.json"))]
    if len(found) < 2:
        raise InvalidInputError(f"{root}: agreement needs at least two judges' grade files in"
                                f" {grading.GRADES}/ (`ecf eval grade --grader ...`)")  # fmt: skip
    seen: set[str] = set()
    for p, r in found:
        grading.check_grade(r, doc)
        model = _judge(r)[1]
        if model in seen:
            raise InvalidInputError(f"{p.name}: a second grade by {model}; keep one per judge")
        seen.add(model)
    check_roles(doc, [_judge(r) for _p, r in found])
    return found


def _judge(r: ResultFile) -> tuple[str, str]:
    d = cast(dict[str, Any], (r.summary or {}).get("grader") or {})
    return str(d.get("provider")), str(d.get("model"))


def check_roles(doc: dict[str, Any], judge_ids: list[tuple[str, str]]) -> None:
    """No judge is the generator or the seed writer; in PROD every pair of generator, seed
    writer and judges comes from a different family (J11)."""
    mode = str(doc.get("mode"))
    g = cast(dict[str, Any], doc["generator"])
    writers = [(str(g.get("provider")), str(g.get("model")))]
    if shared := cast(dict[str, Any] | None, doc.get("seeds_from")):
        provider, _, model = str(shared.get("generator")).partition(":")
        writers.append((provider, model))
    for j in judge_ids:
        for w in writers:
            models.check_not_self(w[1], j[1], mode)
    if mode == "prod":
        for a, b in combinations([*judge_ids, *writers[1:]], 2):
            models.check_pair(a, b)
        for j in judge_ids:  # a judge never shares the classifier's family (Claude)
            if models.family(*j) == "anthropic":
                raise InvalidInputError(f"{j[0]}:{j[1]} is Claude, the classifier's family: a"
                                        " judge can't be")  # fmt: skip


def decide(values: list[Any], name: str, unanimous: tuple[str, ...],
           allowed: set[Any]) -> tuple[bool, Any]:  # fmt: skip
    """(settled, value) for one field from every judge's answer."""
    if any(v is None or v not in allowed for v in values):
        return False, None
    top, n = Counter(values).most_common(1)[0]
    if n == len(values):
        return True, top
    if (
        name in MAJORITY_FIELDS
        and name not in unanimous
        and len(values) >= 3
        and n * 2 > len(values)
    ):
        return True, top
    return False, None


def run(root: Path, on: date) -> Outcome:  # noqa: PLR0915 - one pass over the cases
    doc = gen.verify(root)
    found = judges(root, doc)
    spec = load_schema().fields
    unanimous = unanimous_fields()
    answers = [{c.id: dict(c.got) for c in r.cases} for _p, r in found]
    names = [_judge(r) for _p, r in found]
    ok = label_file.models_ok(root)
    rows = label_file.read(root)
    out = Outcome()
    agreed_ids: list[str] = []
    disputes: list[dict[str, Any]] = []
    writes: dict[Path, dict[str, Any]] = {}
    for row in rows:
        cid = row["id"]
        if label_file.is_confirmed(row, models_ok=ok):
            out.skipped += 1
            continue
        got = [a.get(cid) for a in answers]
        if any(not g for g in got):  # refused, too large or left out by a judge
            out.whole += 1
            out.disputed += 1
            disputes.append(
                {"id": cid, "whole": True, "fields": [n for n in spec if n not in REPORT_ONLY]}
            )
            continue
        labels: dict[str, Any] = {}
        open_fields: dict[str, list[Any]] = {}
        for name, f in spec.items():
            if name in REPORT_ONLY:
                continue
            values = [cast(dict[str, Any], g).get(name) for g in got]
            allowed: set[Any] = {True, False} if f.kind is FieldKind.BOOLEAN else set(f.values)
            settled, value = decide(values, name, unanimous, allowed)
            if settled:
                labels[name] = value
            else:
                open_fields[name] = values
        path = root / "cases" / f"{cid}.md"
        old = dict(parse_card(path.read_text(encoding="utf-8"), source=path.name).expected.labels)
        writes[path] = review.merged({k: v for k, v in old.items() if k not in REPORT_ONLY},
                                     labels)  # fmt: skip
        if open_fields:
            out.disputed += 1
            out.fields.update(open_fields.keys())
            disputes.append({"id": cid, "whole": False, "fields": sorted(open_fields),
                             "judges": {n: {f"{p}:{m}": v for (p, m), v in zip(names, vs,
                                                                               strict=True)}
                                        for n, vs in open_fields.items()}})  # fmt: skip
        else:
            agreed_ids.append(cid)
    for path, labels in writes.items():
        review.set_labels(path, labels)
    if writes and (report := build_all(root)).findings:
        raise InvalidInputError(f"hygiene scan failed after writing labels: {report.findings[0]}")
    gen.verify(root)  # messages and facts unchanged: only the labels line moved
    by = {"by": label_file.MODELS, "judges": [f"{p}:{m}" for p, m in names]}
    out.agreed = label_file.confirm_many(root, agreed_ids, on, by) if agreed_ids else 0
    work = gen.Work(root)
    work.append(DISPUTES, [d | {"on": on.isoformat()} for d in disputes])
    work.append(AGREEMENT, [{"on": on.isoformat(), "judges": [
        {"judge": f"{p}:{m}", "file": path.name,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for (path, _r), (p, m) in zip(found, names, strict=True)],
        "agreed": out.agreed, "disputed": out.disputed, "whole": out.whole,
        "fields": dict(out.fields)}])  # fmt: skip
    return out


def open_disputes(root: Path) -> list[dict[str, Any]]:
    """The latest dispute entry for each case that isn't confirmed yet."""
    ok = label_file.models_ok(root)
    pending = {
        r["id"] for r in label_file.read(root) if not label_file.is_confirmed(r, models_ok=ok)
    }
    latest: dict[str, dict[str, Any]] = {}
    for d in gen.Work(root).read(DISPUTES):
        latest[d["id"]] = d
    return [d for cid, d in latest.items() if cid in pending]


def kappa(a: list[Any], b: list[Any]) -> float | None:
    """Cohen's kappa for two judges' answers on the same cases, or None when undefined."""
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)
