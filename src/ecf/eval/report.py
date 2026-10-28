"""`ecf eval report`: a generated set's figures (eval generate plan, Report; P8, P13, P22, P25).

Against your confirmed labels only (OD-486): the grader's measured accuracy first, then each ecf
result's agreement with your labels and with the grader, per field and per group (a group under
`REPORT_ONLY_BELOW` cases is report-only), with Wilson intervals; and how often the generator's
own label hints matched yours. Rule and safety figures appear only once `ecf eval expect` has
filled them. A DEV set's report shows plumbing counts only, never accuracy. No email or card
text is printed: ids, field names, closed values and counts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, genset, models
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval.metrics import wilson
from ecf.eval.results import (
    REPORT_ONLY_BELOW,
    ResultFile,
    compare,
    holm_fields,
    paired_fields,
    subset_lines,
)
from ecf.schema import FIXED_ITEMS, load_schema

DEV_BANNER = "DEV: plumbing test, not a measurement"


def group_of(case_id: str) -> str:
    return re.sub(r"-\d{3}$", "", case_id.removeprefix(gen.DEV_PREFIX))


def confirmed(root: Path) -> dict[str, dict[str, Any]]:
    """Each confirmed case's expected values (labels, rule, safety), by id."""
    ok = label_file.models_ok(root)
    return {r["id"]: r["expected"] for r in label_file.read(root)
            if label_file.is_confirmed(r, models_ok=ok)}  # fmt: skip


def _asked(exp: Mapping[str, Mapping[str, Any]], rows: list[dict[str, Any]], *,
           deferred: bool = False) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Each case's labels without those never scored on its row (`labels.unscored`):
    sender_type and priority on a judge-labelled row (eval plan draft 21, OD-549), and the
    fields the row defers (OD-563); with `deferred`, those are kept, for the judges' figures on
    what you settled (marked as a subset)."""
    by_id = {r["id"]: r for r in rows}

    def skip(c: str) -> set[str]:
        row = by_id.get(c) or {}
        base: set[str] = set(label_file.UNASKED) if label_file.judged(row) else set()
        return base if deferred else base | set(label_file.unscored(row))

    out: dict[str, dict[str, Any]] = {}
    for c, e in exp.items():
        drop = skip(c)  # once per case
        out[c] = {k: v for k, v in cast(dict[str, Any], e.get("labels") or {}).items()
                  if k not in drop}  # fmt: skip
    return out


def _figure(agree: int, n: int) -> str:
    if not n:
        return "-"
    lo, hi = wilson(agree, n)
    return f"{agree}/{n} {100 * agree / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def agreement(a: Mapping[str, Mapping[str, Any]], b: Mapping[str, Mapping[str, Any]],
              fields: tuple[str, ...]) -> dict[str, tuple[int, int]]:  # fmt: skip
    """Per field, (agree, n) over the cases both answer."""
    out: dict[str, tuple[int, int]] = {}
    for f in fields:
        pairs = [(a[c][f], b[c][f]) for c in a if c in b and f in a[c] and f in b[c]]
        out[f] = (sum(x == y for x, y in pairs), len(pairs))
    return out


def _field_lines(title: str, figures: dict[str, tuple[int, int]],
                 subset: frozenset[str] = frozenset()) -> list[str]:  # fmt: skip
    """A figure per field and their total; a field in `subset` (deferred, OD-563) is shown as a
    judges-agreed or asked subset and left out of the total."""
    lines = [title]
    lines += [f"  {f:<20} {_figure(*v)}"
              + ("  (judges-agreed or asked subset, not an accuracy)" if f in subset else "")
              for f, v in figures.items() if v[1]]  # fmt: skip
    kept = [v for f, v in figures.items() if f not in subset]
    total = (sum(v[0] for v in kept), sum(v[1] for v in kept))
    lines.append(f"  {'all fields':<20} {_figure(*total)}")
    return lines


def _group_lines(got: Mapping[str, Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]],
                 fields: tuple[str, ...]) -> list[str]:  # fmt: skip
    groups = sorted({group_of(c) for c in labels})
    lines = ["  by group (fields agreeing):"]
    for g in groups:
        sub = {c: v for c, v in labels.items() if group_of(c) == g}
        fig = agreement(got, sub, fields)
        cases = sum(1 for c in sub if c in got)
        agree, n = sum(v[0] for v in fig.values()), sum(v[1] for v in fig.values())
        note = " (report-only)" if cases < REPORT_ONLY_BELOW else ""
        lines.append(f"    {g:<20} {cases:>4} cases  {_figure(agree, n)}{note}")
    return lines


def _plumbing(doc: dict[str, Any]) -> list[str]:
    drops = cast(dict[str, dict[str, int]], doc.get("dropped") or {})
    refused = cast(dict[str, int], doc.get("refused") or {})
    lines = [f"generator {doc['generator'].get('provider')}:{doc['generator'].get('model')}"
             f" ({doc['generator'].get('version') or 'version not recorded'}); {doc['calls']}"
             f" calls, {doc['seconds']} s; usage {doc.get('usage') or {}}",
             f"kept {sum(g['kept'] for g in doc['groups'].values())} of {doc['count']};"
             f" refused {sum(refused.values())}"
             + (f" ({', '.join(f'{g} {n}' for g, n in refused.items())})" if refused
                else "")]  # fmt: skip
    for reason, by_group in drops.items():
        lines.append(f"dropped, {reason}: {sum(by_group.values())}"
                     f" ({', '.join(f'{g} {n}' for g, n in by_group.items())})")  # fmt: skip
    if drops:
        lines.append("  (a near-duplicate drop counts against the later email's group)")
    kinds = cast(dict[str, int], doc.get("hygiene_kinds") or {})
    if kinds:
        lines.append("hygiene drops by finding kind: "
                     + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))  # fmt: skip
    on_retry = cast(dict[str, Any], doc.get("refused_on_retry") or {})
    if on_retry.get("count"):  # within the hygiene drops; their kinds are first-pass kinds
        lines.append(f"refused on the hygiene retry (counted as hygiene drops): {on_retry['count']}"
                     " (" + ", ".join(f"{k} {n}" for k, n in sorted(on_retry["kinds"].items()))
                     + ")")  # fmt: skip
    passed = cast(dict[str, Any], doc.get("retried_passed") or {})
    if passed.get("count"):
        lines.append(f"passed on the hygiene retry: {passed['count']} ("
                     + ", ".join(f"{k} {n}" for k, n in sorted(passed["kinds"].items()))
                     + ")")  # fmt: skip
    if doc.get("seeds_only"):
        lines.append("a seed run: seeds only, no emails (OD-545)")
    if shared := cast(dict[str, Any] | None, doc.get("seeds_from")):
        lines.append(f"seeds written by {shared['generator']} (seed run {shared['set']},"
                     f" {str(shared['seeds_sha256'])[:12]}); {len(doc.get('kept_seeds') or [])}"
                     " kept emails, each from a shared seed")  # fmt: skip
    if doc.get("no_shared_seeds"):
        lines.append("unmeasured (the seed run has no seeds for them): "
                     + ", ".join(doc["no_shared_seeds"]))  # fmt: skip
    if "repeated_openings" in doc:
        lines.append(f"repeated opening lines among kept emails: {doc['repeated_openings']}")
    if doc.get("selected"):
        lines.append(f"selected (over a third dropped): {', '.join(doc['selected'])}")
    return lines


def assignments(root: Path) -> dict[str, dict[str, str]]:
    """Each kept case's assignment from the work logs: `kind` (scam or ordinary) and the seed's
    `auth_result` (facts only, never text)."""
    work = gen.Work(root)
    seeds = {s["id"]: s for s in work.read("seeds") if s.get("id")}
    out: dict[str, dict[str, str]] = {}
    for o in work.read("outcomes"):
        s = seeds.get(o.get("seed", ""))
        if o.get("case") and s is not None:
            out[o["case"]] = {"kind": str(s["bucket"]).split("/", 1)[0],
                              "auth": str(s.get("auth_result") or "none")}  # fmt: skip
    return out


def _detectability(root: Path, labels: Mapping[str, Mapping[str, Any]],
                   results: list[ResultFile],
                   given: dict[str, dict[str, str]] | None = None) -> list[str]:  # fmt: skip
    """How many of the emails written as scams each reader called phishing, and how the seeds'
    authentication results fall by kind (OD-544): reported, never scored."""
    given = assignments(root) if given is None else given
    scams = sorted(c for c, a in given.items() if a["kind"] == "scam")
    if not scams:
        return []

    def called(answers: Mapping[str, Mapping[str, Any]]) -> str:
        seen = [c for c in scams if c in answers and "phishing" in answers[c]]
        return f"{sum(1 for c in seen if answers[c]['phishing'] is True)}/{len(seen)}"

    lines = ["set detectability (emails written as scams answered phishing: yes; reported, not"
             " scored):"]  # fmt: skip
    if labels:
        lines.append(f"  your labels          {called(labels)}")
    for r in results:
        got = {c.id: dict(c.got) for c in r.cases if c.got}
        if grading.is_grade(r):
            seen = cast(dict[str, Any], r.summary or {}).get("grader_input")
            how = seen or "raw email, authentication results included"
            lines.append(f"  {r.pair:<20} {called(got)} (input: {how})")
        else:
            lines.append(f"  ecf {r.run_id[:8]:<16} {called(got)}")
    auth: Counter[tuple[str, str]] = Counter((a["kind"], a["auth"]) for a in given.values())
    lines.append("seeds' authentication by kind: " + ", ".join(
        f"{k} {v} {n}" for (k, v), n in sorted(auth.items())))  # fmt: skip
    return lines


KAPPA_FLOOR = 0.6  # a field the judges agree on less than this is report-only (J7)


def weak_fields(grades: list[ResultFile]) -> set[str]:
    """Fields where some pair of judges has Cohen's kappa under `KAPPA_FLOOR` (an undefined
    kappa, no variation at all, is not weak)."""
    from ecf.eval import agree as ag  # noqa: PLC0415

    got = [{c.id: dict(c.got) for c in g.cases if c.got} for g in grades]
    out: set[str] = set()
    for f in load_schema().fields:
        for a, b in combinations(got, 2):
            ids = [c for c in a if c in b and f in a[c] and f in b[c]]
            k = ag.kappa([a[c][f] for c in ids], [b[c][f] for c in ids])
            if k is not None and k < KAPPA_FLOOR:
                out.add(f)
    return out


def _scorable(root: Path, labels: dict[str, dict[str, Any]], theirs: dict[str, dict[str, Any]],
              grades: list[ResultFile], given: dict[str, dict[str, str]] | None = None,
              ) -> tuple[dict[str, dict[str, Any]], list[str]]:  # fmt: skip
    """The labels ecf is scored on when judges set some (OD-547, J7): fields the judges agree
    on only weakly are report-only, except the safety fields (the fixed items and fraud_risk,
    whose disputes you always settle; eval plan draft 21), and a judges' label contradicting the
    email's assignment (a scam not phishing, or ordinary mail phishing) is left out for phishing
    and fraud_risk, unless you settled them (`theirs`: the fields the judges set, per case)."""
    weak: set[str] = weak_fields(grades) if len(grades) > 1 else set()
    weak -= {*FIXED_ITEMS, "fraud_risk"}
    given = assignments(root) if given is None else given
    contradicted: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for c, lab in labels.items():
        keep = {f: v for f, v in lab.items() if f not in weak}
        kind = given.get(c, {}).get("kind")
        if "phishing" in theirs.get(c, {}) and ((kind == "scam") != bool(lab["phishing"])):
            contradicted.append(c)
            drop = {"phishing", "fraud_risk"} & set(theirs[c])
            keep = {f: v for f, v in keep.items() if f not in drop}
        out[c] = keep
    notes = [f"report-only (judges' kappa under {KAPPA_FLOOR}; never the safety fields): "
             + (", ".join(sorted(weak)) or "none"),
             f"judges' labels contradicting the assignment (left out for phishing and"
             f" fraud_risk): {len(contradicted)}"]  # fmt: skip
    return out, notes


def _split(labels: dict[str, dict[str, Any]], rows: list[dict[str, Any]],
           sources: dict[str, str | None]) -> tuple[dict[str, dict[str, Any]],
                                                    dict[str, dict[str, Any]]]:  # fmt: skip
    """Per case and field, who set each confirmed label (OD-547 J6, J13; draft 21): on a judged
    row only the fields you settled are yours; an older row confirmed by the judges is all
    theirs; any other row is all yours. Returns (yours, theirs), empty cases left out."""
    by_id = {r["id"]: r for r in rows}
    yours: dict[str, dict[str, Any]] = {}
    theirs: dict[str, dict[str, Any]] = {}
    for c, lab in labels.items():
        mine = label_file.yours(by_id.get(c) or {}, set(lab), sources.get(c))
        if y := {f: v for f, v in lab.items() if f in mine}:
            yours[c] = y
        if t := {f: v for f, v in lab.items() if f not in mine}:
            theirs[c] = t
    return yours, theirs


def _expect_lines(root: Path, exp: Mapping[str, Mapping[str, Any]], rows: list[dict[str, Any]],
                  runs: list[ResultFile],
                  given: dict[str, dict[str, str]] | None = None) -> list[str]:  # fmt: skip
    """What `ecf eval expect` did on a judged set (draft 21, OD-563): the label policy, the cases
    expect sent to you (a varied field decided the rule or safety, or no judge answered it
    validly), the cases with no expected rule (the
    scam ones by id), and the rules it used beside each run's."""
    from ecf.eval import expect as ex  # noqa: PLC0415 - expect reads the agreement this reports

    work = gen.Work(root)
    sent = {d["id"] for d in work.read("disputes") if d.get("reason") == ex.DECIDES}
    authors = {r["id"]: str(r.get("author", "")) for r in rows}
    undecided = sum(1 for c, e in exp.items() if genset.undecided(authors.get(c, ""), dict(e)))
    log = work.read(ex.LOG)
    errs = cast(list[dict[str, Any]], log[-1].get("errors") or []) if log else []
    skipped = sum(1 for e in errs if e.get("error") == ex.NOT_EVALUATED)
    digest = str(log[-1].get("rules_digest") or "") if log else ""
    deferring = sum(1 for r in rows if label_file.deferred(r))
    lines = [f"sender_type and priority: not scored on judged rows; {len(sent)} emails sent to"
             " you by expect (a varied field decided the rule or safety, or no judge answered it"
             " validly)",
             f"rule undecided: {undecided} (rules errors: {len(errs) - skipped}, not evaluated"
             f" by the service: {skipped})"]  # fmt: skip
    if deferring:
        lines.append(f"label policy (OD-563): {', '.join(label_file.DEFERRED)} deferred on"
                     f" {deferring} emails; where the judges disagreed and it decided nothing,"
                     " left unlabelled and unscored")  # fmt: skip
    kinds = assignments(root) if given is None else given
    last = log[-1] if log else {}
    if risky := _undecided_risky(rows, last, kinds):
        lines.append(f"undecided scam or regulator emails, {len(risky)} in all (a rules error: no"
                     f" expected rule and unconfirmed, so out of every figure until expect decides"
                     f" them): {', '.join(risky[:10])}")  # fmt: skip
    if risky := _undecided_risky(rows, last, kinds, not_evaluated=True):
        lines.append(f"scam or regulator emails the service didn't evaluate, {len(risky)} in all"
                     f" (too large to send: no expected rule, so out of the rule and safety"
                     f" figures; their labels still count): {', '.join(risky[:10])}")  # fmt: skip
    for r in runs:
        theirs = str((r.summary or {}).get("rules_digest") or "")
        flag = "" if theirs == digest else " MISMATCH: run `ecf eval expect` again, or the run"
        lines.append(f"rules: expect {digest[:12] or 'not run'} (run {r.run_id[:8]}:"
                     f" {theirs[:12] or 'none'}){flag}")  # fmt: skip
    return lines


def _judge_lines(root: Path, grades: list[ResultFile]) -> list[str]:
    """Who set the labels, and how the judges agree per field (pairwise agreement and Cohen's
    kappa; OD-547, J4, J6, J7): read with the base rates, never as accuracy."""
    from ecf.eval import agree as ag  # noqa: PLC0415

    n, by = label_file.counts(root), label_file.by_source(root)
    lines = [f"labels: {by['models']} emails set by the judges, {by['operator']} by you,"
             f" {n['cases'] - n['confirmed']} not confirmed"
             f" ({len(ag.open_disputes(root))} waiting for you to settle)"]  # fmt: skip
    lines += _shares(root)
    if len(grades) < 2:
        return lines
    got = [{c.id: dict(c.got) for c in g.cases if c.got} for g in grades]
    names = [g.pair.removeprefix("grader:") for g in grades]
    lines.append("judge agreement per field (pairs; kappa; read with each field's base rate):")
    for f in load_schema().fields:
        parts: list[str] = []
        for (i, a), (j, b) in combinations(enumerate(got), 2):
            ids = [c for c in a if c in b and f in a[c] and f in b[c]]
            if not ids:
                continue
            xs, ys = [a[c][f] for c in ids], [b[c][f] for c in ids]
            k = ag.kappa(xs, ys)
            same = sum(x == y for x, y in zip(xs, ys, strict=True))
            parts.append(f"{names[i]}~{names[j]} {same}/{len(ids)}"
                         + (f" k={k:.2f}" if k is not None else ""))  # fmt: skip
        if parts:
            lines.append(f"  {f:<20} " + "; ".join(parts))
    return lines


# the fixed items that mark a fraud or regulator case (private_individual only says who wrote it)
RISK_ITEMS = tuple(f for f in FIXED_ITEMS if f != "private_individual")


def _undecided_risky(rows: list[dict[str, Any]], last: Mapping[str, Any],
                     kinds: dict[str, dict[str, str]], *,
                     not_evaluated: bool = False) -> list[str]:  # fmt: skip
    """The emails the last expect left undecided by a rules error (unconfirmed, OD-563), or with
    `not_evaluated` those the service didn't evaluate (still confirmed), that were written as
    scams or whose labels make them a fraud or regulator case: a fixed item other than
    private_individual true (`RISK_ITEMS`), or fraud_risk medium or high."""
    from ecf.eval import expect as ex  # noqa: PLC0415 - as _expect_lines

    errs = cast(list[dict[str, Any]], last.get("errors") or [])
    ids = {str(e.get("id")) for e in errs if (e.get("error") == ex.NOT_EVALUATED) == not_evaluated}
    out: list[str] = []
    for r in rows:
        lab = _labels_of(r)
        if r["id"] in ids and (kinds.get(r["id"], {}).get("kind") == "scam"
                               or any(lab.get(f) is True for f in RISK_ITEMS)
                               or lab.get("fraud_risk") in ("medium", "high")):  # fmt: skip
            out.append(r["id"])
    return sorted(out)


def _shares(root: Path) -> list[str]:
    """Per field on the confirmed rows that defer fields (OD-563): how many labels the judges
    agreed on, how many you settled, and how many the label policy left unlabelled."""
    ok = label_file.models_ok(root)
    rows = [r for r in label_file.read(root)
            if label_file.deferred(r) and label_file.is_confirmed(r, models_ok=ok)]  # fmt: skip
    if not rows:
        return []
    lines = [f"label shares on the {len(rows)} emails under the label policy (judges agreed /"
             " you settled / left unlabelled):"]  # fmt: skip
    for f in load_schema().fields:
        if f in label_file.UNASKED:
            continue
        mine = sum(1 for r in rows if f in label_file.settled(r))
        on = sum(1 for r in rows if f in _labels_of(r))
        off = sum(1 for r in rows if f in label_file.deferred(r)) - on
        lines.append(f"  {f:<20} {on - mine} / {mine} / {max(off, 0)}")
    return lines


def _labels_of(row: dict[str, Any]) -> dict[str, Any]:
    expected = cast(dict[str, Any], row.get("expected") or {})
    return cast(dict[str, Any], expected.get("labels") or {})


def _no_self_grades(doc: dict[str, Any], grades: list[ResultFile]) -> None:
    """A grade file made by one of the set's writers (its generator, or its seed writer) is
    refused (OD-543, OD-545), even one written before that rule existed."""
    for g in grades:
        d = cast(dict[str, Any], (g.summary or {}).get("grader") or {})
        for w in models.writers(doc):
            models.check_not_self(w[1], str(d.get("model")), str(doc.get("mode")))


def _dev(root: Path, doc: dict[str, Any], results: list[ResultFile],
         lines: list[str]) -> list[str]:  # fmt: skip
    """A DEV set: plumbing, then each grader against your confirmed labels when you have labelled
    the set (the DEV grader comparison, OD-543; never rows the judges labelled, draft 21); never
    ecf's own figures."""
    out = [DEV_BANNER, *lines]
    grades = [r for r in results if grading.is_grade(r)]
    if len(grades) < len(results):
        out.append("ecf results aren't scored on a DEV set; pass only its grade files")
    judged = {r["id"] for r in label_file.read(root) if label_file.judged(r)}
    exp = {c: e for c, e in confirmed(root).items() if c not in judged}  # your labels only
    labels = {c: cast(dict[str, Any], e.get("labels") or {}) for c, e in exp.items()}
    if not grades:
        return out
    if not labels:
        out.append("no confirmed labels of yours: the grader comparison needs the set labelled"
                   f" blind (`ecf eval label --root {root} --review all --add`, then"
                   " confirm)")  # fmt: skip
        return out + (_judge_lines(root, grades) if judged else [])
    out.append(f"confirmed labels: {len(labels)} of {len(doc['cases'])} cases")
    fields = tuple(load_schema().fields)
    for g in grades:
        grading.check_grade(g, doc)
        got = {c.id: dict(c.got) for c in g.cases if c.got}
        title = (f"grader {g.pair.removeprefix('grader:')} against your labels (DEV grader"
                 " comparison, not a measurement of ecf):")  # fmt: skip
        figures = agreement(got, labels, fields)
        out += _field_lines(title, figures)
        out += _measured(root, g.run_id, labels, figures)
    return out + _detectability(root, labels, grades) + _judge_lines(root, grades)


def report(root: Path, results: list[ResultFile]) -> list[str]:  # noqa: PLR0912, PLR0915 - one block a section
    doc = gen.verify(root)
    fields = tuple(load_schema().fields)
    lines = [f"Generated set {root} ({doc['mode']}, made with ecf {doc['ecf_version']})"]
    lines += _plumbing(doc)
    _no_self_grades(doc, [r for r in results if grading.is_grade(r)])
    if doc["mode"] == "dev":
        return _dev(root, doc, results, lines)
    ids = set(doc["cases"])
    exp = confirmed(root)
    rows = label_file.read(root)
    labels = _asked(exp, rows)
    full = _asked(exp, rows, deferred=True)  # with deferred fields, for the judges' figures
    lines.append(f"confirmed labels: {len(labels)} of {len(ids)} cases")
    if not labels:
        lines.append(f"no confirmed labels yet: label blind with `ecf eval label --root {root}"
                     " --review all --add`, then confirm (`ecf eval label`)")  # fmt: skip
        return lines
    grades = [r for r in results if grading.is_grade(r)]
    runs = [r for r in results if not grading.is_grade(r)]
    for r in results:
        if not {c.id for c in r.cases} <= ids:
            raise InvalidInputError(f"result {r.run_id[:8]} has cases this set doesn't (matched"
                                    " by case id)")  # fmt: skip
    for g in grades:
        grading.check_grade(g, doc)
    shared = _families(doc, grades)
    sources = {r["id"]: label_file.source(r) for r in rows if r["id"] in labels}
    settled, theirs = _split(labels, rows, sources)
    settled_full, _ = _split(full, rows, sources)
    subset = frozenset(f for r in rows if r["id"] in labels for f in label_file.deferred(r))
    judged = bool(theirs) or any(label_file.judged(r) for r in rows if r["id"] in labels)
    graded: dict[str, dict[str, Any]] = {}
    for g in grades:  # measured first: every report states it (P8)
        got = {c.id: dict(c.got) for c in g.cases if c.got}
        graded = graded or got
        if judged:  # the judges' own labels can't measure them (OD-547, J6)
            title = (f"grader {g.pair.removeprefix('grader:')} against the labels you settled"
                     f" ({len(settled_full)} emails; never the labels the judges"
                     " set):")  # fmt: skip
            figures = agreement(got, settled_full, fields)
        else:
            title = (f"grader {g.pair.removeprefix('grader:')} against your labels (its"
                     " measured accuracy):")  # fmt: skip
            figures = agreement(got, labels, fields)
        lines += _field_lines(title, figures, subset if judged else frozenset())
        if not judged:
            lines += _measured(root, g.run_id, labels, figures)
    given = assignments(root)  # read once (the work logs)
    scored, notes = _scorable(root, labels, theirs, grades, given) if judged else (labels, [])
    lines += notes
    if judged:
        lines += _expect_lines(root, exp, rows, runs, given)
    proposals = {p["id"]: cast(dict[str, Any], p["labels"])
                 for p in gen.Work(root).read("proposals")}  # fmt: skip
    if proposals:
        lines += _field_lines("generator's label hints against your labels:",
                              agreement(proposals, labels, fields))  # fmt: skip
    for r in runs:
        lines += _family_line(doc, shared, r)
        got = {c.id: dict(c.got) for c in r.cases}
        if judged:  # three figures: judges' labels, your settled ones, both (J13)
            for part, which in (
                ("labels the judges set", theirs),
                ("labels you settled", settled),
                ("all labels", None),
            ):
                sub = scored if which is None else {
                    c: {f: v for f, v in lab.items() if f in which.get(c, {})}
                    for c, lab in scored.items() if which.get(c)}  # fmt: skip
                lines += _field_lines(
                    f"ecf result {r.run_id[:8]} ({r.pair}) against {part} ({len(sub)} emails):",
                    agreement(got, sub, fields),
                )
        else:
            lines += _field_lines(f"ecf result {r.run_id[:8]} ({r.pair}) against your labels:",
                                  agreement(got, labels, fields))  # fmt: skip
        lines += _group_lines(got, scored, fields)
        if graded:
            lines += _field_lines(f"ecf result {r.run_id[:8]} against the grader:",
                                  agreement(got, graded, fields))  # fmt: skip
        lines += [f"  {line}" for line in subset_lines(r.summary)]
        lines += _rule_lines(r, exp)
    lines += _detectability(root, labels, results, given)
    lines += _judge_lines(root, grades)
    if not runs:
        lines.append(f"no ecf result given: run `ecf eval run --root {root} --claude`, then"
                     " pass its result file")  # fmt: skip
    lines.append("noise: two runs of the same models on this set (`ecf eval compare A B --root"
                 f" {root}`) give the run-to-run spread to read these figures against, and with"
                 " judges, the comparison on the labels you settled")  # fmt: skip
    return lines


ACCURACY_LOG = "grader-accuracy"


def _family_line(doc: dict[str, Any], shared: dict[str, str], r: ResultFile) -> list[str]:
    """WAIVER or NOT A MEASUREMENT when the result's classifier shares a family with the set's
    writer or a grader; nothing otherwise."""
    fam = models.classifier_family(r.pair)
    if fam is None:
        raise InvalidInputError(f"result {r.run_id[:8]}: ecf doesn't know its classifier's"
                                f" family ({r.pair})")  # fmt: skip
    if fam in shared and shared[fam] == "generator" and doc.get("generator_family_waiver"):
        return [f"WAIVER: result {r.run_id[:8]}: Claude wrote these emails and Claude"
                " classifies them (operator decision, OD-547); read every figure with"
                " that in mind"]  # fmt: skip
    if fam in shared:
        return [f"NOT A MEASUREMENT: result {r.run_id[:8]}'s classifier is {fam}, the"
                f" family of the {shared[fam]}"]  # fmt: skip
    return []


def _families(doc: dict[str, Any], grades: list[ResultFile]) -> dict[str, str]:
    """Family -> the role(s) using it: the set's generator and each grade's grader (Model
    switches: a classifier sharing one makes the result no measurement)."""
    out: dict[str, str] = {}
    g = doc["generator"]
    out[models.family(str(g["provider"]), str(g["model"]))] = "generator"
    for r in grades:
        d = cast(dict[str, Any], (r.summary or {}).get("grader") or {})
        fam = models.family(str(d.get("provider")), str(d.get("model")))
        out[fam] = "generator and grader" if out.get(fam) == "generator" else "grader"
    return out


def _measured(root: Path, grade_id: str, labels: Mapping[str, Mapping[str, Any]],
              figures: dict[str, tuple[int, int]]) -> list[str]:  # fmt: skip
    """The grader's accuracy is measured once, on your labels as they first stood (their digest
    recorded); a later report on relabelled cases says so beside the first figure (P8)."""
    digest = hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()
    work = gen.Work(root)
    first = next((r for r in work.read(ACCURACY_LOG) if r["grade"] == grade_id), None)
    if first is None:
        kept = {f: list(v) for f, v in figures.items()}
        work.append(ACCURACY_LOG, [{"grade": grade_id, "labels_sha256": digest,
                                    "figures": kept}])  # fmt: skip
        return []
    if first["labels_sha256"] == digest:
        return []
    then = cast(dict[str, list[int]], first["figures"])
    agree, n = sum(v[0] for v in then.values()), sum(v[1] for v in then.values())
    return [f"  measured on your labels as first reported ({first['labels_sha256'][:8]}):"
            f" all fields {_figure(agree, n)}; your labels changed since, so the figure above is"
            " on the relabelled cases"]  # fmt: skip


def _rule_lines(r: ResultFile, exp: Mapping[str, Mapping[str, Any]]) -> list[str]:
    # the result file is the record: a case undecided when it ran stays out, whatever expect
    # wrote since (eval plan draft 21)
    with_rule = [c for c in r.cases if c.id in exp and exp[c.id].get("rule")
                 and not c.undecided and "rule" in c.fields]  # fmt: skip
    if not with_rule:
        return ["  rule and safety: not scored (no fraud-guard or injection cases; `ecf eval"
                " expect` fills the expected rule and safety from your labels)"]  # fmt: skip
    rule_ok = sum(c.fields.get("rule", False) for c in with_rule)
    unsafe = [c.id for c in with_rule if c.safety is False]
    return [f"  rule {_figure(rule_ok, len(with_rule))}; unsafe {len(unsafe)}"
            + (f" ({', '.join(unsafe[:5])})" if unsafe else "")]  # fmt: skip


# `ecf eval compare A B --root <set>` (J12; plan state-archive/eval-compare-settled draft 2): the
# two results paired again on the labels you settled only. The judges' agreed labels dominate the
# main comparison (94% of PROD 300's), so this is the one comparison no model labelled.
TOO_FEW = 6  # discordant pairs below which exact McNemar can't reach p < 0.05 (P9)
NOT_PER_FIELD = ("rule", "safety")  # derived from all the labels, never set by anyone


def check_settled_root(root: Path, ra: ResultFile, rb: ResultFile) -> None:
    """Refuse `--root` unless both results were scored on the set's labels as they stand now
    (P5, P10, P12)."""
    if not genset.is_generated(root):
        raise InvalidInputError(f"{root} isn't a generated set: --root is for a set the judges"
                                " labelled")  # fmt: skip
    if (root / fixed_labels.FILE).is_file():
        raise InvalidInputError(f"{root} has {fixed_labels.FILE}: its hand-set labels aren't"
                                " in the judges' records, so who set each label can't be"
                                " told")  # fmt: skip
    version, ids = label_file.set_version(root), {r["id"] for r in label_file.read(root)}
    for r in (ra, rb):
        if r.set_version != version:
            raise InvalidInputError(
                f"the set's labels changed since result {r.run_id[:8]} ({r.set_version} vs"
                f" {version}); rescore both results the same way, both with or both without"
                f" --through-policy: `ecf eval rescore <result> --synthetic {root}`"
            )
        if extra := {c.id for c in r.cases} - ids:
            raise InvalidInputError(f"result {r.run_id[:8]} has {len(extra)} cases this set"
                                    " doesn't (matched by case id)")  # fmt: skip


def settled_lines(root: Path, ra: ResultFile, rb: ResultFile,
                  subset: frozenset[str] | None = None) -> list[str]:  # fmt: skip
    """The comparison on the labels you settled, after `check_settled_root`."""
    doc = gen.verify(root)
    shared = _families(doc, [])
    rows = {r["id"]: r for r in label_file.read(root)}
    lines = [line for r in (ra, rb) for line in _family_line(doc, shared, r)]
    lines.append("On the labels you settled only. Not a gate figure: these are the emails the"
                 " judges disagreed on, the hardest in the set; read A against B here, never"
                 " against the figures above.")  # fmt: skip
    if not any(label_file.judged(r) for r in rows.values()):
        by = Counter(label_file.source(r) for r in rows.values())
        mine, theirs = by["operator"], by[label_file.MODELS]
        lines.append(f"no rows the judges labelled field by field: emails confirmed by you"
                     f" {mine}, by the judges (older rows) {theirs}")  # fmt: skip
    only: dict[str, set[str]] = {}
    for c in ra.cases:
        row = rows.get(c.id) or {}
        only[c.id] = label_file.yours(row, set(c.fields), label_file.source(row)) - set(
            NOT_PER_FIELD
        )
    every = {f: ps for f, ps in paired_fields(ra, rb, subset).items() if f not in NOT_PER_FIELD}
    mine = {f: ps for f, ps in paired_fields(ra, rb, subset, only).items()
            if f not in NOT_PER_FIELD}  # fmt: skip
    total, k = sum(map(len, every.values())), sum(map(len, mine.values()))
    if total:
        lines.append(f"field labels compared: {total}; judges agreed {total - k}, you settled"
                     f" {k} ({100 * k / total:.1f}%)")  # fmt: skip
    unscored = sum(1 for r in rows.values()
                   if set(label_file.settled(r)) & set(label_file.UNASKED))  # fmt: skip
    if unscored:
        lines.append(f"you also settled sender_type or priority on {unscored} emails: those"
                     " decide the expected rule and aren't scored per field")  # fmt: skip
    if not k:
        lines.append("no labels you settled in both results: nothing to compare on your labels")
    else:
        lines += _settled_fields(mine)
    return lines + _settled_end_to_end(ra, rb, rows, subset)


def _settled_fields(mine: dict[str, list[tuple[bool, bool]]]) -> list[str]:
    def discordant(ps: list[tuple[bool, bool]]) -> int:
        return sum(x != y for x, y in ps)

    tested = {f: ps for f, ps in mine.items() if discordant(ps) >= TOO_FEW}
    lines = ["per field on your labels (exact McNemar, Holm over the fields tested here, alpha"
             " 0.05; its own family, on cases the table above also counts; scored as each run"
             " scored them, so a field `ecf eval report` leaves out (weak judge agreement, or a"
             " label against the email's assignment) counts here):"]  # fmt: skip
    for f in holm_fields(tested):
        mark = "  significant" if f.significant else ""
        lines.append(f"  {f.field:<18} n={f.n:<4} B-only {f.b_only:<3} A-only {f.a_only:<3} "
                     f"p = {f.p_value:.3g}, Holm p = {f.p_holm:.3g}{mark}")  # fmt: skip
    for name, ps in sorted(mine.items()):
        if name not in tested:
            lines.append(f"  {name:<18} n={len(ps):<4} B-only"
                         f" {sum(y and not x for x, y in ps):<3} A-only"
                         f" {sum(x and not y for x, y in ps):<3} too few to test")  # fmt: skip
    return lines


def _settled_end_to_end(ra: ResultFile, rb: ResultFile, rows: dict[str, dict[str, Any]],
                        subset: frozenset[str] | None) -> list[str]:  # fmt: skip
    """End to end on the cases with a label of yours: counts, difference, interval and p, never
    a non-inferiority verdict (P2): `correct` there still reads the judges' other labels."""
    ids = frozenset(c for c, r in rows.items() if label_file.source(r) == "operator"
                    and (subset is None or c in subset))  # fmt: skip
    try:
        c = compare(ra, rb, ids)
    except InvalidInputError:
        return ["end to end: no emails with a label of yours in both results"]
    safety = {*FIXED_ITEMS, "fraud_risk"}  # the safety fields, as `_scorable` names them
    fixed = sum(1 for i in ids if set(label_file.settled(rows[i])) & safety
                or not label_file.judged(rows[i]))  # fmt: skip
    return [f"end to end on the emails with at least one label of yours: {len(ids)} ({fixed}"
            f" where you set a safety field such as phishing or fraud risk,"
            f" {len(ids) - fixed} only other fields; on judge-labelled emails the rest are the"
            f" judges'), {c.n} decided in both: A {c.a_correct}, B {c.b_correct}; B-A"
            f" {c.diff_points:+.1f} points (95% Newcombe"
            f" interval {c.diff_ci[0]:+.1f} to {c.diff_ci[1]:+.1f}); McNemar p ="
            f" {c.p_value:.3g}"]  # fmt: skip
