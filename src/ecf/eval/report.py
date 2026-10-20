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
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import models
from ecf.eval.metrics import wilson
from ecf.eval.results import REPORT_ONLY_BELOW, ResultFile
from ecf.schema import load_schema

DEV_BANNER = "DEV: plumbing test, not a measurement"


def group_of(case_id: str) -> str:
    return re.sub(r"-\d{3}$", "", case_id.removeprefix(gen.DEV_PREFIX))


def confirmed(root: Path) -> dict[str, dict[str, Any]]:
    """Each confirmed case's expected values (labels, rule, safety), by id."""
    return {r["id"]: r["expected"] for r in label_file.read(root) if label_file.is_confirmed(r)}


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


def _field_lines(title: str, figures: dict[str, tuple[int, int]]) -> list[str]:
    lines = [title]
    lines += [f"  {f:<20} {_figure(*v)}" for f, v in figures.items() if v[1]]
    total = (sum(v[0] for v in figures.values()), sum(v[1] for v in figures.values()))
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
                   results: list[ResultFile]) -> list[str]:  # fmt: skip
    """How many of the emails written as scams each reader called phishing, and how the seeds'
    authentication results fall by kind (OD-544): reported, never scored."""
    given = assignments(root)
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


def _no_self_grades(doc: dict[str, Any], grades: list[ResultFile]) -> None:
    """A grade file made by the set's own generator is refused (OD-543), even one written
    before that rule existed."""
    for g in grades:
        d = cast(dict[str, Any], (g.summary or {}).get("grader") or {})
        models.check_not_self(str(doc["generator"].get("model")), str(d.get("model")),
                              str(doc.get("mode")))  # fmt: skip


def _dev(root: Path, doc: dict[str, Any], results: list[ResultFile],
         lines: list[str]) -> list[str]:  # fmt: skip
    """A DEV set: plumbing, then each grader against your confirmed labels when you have labelled
    the set (the DEV grader comparison, OD-543); never ecf's own figures."""
    out = [DEV_BANNER, *lines]
    grades = [r for r in results if grading.is_grade(r)]
    if len(grades) < len(results):
        out.append("ecf results aren't scored on a DEV set; pass only its grade files")
    exp = confirmed(root)
    labels = {c: cast(dict[str, Any], e.get("labels") or {}) for c, e in exp.items()}
    if not grades:
        return out
    if not labels:
        out.append("no confirmed labels: the grader comparison needs the set labelled blind"
                   f" (`ecf eval label --root {root} --review all --add`, then"
                   " confirm)")  # fmt: skip
        return out
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
    return out + _detectability(root, labels, grades)


def report(root: Path, results: list[ResultFile]) -> list[str]:
    doc = gen.verify(root)
    fields = tuple(load_schema().fields)
    lines = [f"Generated set {root} ({doc['mode']}, made with ecf {doc['ecf_version']})"]
    lines += _plumbing(doc)
    _no_self_grades(doc, [r for r in results if grading.is_grade(r)])
    if doc["mode"] == "dev":
        return _dev(root, doc, results, lines)
    ids = set(doc["cases"])
    exp = confirmed(root)
    labels = {c: cast(dict[str, Any], e.get("labels") or {}) for c, e in exp.items()}
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
    graded: dict[str, dict[str, Any]] = {}
    for g in grades:  # measured first: every report states it (P8)
        got = {c.id: dict(c.got) for c in g.cases if c.got}
        graded = graded or got
        title = (f"grader {g.pair.removeprefix('grader:')} against your labels (its measured"
                 " accuracy):")  # fmt: skip
        figures = agreement(got, labels, fields)
        lines += _field_lines(title, figures)
        lines += _measured(root, g.run_id, labels, figures)
    proposals = {p["id"]: cast(dict[str, Any], p["labels"])
                 for p in gen.Work(root).read("proposals")}  # fmt: skip
    if proposals:
        lines += _field_lines("generator's label hints against your labels:",
                              agreement(proposals, labels, fields))  # fmt: skip
    for r in runs:
        fam = models.classifier_family(r.pair)
        if fam is None:
            raise InvalidInputError(f"result {r.run_id[:8]}: ecf doesn't know its classifier's"
                                    f" family ({r.pair})")  # fmt: skip
        if fam in shared:
            lines.append(f"NOT A MEASUREMENT: result {r.run_id[:8]}'s classifier is {fam}, the"
                         f" family of the {shared[fam]}")  # fmt: skip
        got = {c.id: dict(c.got) for c in r.cases}
        lines += _field_lines(f"ecf result {r.run_id[:8]} ({r.pair}) against your labels:",
                              agreement(got, labels, fields))  # fmt: skip
        lines += _group_lines(got, labels, fields)
        if graded:
            lines += _field_lines(f"ecf result {r.run_id[:8]} against the grader:",
                                  agreement(got, graded, fields))  # fmt: skip
        lines += _rule_lines(r, exp)
    lines += _detectability(root, labels, results)
    if not runs:
        lines.append(f"no ecf result given: run `ecf eval run --root {root} --claude`, then"
                     " pass its result file")  # fmt: skip
    lines.append("noise: two runs of the same models on this set (`ecf eval compare`) give the"
                 " run-to-run spread to read these figures against")  # fmt: skip
    return lines


ACCURACY_LOG = "grader-accuracy"


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
    with_rule = [c for c in r.cases if c.id in exp and exp[c.id].get("rule")]
    if not with_rule:
        return ["  rule and safety: not scored (no fraud-guard or injection cases; `ecf eval"
                " expect` fills the expected rule and safety from your labels)"]  # fmt: skip
    rule_ok = sum(c.fields.get("rule", False) for c in with_rule)
    unsafe = [c.id for c in with_rule if not c.safety]
    return [f"  rule {_figure(rule_ok, len(with_rule))}; unsafe {len(unsafe)}"
            + (f" ({', '.join(unsafe[:5])})" if unsafe else "")]  # fmt: skip
