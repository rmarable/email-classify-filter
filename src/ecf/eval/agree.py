"""`ecf eval agree`: the judges set a generated set's labels; the operator settles only where they
disagree (eval generate plan, Judges; OD-547, Phase R J1-J20).

The judges are the set's grade files (`grades/*.json`), one per model, every judge a different
model and, in PROD, every pair of generator, seed writer and judges a different family. Per case
and field: a boolean or `fraud_risk` counts only when every judge gave the same valid value;
`category` takes a majority when three or more judges answered. `sender_type` and `priority`
are never asked here and never set on the card (eval plan draft 21, OD-549): `ecf eval
expect` evaluates the rules with each judge's value and sends them to the operator only when they
decide the expected rule. A case any judge refused, left out or answered with a missing asked
field goes to the operator whole (with sender_type and priority too when every judge refused).
Agreed values are written into the cards at once, the set rebuilt once, and fully agreed cases
confirmed in one atomic write with their `labelled_by` (`labels.confirm_many`); the rest go to
`work/disputes.jsonl` with the judges' closed values, for `ecf eval settle`.
`work/agreement.jsonl` records each judge file's digest so the report and expect can recompute
from them. A rerun skips a judged row whose labels are unchanged since it was confirmed, and an
operator-confirmed row; a judged row edited by hand is decided again, keeping the fields the
operator settled.
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

MAJORITY_FIELDS = ("category",)
MAX_JUDGES = 3  # OD-547; expect's combinations are bounded by it
UNASKED = label_file.UNASKED  # never asked or set here (eval plan draft 21)
DISPUTES = "disputes"
AGREEMENT = "agreement"


@dataclass
class Outcome:
    agreed: int = 0  # cases every counted field agreed on, confirmed by the judges
    disputed: int = 0  # cases with at least one field for the operator
    whole: int = 0  # cases sent whole (a judge refused, left it out or a field missing)
    skipped: int = 0  # already confirmed: left alone
    redecided: int = 0  # judged rows edited by hand, decided again
    yours_edited: int = 0  # your confirmed rows whose card was edited: unconfirmed by the rebuild
    waiting: int = 0  # judged rows left alone with a dispute still open
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
    if len(found) > MAX_JUDGES:
        raise InvalidInputError(f"{root}: at most {MAX_JUDGES} judges' grade files in"
                                f" {grading.GRADES}/ (OD-547)")  # fmt: skip
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
    if mode == "prod":  # judges differ in family from each other and from every writer
        for a, b in combinations(judge_ids, 2):
            models.check_pair(a, b)
        for j in judge_ids:
            for w in writers:
                models.check_pair(w, j)
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


def run(root: Path, on: date) -> Outcome:  # noqa: PLR0912, PLR0915 - one pass over the cases
    doc = gen.verify(root)
    found = judges(root, doc)
    spec = load_schema().fields
    unanimous = unanimous_fields()
    answers = [{c.id: dict(c.got) for c in r.cases} for _p, r in found]
    names = [_judge(r) for _p, r in found]
    who = [f"{p}:{m}" for p, m in names]
    ok = label_file.models_ok(root)
    rows = label_file.read(root)
    out = Outcome()
    provenance: dict[str, dict[str, Any]] = {}
    disputes: list[dict[str, Any]] = []
    writes: dict[Path, dict[str, Any]] = {}
    reset: set[str] = set()
    drift = False  # a card whose labels differ from labels.jsonl: rebuild before marking
    for row in rows:
        cid = row["id"]
        lb: dict[str, Any] = row.get(label_file.LABELLED_BY) or {}
        path = root / "cases" / f"{cid}.md"
        old = dict(parse_card(path.read_text(encoding="utf-8"), source=path.name).expected.labels)
        hand = label_file.edited(row, old)
        drift = drift or label_file.hash_labels(old) != label_file.labels_hash(row)
        same = set(lb.get("judges") or []) == set(who)
        if (same and lb.get("labels_sha256") and not hand) or (
            not lb and label_file.is_confirmed(row, models_ok=ok)
        ):
            out.skipped += 1  # judged by these judges and unchanged since, or the operator's
            if not lb and label_file.hash_labels(old) != label_file.labels_hash(row):
                out.yours_edited += 1  # the rebuild below drops its confirmation
            elif lb and not label_file.is_confirmed(row, models_ok=ok):
                out.waiting += 1
            continue
        if hand:
            reset.add(cid)  # edited by hand: its rule came from other labels
        settled = label_file.settled(row)
        kept = {f: old[f] for f in settled if f in old}  # the operator's answers stand
        got = [a.get(cid) for a in answers]
        if any(not g for g in got):  # refused, too large or left out by a judge
            fields = [n for n in spec if n not in UNASKED or not any(got)]
            provenance[cid] = {"judges": who, "settled": settled}
            if left := [f for f in fields if f not in kept]:
                out.whole += 1
                out.disputed += 1
                disputes.append({"id": cid, "whole": True, "fields": left})
            continue  # nothing left: you settled every field, so it is confirmed
        labels: dict[str, Any] = {}
        open_fields: dict[str, list[Any]] = {}
        for name, f in spec.items():
            if name in UNASKED or name in kept:
                continue
            values = [cast(dict[str, Any], g).get(name) for g in got]
            allowed: set[Any] = {True, False} if f.kind is FieldKind.BOOLEAN else set(f.values)
            settled_ok, value = decide(values, name, unanimous, allowed)
            if settled_ok:
                labels[name] = value
            else:
                open_fields[name] = values
        base = {k: v for k, v in old.items() if k not in UNASKED or k in kept}
        writes[path] = review.merged(base, labels | kept)
        provenance[cid] = {"judges": who, "settled": settled}
        if open_fields:
            out.disputed += 1
            out.fields.update(open_fields.keys())
            disputes.append({"id": cid, "whole": False, "fields": sorted(open_fields),
                             "judges": {n: {f"{p}:{m}": v for (p, m), v in zip(names, vs,
                                                                               strict=True)}
                                        for n, vs in open_fields.items()}})  # fmt: skip
    for path, labels in writes.items():
        review.write_labels(path, labels)
    for cid in reset:  # whatever path the row took (Y6)
        review.write_expected(root / "cases" / f"{cid}.md", None, review.DEFAULT_SAFETY)
    if (writes or reset or drift) and (report := build_all(root)).findings:
        raise InvalidInputError(f"hygiene scan failed after writing labels: {report.findings[0]}")
    gen.verify(root)  # messages and facts unchanged: only the labels line moved
    open_ids = {d["id"] for d in disputes}
    agreed = {c: p for c, p in provenance.items() if c not in open_ids}
    out.redecided = len(reset)
    # the logs first: a crash before the marks then re-decides those rows, and a retry only
    # repeats entries (the latest wins); never a marked row with no dispute (Z1)
    work = gen.Work(root)
    work.append(DISPUTES, [d | {"on": on.isoformat()} for d in disputes])
    work.append(AGREEMENT, [{"on": on.isoformat(), "judges": [
        {"judge": f"{p}:{m}", "file": path.name,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for (path, _r), (p, m) in zip(found, names, strict=True)],
        "agreed": len(agreed), "disputed": out.disputed, "whole": out.whole,
        "fields": dict(out.fields)}])  # fmt: skip
    out.agreed = label_file.confirm_many(root, agreed, on) if agreed else 0
    _mark(root, {c: p for c, p in provenance.items() if c in open_ids})  # hash taken now
    return out


def _mark(root: Path, provenance: dict[str, dict[str, Any]]) -> None:
    """A disputed row gets its `labelled_by` (judges, settled so far, the hash of its labels now)
    unconfirmed: it is judged, so `ecf eval label` leaves it to settle, and a hand edit before
    settling shows (Y2)."""
    if not provenance:
        return
    rows = label_file.read(root)
    for i, r in enumerate(rows):
        if (lb := provenance.get(r["id"])) is not None:
            rows[i] = {k: v for k, v in r.items() if k != "confirmed"} | {
                label_file.LABELLED_BY: lb | {"labels_sha256": label_file.labels_hash(r)}
            }
    label_file.write(root, rows)


def open_disputes(root: Path) -> list[dict[str, Any]]:
    """The latest dispute entry for each case that isn't confirmed yet, unless every field it
    names is one the operator settled since."""
    ok = label_file.models_ok(root)
    rows = label_file.read(root)
    pending = {r["id"] for r in rows if not label_file.is_confirmed(r, models_ok=ok)}
    settled = {r["id"]: set(label_file.settled(r)) for r in rows}
    latest: dict[str, dict[str, Any]] = {}
    for d in gen.Work(root).read(DISPUTES):
        latest[d["id"]] = d
    return [d for cid, d in latest.items()
            if cid in pending and not set(d["fields"]) <= settled.get(cid, set())]  # fmt: skip


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
