"""v3.0.0 step 3's offline tables (planning-docs/v3.0.0-STEP3.md §6): what each candidate
unsure band and confidence bar would do, from the probabilities several Jev eval runs recorded,
scored with ecf's own plan and hold (`evaljev.remap`), with no TypeSafe call. The operator chooses
every band and bar from these tables (operator decision 2026-10-14, step 3a diff review D1: a
lower bound can only catch more true cards, so no fixed order of criteria picks one).

    uv run python scripts/jev_sweep.py --root tests/eval/synthetic RUN1.json RUN2.json ... [--json]

- **Data:** the runs must share one prompt digest and the set's current version; each card's
  mean probability over the runs that answered it (P3), its answer recomputed from the means as a
  run computes it (yes at p >= 0.5, the most probable choice, a score's most probable level with
  ties to the higher). A card no run answered, or whose answer didn't map, stays a failure.
- **Groups (P3):** `phishing` alone; the thin items (`private_individual`,
  `bank_detail_change`, `claims_internal`, `regulator_claim`) share one lower bound;
  `payment_related` joins them under 40 positives, else is alone (106 on the 304-card set);
  `requires_action` alone (P11).
- **Per group and candidate** (no band, 0.30-0.45 by 0.05), on every card and on each of two
  folds (a deterministic assignment: cards sorted by ID within each pattern of banded-item and
  fraud-guard positives, alternated; S3-D1): unsafe cases, fraud-guard misses, true cards caught
  as unsure, true cards still a clear no, false cards flagged (flag volume), held cases and
  automation lost; the cards within 0.08 of the bound and of 0.5 (expected flips per run, from
  the live check's run-to-run spread). The folds show how much a figure moves between halves.
- **Bars (P11, D4, step 4 P17):** `category` and `sender_type` (unsafe, held and automation lost
  per bar), `fraud_risk`'s p(none) bar (cases flagged and how many are labelled low or higher).

It prints the tables; it writes nothing. Exit 2 on a bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval.results import CaseResult, ResultFile, load_result
from ecf.schema import FIXED_ITEMS, CompiledSchema, FieldKind, load_schema
from ecf_server import evaljev, evalrun, jev_mapping, systemone
from ecf_server.clock import SystemClock
from ecf_server.evalrun import Case
from ecf_server.jev_mapping import JevMapping

CANDIDATES: tuple[float | None, ...] = (None, 0.30, 0.35, 0.40, 0.45)
BARS: tuple[float | None, ...] = (None, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
EDGE = 0.08  # the live check's largest run-to-run move in a probability (SPEC §21.2)
THIN_LIMIT = 40  # `payment_related` joins the shared band under this many positives


def mean_cases(results: list[ResultFile], schema: CompiledSchema) -> list[CaseResult]:
    """One case per card: the mean of the probabilities of the runs that answered it, its answer
    recomputed from them; a card no run answered keeps the first run's failure."""
    first = {c.id: c for c in results[0].cases}
    by_id: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        for c in r.cases:
            if (
                c.fault is None
                and set(c.got) - {"rule"}
                and set(schema.fields) <= set(c.probabilities or {})
            ):  # an answer that mapped, every field's probabilities
                by_id[c.id].append(c)
    out: list[CaseResult] = []
    for cid, c0 in first.items():
        answered = by_id.get(cid)
        if not answered:
            out.append(c0)
            continue
        probs: dict[str, dict[str, float]] = {}
        for name in schema.fields:
            keys = {k for c in answered for k in (c.probabilities or {}).get(name, {})}
            probs[name] = {k: sum((c.probabilities or {}).get(name, {}).get(k, 0.0)
                                  for c in answered) / len(answered) for k in keys}  # fmt: skip
        out.append(c0.model_copy(update={"probabilities": probs, "fault": None,
                                         "got": answer(probs, schema)}))  # fmt: skip
    return out


def answer(probs: dict[str, dict[str, float]], schema: CompiledSchema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in schema.fields.values():
        p = probs.get(f.name, {})
        if f.kind is FieldKind.BOOLEAN:
            out[f.name] = p.get("true", 0.0) >= systemone.NOUL_TRUE
        elif f.kind is FieldKind.ENUM:
            out[f.name] = max(f.values, key=lambda v: p.get(v, 0.0))
        else:  # ties to the higher level, as `map_answers`
            out[f.name] = max(reversed(f.values), key=lambda v: p.get(v, 0.0))
    return out


def positives(inputs: evaljev.RemapInputs, name: str) -> dict[str, bool]:
    """Each labelled card's expected answer for a yes/no field."""
    out: dict[str, bool] = {}
    for cid, (case, _facts) in inputs.cases.items():
        v = _labels(case).get(name)
        if isinstance(v, bool) and case.confirmed:
            out[cid] = v
    return out


def _labels(case: Case) -> dict[str, Any]:
    return cast("dict[str, Any]", case.expected.get("labels") or {})


def folds(inputs: evaljev.RemapInputs, banded: Iterable[str]) -> tuple[set[str], set[str]]:
    labels = {n: positives(inputs, n) for n in banded}
    pattern: dict[tuple[bool, ...], list[str]] = defaultdict(list)
    for cid, (case, _facts) in sorted(inputs.cases.items()):
        key = (
            *(labels[n].get(cid, False) for n in sorted(labels)),
            case.expected.get("rule") == "fraud_guard",
        )
        pattern[key].append(cid)
    a: set[str] = set()
    b: set[str] = set()
    for ids in pattern.values():
        for i, cid in enumerate(ids):
            (a if i % 2 == 0 else b).add(cid)
    return a, b


def _p(c: CaseResult, name: str) -> float | None:
    v = (c.probabilities or {}).get(name, {}).get("true")
    return v if isinstance(v, float) else None


def figures(scored: list[CaseResult], fields: list[str], lo: float | None,
            inputs: evaljev.RemapInputs, ids: set[str]) -> dict[str, Any]:  # fmt: skip
    """On `ids`: unsafe cases and fraud-guard misses from the remapped cases, then over the group's
    fields the true cards caught as unsure, the true cards still a clear no, and the false cards
    flagged."""
    bound = lo if lo is not None else systemone.NOUL_TRUE
    by = {c.id: c for c in scored}
    caught = clear_no = flags = 0
    for name in fields:
        for cid, truth in positives(inputs, name).items():
            if cid not in ids or (c := by.get(cid)) is None or (p := _p(c, name)) is None:
                continue
            if p >= systemone.NOUL_TRUE:
                continue  # a yes, as today
            if truth:
                caught += p >= bound
                clear_no += p < bound
            else:
                flags += p >= bound
    counted = [c for c in scored if c.id in ids and c.confirmed]
    fg = [c for c in counted if c.fraud_guard]
    return {"unsafe": sum(not c.safety for c in counted),
            "fraud_guard_missed": sum(c.got.get("rule") != "fraud_guard" for c in fg),
            "true_caught_unsure": caught, "true_clear_no": clear_no,
            "false_flagged": flags}  # fmt: skip


def group_table(cases: list[CaseResult], inputs: evaljev.RemapInputs, fields: list[str],
                fold_a: set[str], fold_b: set[str]) -> list[dict[str, Any]]:  # fmt: skip
    """Each candidate lower bound for one group (the other groups at no band)."""
    every = set(inputs.cases)
    rows: list[dict[str, Any]] = []
    for lo in CANDIDATES:
        m = JevMapping(bands={n: lo for n in fields} if lo is not None else {})
        scored = evaljev.remap(cases, inputs, m)
        lost = evalrun.automation_lost(scored, _cases(inputs), inputs.rules)
        rows.append({"lo": lo, "all": figures(scored, fields, lo, inputs, every),
                     "a": figures(scored, fields, lo, inputs, fold_a),
                     "b": figures(scored, fields, lo, inputs, fold_b),
                     "held_cases": sum(bool(c.held) for c in scored),
                     "automation_lost": len(lost), "edges": edges(cases, fields, lo)})  # fmt: skip
    return rows


def _cases(inputs: evaljev.RemapInputs) -> list[Case]:
    return [case for case, _facts in inputs.cases.values()]


def bar_table(cases: list[CaseResult], inputs: evaljev.RemapInputs, name: str,
              make: Callable[[float], JevMapping]) -> list[dict[str, Any]]:  # fmt: skip
    """Per candidate bar: unsafe cases, cases it held, and automation lost."""
    rows: list[dict[str, Any]] = []
    for bar in BARS:
        m = make(bar) if bar is not None else JevMapping()
        scored = evaljev.remap(cases, inputs, m)
        held = [c for c in scored if c.held and any(name in h for h in c.held)]
        lost = evalrun.automation_lost(held, _cases(inputs), inputs.rules)
        rows.append({"bar": bar, "unsafe": sum(not c.safety for c in scored if c.confirmed),
                     "held": len(held), "automation_lost": len(lost)})  # fmt: skip
    return rows


def none_bar_table(cases: list[CaseResult], inputs: evaljev.RemapInputs) -> list[dict[str, Any]]:
    """`fraud_risk`'s p(none) bar: cases answered `none` it flags, and how many of those are
    labelled low or higher."""
    rows: list[dict[str, Any]] = []
    for bar in (b for b in BARS if b is not None):
        flagged = risky = 0
        for c in cases:
            p = (c.probabilities or {}).get("fraud_risk", {}).get("none")
            if c.got.get("fraud_risk") != "none" or p is None or p >= bar:
                continue
            flagged += 1
            case = inputs.cases.get(c.id)
            label = _labels(case[0]).get("fraud_risk") if case else None
            risky += label in ("low", "medium", "high")
        rows.append({"bar": bar, "flagged": flagged, "labelled_low_or_higher": risky})
    return rows


def edges(cases: list[CaseResult], fields: list[str], lo: float | None) -> dict[str, int]:
    near_lo = near_yes = 0
    for c in cases:
        for name in fields:
            p = _p(c, name)
            if p is None:
                continue
            near_lo += lo is not None and abs(p - lo) <= EDGE
            near_yes += abs(p - systemone.NOUL_TRUE) <= EDGE
    return {"within_0.08_of_bound": near_lo, "within_0.08_of_0.5": near_yes}


def groups_for(inputs: evaljev.RemapInputs) -> dict[str, list[str]]:
    """`phishing` alone, the thin items sharing one bound (`payment_related` among them under
    THIN_LIMIT positives, else alone), `requires_action` alone."""
    thin = [n for n in FIXED_ITEMS if n != "phishing"]
    out = {"phishing": ["phishing"], "shared": thin}
    if sum(positives(inputs, "payment_related").values()) < THIN_LIMIT:
        thin.append("payment_related")
    else:
        out["payment_related"] = ["payment_related"]
    return out | {"requires_action": ["requires_action"]}


def report(paths: list[Path], root: Path) -> dict[str, Any]:
    results = [load_result(p) for p in paths]
    if any((r.summary or {}).get("backend") != "jev" for r in results):
        raise InvalidInputError("every result must be a Jev run (`ecf eval run`, backend jev)")
    if len({(r.summary or {}).get("prompt_digest") for r in results}) != 1:
        raise InvalidInputError("the runs were asked differently (prompt digests differ): use one"
                                " arm's runs")  # fmt: skip
    version = evalrun.set_version(root)
    if any(r.set_version != version for r in results):
        raise InvalidInputError(f"a run is on another version of the set than {root} ({version})")
    schema = load_schema()
    inputs = evaljev.remap_inputs(root, SystemClock(), schema)
    cases = mean_cases(results, schema)
    groups = groups_for(inputs)
    a, b = folds(inputs, [n for fs in groups.values() for n in fs])
    return {
        "runs": [r.run_id for r in results],
        "positives": {n: sum(positives(inputs, n).values()) for fs in groups.values() for n in fs},
        "folds": {"a": sorted(a), "b": sorted(b)},
        "groups": {g: {"fields": fs, "table": group_table(cases, inputs, fs, a, b)}
                   for g, fs in groups.items()},
        "bars": {n: bar_table(cases, inputs, n, lambda bar, n=n: JevMapping(bars={n: bar}))
                 for n in sorted(jev_mapping.BARRABLE)},
        "fraud_risk_none_bar": none_bar_table(cases, inputs),
    }  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("runs", nargs="+", type=Path, help="Jev result files (the frozen arm's)")
    ap.add_argument("--root", type=Path, required=True, help="the synthetic set folder")
    ap.add_argument("--json", action="store_true", help="the whole report as JSON")
    args = ap.parse_args(argv)
    try:
        r = report(args.runs, args.root)
    except (InvalidInputError, OSError) as exc:
        print(f"jev_sweep: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"runs: {', '.join(x[:8] for x in r['runs'])}; positives: {r['positives']}")
    for g, grp in r["groups"].items():
        print(f"{g} ({', '.join(grp['fields'])}):")
        for row in grp["table"]:
            f, fa, fb = row["all"], row["a"], row["b"]
            print(
                f"  lo {row['lo']}: unsafe {f['unsafe']}, fg missed {f['fraud_guard_missed']},"
                f" caught {f['true_caught_unsure']} (a {fa['true_caught_unsure']}, b"
                f" {fb['true_caught_unsure']}), clear no {f['true_clear_no']}, flagged"
                f" {f['false_flagged']} (a {fa['false_flagged']}, b {fb['false_flagged']}),"
                f" held {row['held_cases']}, lost {row['automation_lost']}, edges"
                f" {row['edges']}"
            )
    for n, rows in r["bars"].items():
        print(
            f"bar {n}: "
            + "; ".join(
                f"{x['bar']}: unsafe {x['unsafe']}, held {x['held']}, lost {x['automation_lost']}"
                for x in rows
            )
        )
    print(
        "fraud_risk p(none) bar: "
        + "; ".join(
            f"{x['bar']}: flagged {x['flagged']} ({x['labelled_low_or_higher']} labelled low+)"
            for x in r["fraud_risk_none_bar"]
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
