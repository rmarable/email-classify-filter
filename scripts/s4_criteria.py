"""The v3.0.0 S4 user-layer measurement, M2 (planning-docs/S4-USER-LAYER.md §7; OD-502):
the criteria named before the runs, computed from four eval result files with ecf's own scoring
(`ecf.eval.results`, `ecf.eval.metrics.newcombe_paired`, as `ecf eval compare` pairs cases).

    uv run python scripts/s4_criteria.py S4_RUN1.json S4_RUN2.json \\
        --baseline S3B_RUN1.json S3B_RUN2.json [--json]

Each comparison is S4 minus S3b on the confirmed, scored cases both runs have (B minus A, in
points, with Newcombe's paired 95% interval), in the four cross pairings (S4 run i against S3b
run j). Gated (each pairing's lower bound above minus the margin): `correct` 5; every labelled
shipped field right per case 5 (old-style `correct`: every labelled field of schema v2, the
fields before the fixed items, and the rule; the definition behind plan §7's -0.66 [-4.55,
3.24]); `fraud_risk` 5; rule 3.5; `category` 5; `phishing` recall 5 on the phishing-labelled
cases. Criterion 1, per S4 run: 0 unsafe, fraud-guard recall 100%, gate passed, and the refused
submissions per stage (`ClaimRecord.invalid`, P19). Report only (criterion 8): `priority`, the
workflow fields and `phishing` answered right (n 304), per-field McNemar with Holm, the
within-arm discordance of each pair, the noise reference (the S3b pair against itself, plan
§7's table), the answers to the user fields and added category values, and Sonnet input tokens
per case (from `summary.claude`); the bytes of schema per read aren't in a result file (`ecf
config apply` and `ecf doctor` print them). Then the pre-stated repeat rule: one more S4 run
only if exactly one pairing misses its margin by less than 1 point.

`--noise-only S3B_RUN1.json S3B_RUN2.json` prints the noise reference alone (plan §7's table:
run 2 minus run 1); with the S3b pair `024ec270` and `1a8708cf` it reproduces those figures
(`tests/test_s4_criteria.py`, from `tests/data/s3b_noise_pair.json`, derived from those files).
Exit 1 when a gated criterion fails, 2 on a bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval.metrics import newcombe_paired
from ecf.eval.results import (
    CaseResult,
    ResultFile,
    check_label_maps,
    compare,
    compare_fields,
    load_result,
)
from ecf.schema import load_schema, load_schema_v2

SHIPPED = frozenset(load_schema().fields)
# criterion 3, old-style `correct` (P6): schema v2's fields (the shipped fields before the fixed
# items) and the rule, each where labelled
OLD_STYLE = frozenset(load_schema_v2().fields) | {"rule"}
SHIPPED_CATEGORIES = frozenset(load_schema().fields["category"].values)
WORKFLOW = ("priority", "requires_action", "requires_reply", "deadline_mentioned",
            "payment_related")  # fmt: skip
NEAR_MISS = 1.0  # points: a miss this close allows the one pre-stated repeat (P5)

Key = Callable[[CaseResult], bool | None]


def _shipped_right(c: CaseResult) -> bool | None:
    vals = [ok for f, ok in c.fields.items() if f in OLD_STYLE]
    return all(vals) if vals else None


def _phishing_found(c: CaseResult) -> bool | None:
    return c.got.get("phishing") is True if c.expected.get("phishing") is True else None


def _correct(c: CaseResult) -> bool | None:
    return c.correct


def _field(name: str) -> Key:
    def key(c: CaseResult) -> bool | None:
        return c.fields.get(name)

    return key


# name -> (what is compared per case, the margin in points; None: report only)
FIGURES: dict[str, tuple[Key, float | None]] = {
    "correct": (_correct, 5.0),
    "every_shipped_field": (_shipped_right, 5.0),
    "fraud_risk": (_field("fraud_risk"), 5.0),
    "rule": (_field("rule"), 3.5),
    "category": (_field("category"), 5.0),
    "phishing_recall": (_phishing_found, 5.0),
    "phishing": (_field("phishing"), None),
    **{f: (_field(f), None) for f in WORKFLOW},
}


@dataclass(frozen=True)
class Paired:
    figure: str
    a: str  # run ids (8 characters)
    b: str
    n: int
    a_right: int
    b_right: int
    a_only: int
    b_only: int
    diff: float  # points, B minus A
    lo: float
    hi: float
    margin: float | None

    @property
    def passed(self) -> bool | None:
        return None if self.margin is None else self.lo > -self.margin


def _counted(r: ResultFile) -> dict[str, CaseResult]:
    return {c.id: c for c in r.cases if c.confirmed and c.scored}


def paired(figure: str, a: ResultFile, b: ResultFile, key: Key | None = None,
           margin: float | None = None) -> Paired:  # fmt: skip
    """B against A on `figure`, on the cases both count where `key` says something."""
    k = key if key is not None else FIGURES[figure][0]
    am, bm = _counted(a), _counted(b)
    pairs: list[tuple[bool, bool]] = []
    for cid, ca in am.items():
        cb = bm.get(cid)
        if cb is None:
            continue
        x, y = k(ca), k(cb)
        if x is not None and y is not None:
            pairs.append((x, y))
    return _paired(figure, a.run_id[:8], b.run_id[:8], pairs, margin)


def _paired(figure: str, a: str, b: str, pairs: list[tuple[bool, bool]],
            margin: float | None) -> Paired:  # fmt: skip
    n = len(pairs)
    a_only = sum(x and not y for x, y in pairs)
    b_only = sum(y and not x for x, y in pairs)
    both = sum(x and y for x, y in pairs)
    lo, hi = newcombe_paired(both, b_only, a_only, n - both - a_only - b_only)
    return Paired(figure, a, b, n, sum(x for x, _ in pairs), sum(y for _, y in pairs), a_only,
                  b_only, round(100 * (b_only - a_only) / n, 2) if n else 0.0,
                  round(100 * lo, 2), round(100 * hi, 2), margin)  # fmt: skip


def pooled(figure: str, s4: list[ResultFile], s3b: list[ResultFile]) -> Paired:
    """Run i against run i, pairs concatenated (S3b's pooled phishing figure; it counts each
    card twice, so its interval is too narrow: reported only, P21)."""
    key = FIGURES[figure][0]
    pairs: list[tuple[bool, bool]] = []
    for a, b in zip(s3b, s4, strict=True):
        am, bm = _counted(a), _counted(b)
        for cid, ca in am.items():
            cb = bm.get(cid)
            x, y = (key(ca), key(cb)) if cb is not None else (None, None)
            if x is not None and y is not None:
                pairs.append((x, y))
    return _paired(f"{figure}_pooled", "s3b", "s4", pairs, None)


def run_checks(r: ResultFile) -> dict[str, Any]:
    """Criterion 1 for one run: the summary's gates, and refused submissions per stage."""
    s = r.summary or {}
    refused: Counter[str] = Counter()
    for c in r.cases:
        for claim in c.claims:
            refused[claim.stage] += claim.invalid
    unsafe = cast("list[str]", s.get("unsafe") or [])
    missed = cast("list[str]", s.get("fraud_guard_missed") or [])
    return {"run": r.run_id[:8], "unsafe": len(unsafe), "fraud_guard_cases": s.get(
        "fraud_guard_cases"), "fraud_guard_missed": len(missed), "gate_passed": s.get(
        "gate_passed") is True, "refused_classify": refused["classify"],
        "refused_act": refused["act"] + refused["repeat"],
        "passed": not unsafe and not missed and s.get("gate_passed") is True}  # fmt: skip


def user_answers(r: ResultFile) -> dict[str, dict[str, int]]:
    """Each user field's answers, and the added category values answered (from `got`)."""
    out: dict[str, Counter[str]] = {}
    for c in _counted(r).values():
        for f, v in c.got.items():
            if f not in SHIPPED and f != "rule":
                out.setdefault(f, Counter())[str(v).lower() if isinstance(v, bool) else str(v)] += 1
        cat = c.got.get("category")
        if isinstance(cat, str) and cat not in SHIPPED_CATEGORIES:
            out.setdefault("category (added values)", Counter())[cat] += 1
    return {f: dict(sorted(n.items())) for f, n in sorted(out.items())}


def added_category_cases(r: ResultFile) -> list[str]:
    return sorted(c.id for c in _counted(r).values() if isinstance(
        v := c.got.get("category"), str) and v not in SHIPPED_CATEGORIES)  # fmt: skip


def sonnet_input_per_case(r: ResultFile) -> float | None:
    """Sonnet input tokens, cache read and written included, per case (as S2's F6); None when
    the result has no Claude figures."""
    claude = (r.summary or {}).get("claude")
    groups = cast("dict[str, Any]", claude).get("groups") if isinstance(claude, dict) else None
    if not isinstance(groups, list) or not r.cases:
        return None
    total = 0
    for g in cast("list[dict[str, Any]]", groups):
        if "sonnet" in str(g.get("model")):
            total += sum(int(g.get(k) or 0) for k in ("input_tokens", "cache_read_tokens",
                                                     "cache_creation_tokens"))  # fmt: skip
    return round(total / len(r.cases), 1)


def noise(a: ResultFile, b: ResultFile) -> list[Paired]:
    """The within-arm table (plan §7): run 2 against run 1 of the same arm, every figure."""
    return [paired(f, a, b) for f in FIGURES]


def repeat_line(gated: list[Paired]) -> str:
    """P5's pre-stated repeat: one more S4 run only if exactly one pairing misses, by < 1 point."""
    misses = [p for p in gated if p.passed is False]
    if not misses:
        return "repeat: not needed (every gated pairing passed)"
    m = misses[0]
    if len(misses) == 1 and m.margin is not None and m.lo > -(m.margin + NEAR_MISS):
        return (f"repeat: allowed, one more S4 run (only {m.figure} {m.b} vs {m.a} missed, by"
                f" {-m.margin - m.lo:.2f} points); then the pairings with the new run"
                " decide")  # fmt: skip
    return f"repeat: not allowed ({len(misses)} pairing(s) missed, or by 1 point or more)"


def criteria(s4: list[ResultFile], s3b: list[ResultFile]) -> dict[str, Any]:
    if len(s4) != 2 or len(s3b) != 2:
        raise InvalidInputError("M2 takes two S4 runs and two S3b runs")
    for a in s3b:
        for b in s4:
            check_label_maps(a, b)
    versions = {r.set_version for r in (*s4, *s3b)}
    gated = [paired(f, a, b, margin=m) for f, (_, m) in FIGURES.items() if m is not None
             for a in s3b for b in s4]  # fmt: skip
    report = [paired(f, a, b) for f, (_, m) in FIGURES.items() if m is None
              for a in s3b for b in s4]  # fmt: skip
    holm = {f"{b.run_id[:8]} vs {a.run_id[:8]}": [
        {"field": x.field, "n": x.n, "s4_only": x.b_only, "s3b_only": x.a_only,
         "p": round(x.p_value, 4), "p_holm": round(x.p_holm, 4), "significant": x.significant}
        for x in compare_fields(a, b)] for a in s3b for b in s4}  # fmt: skip
    within = {}
    for name, (x, y) in (("s3b", s3b), ("s4", s4)):
        c = compare(x, y)
        within[name] = {"n": c.n, "discordant": c.a_only + c.b_only, "run1_only": c.a_only,
                        "run2_only": c.b_only}  # fmt: skip
    runs = [run_checks(r) for r in s4]
    ok = all(r["passed"] for r in runs) and all(p.passed for p in gated)
    return {
        "set_versions": sorted(versions),
        "criterion_1": runs,
        "gated": [asdict(p) | {"passed": p.passed} for p in gated],
        "phishing_pooled": asdict(pooled("phishing_recall", s4, s3b)),
        "added_category_cases": {r.run_id[:8]: added_category_cases(r) for r in s4},
        "report_only": [asdict(p) for p in report],
        "per_field_holm": holm,
        "within_arm": within,
        "noise_reference": [asdict(p) for p in noise(*s3b)],
        "user_answers": {r.run_id[:8]: user_answers(r) for r in s4},
        "sonnet_input_per_case": {r.run_id[:8]: sonnet_input_per_case(r) for r in (*s4, *s3b)},
        "read_bytes": "not in the result (ecf config apply and ecf doctor print it)",
        "repeat": repeat_line(gated),
        "passed": ok,
    }


def _line(p: dict[str, Any]) -> str:
    verdict = "" if p.get("margin") is None else (
        f"  {'PASS' if p['passed'] else 'FAIL'} (> -{p['margin']:g})")  # fmt: skip
    return (f"{p['figure']}: {p['b']} vs {p['a']} n {p['n']}: {p['b_right']} vs {p['a_right']},"
            f" {p['diff']:+.2f} [{p['lo']:.2f}, {p['hi']:.2f}] (s4-only {p['b_only']},"
            f" s3b-only {p['a_only']}){verdict}")  # fmt: skip


def text(out: dict[str, Any]) -> str:
    lines = [f"set versions: {', '.join(out['set_versions'])}", "criterion 1:"]
    for r in out["criterion_1"]:
        lines.append(f"  {r['run']}: unsafe {r['unsafe']}, fraud-guard missed"
                     f" {r['fraud_guard_missed']} of {r['fraud_guard_cases']}, gate passed"
                     f" {r['gate_passed']}, refused submissions classify {r['refused_classify']}"
                     f" act {r['refused_act']}  {'PASS' if r['passed'] else 'FAIL'}")  # fmt: skip
    lines.append("criteria 2-7 (S4 minus S3b, points, Newcombe 95%):")
    lines += [f"  {_line(p)}" for p in out["gated"]]
    lines.append(f"  pooled (reported only): {_line(out['phishing_pooled'])}")
    for run, ids in out["added_category_cases"].items():
        lines.append(f"  added category values answered in {run}: {', '.join(ids) or 'none'}")
    lines.append("report only:")
    lines += [f"  {_line(p)}" for p in out["report_only"]]
    for pair, rows in out["per_field_holm"].items():
        sig = [f"{x['field']} (p_holm {x['p_holm']})" for x in rows if x["significant"]]
        lines.append(f"  per-field McNemar/Holm {pair}: {', '.join(sig) or 'none significant'}")
    for arm, w in out["within_arm"].items():
        lines.append(f"  within {arm}: correct discordant {w['discordant']} of {w['n']}"
                     f" ({w['run1_only']}/{w['run2_only']})")  # fmt: skip
    lines.append("  noise reference (S3b run 2 vs run 1):")
    lines += [f"    {_line(p)}" for p in out["noise_reference"]]
    for run, fields in out["user_answers"].items():
        for f, counts in fields.items():
            lines.append(f"  {run} {f}: " + ", ".join(f"{v} {n}" for v, n in counts.items()))
    for run, v in out["sonnet_input_per_case"].items():
        lines.append(f"  {run} Sonnet input per case: {'not in the result' if v is None else v}")
    lines.append(f"  bytes per read: {out['read_bytes']}")
    lines += [out["repeat"], "M2: " + ("PASS" if out["passed"] else "FAIL")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("s4", nargs="*", type=Path, help="the two S4 result files")
    p.add_argument("--baseline", nargs=2, type=Path, help="the two S3b result files")
    p.add_argument("--noise-only", nargs=2, type=Path, help="one arm's two runs: the table alone")
    p.add_argument("--json", action="store_true", help="JSON instead of text")
    args = p.parse_args(argv)
    try:
        if args.noise_only:
            a, b = (load_result(x) for x in args.noise_only)
            check_label_maps(a, b)
            rows = [asdict(x) for x in noise(a, b)]
            print(json.dumps(rows, indent=2) if args.json else "\n".join(_line(x) for x in rows))
            return 0
        if len(args.s4) != 2 or not args.baseline:
            p.error("give two S4 result files and --baseline with two S3b ones")
        out = criteria([load_result(x) for x in args.s4], [load_result(x) for x in args.baseline])
    except InvalidInputError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2) if args.json else text(out))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
