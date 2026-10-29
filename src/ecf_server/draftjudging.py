"""The draft eval's judge route (M8 plan §4 "Judges (P5)"; Step 5c): what the judges grade, where
their answers are kept, and a finished draft run's scores.

Draft text from the synthetic draft set leaves the service only through this route, to the two
plan judges (SPEC §16.2's I5 exception): Mistral, which the service calls itself, and Sol, whose
prompt the service builds and the CLI passes to codex. Only a complete run on the draft set, as the
set stands at the run's own version with every card matching its built file, is judged (diff review
J5C-2, J5C-5). Answers are kept per run in `<data dir>/evals/<run id>.judgements.json`, 0600, under
one lock, never overwritten. Settled values (`<run id>.settled.json`) and the calibration file
(`draft-calibration.json`, the fields that passed) are read when present; Step 5c-2 writes them.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError, NotFoundError
from ecf.eval import draftjudge, drafts
from ecf.eval.drafts import DraftCase, DraftsFile
from ecf_server import draftruns, evalrun

JUDGEMENTS_SUFFIX = ".judgements.json"
SETTLED_SUFFIX = ".settled.json"
NULL_SUFFIX = ".null.json"
CALIB_SUFFIX = ".calib.json"  # a calibration record's root, item -> card map, real items, key
RATINGS_SUFFIX = ".ratings.json"  # the operator's blind ratings of a calibration record
CALIBRATION = "draft-calibration.json"
FORMAT = 1
NULL_TEXT = "Thank you for your message; we'll review it and get back to you."
LOCK = threading.Lock()  # every judgements write, one at a time (J5C-1)


def folder(data_dir: Path) -> Path:
    return draftruns.folder(data_dir)


def resolve(data_dir: Path, run: str) -> DraftsFile:
    """A draft run's record, by its ID or the first 8 characters of it."""
    if not run or not all(c in "0123456789abcdef" for c in run):
        raise InvalidInputError("name a draft run by its ID (hex)")
    found = sorted(folder(data_dir).glob(f"{run}*{drafts.DRAFTS_SUFFIX}"))
    if not found:
        raise NotFoundError(f"no draft record for run {run}")
    if len(found) > 1:
        raise InvalidInputError(f"{run} names more than one run: give more of its ID")
    return DraftsFile.model_validate_json(found[0].read_text(encoding="utf-8"))


def _complete(record: DraftsFile) -> DraftsFile:
    """Only a complete run is judged: a stopped or expired one's drafts are judged with the slot's
    final run, which carries them (J5C-5)."""
    if not record.complete:
        raise InvalidInputError(f"run {record.run_id[:8]} is {record.state}, not complete: judge"
                                " its slot's final run, which carries its drafts")  # fmt: skip
    return record


def root_of(data_dir: Path, run_id: str) -> Path:
    for suffix in (NULL_SUFFIX, CALIB_SUFFIX):
        side = read_json(folder(data_dir) / f"{run_id}{suffix}")
        if side:
            return Path(str(side["root"]))
    rec = draftruns.load(data_dir).get(run_id)
    if rec is None:
        raise NotFoundError(f"run {run_id[:8]} isn't in the draft run record")
    return Path(rec.root)


def _set(root: Path, version: str) -> list[evalrun.Case]:
    """The draft set as the run saw it: refused when it has changed since, or isn't a draft set,
    so the judges see the email and question the drafter answered (J5C-2)."""
    cases, now = evalrun.load(root, fraud_only=False)
    if now != version:
        raise InvalidInputError(f"the set at {root} is {now[:16]}, not the run's {version[:16]}:"
                                " the judges grade a draft against the email it"
                                " answered")  # fmt: skip
    if not any(drafts.parse(c.expected) for c in cases):
        raise InvalidInputError(f"{root} isn't a draft set: no card has a draft block")
    return cases


def items(data_dir: Path, run: str, *, prompts: bool = True) -> dict[str, Any]:  # noqa: PLR0912 - a calibration record and a run
    """What the judges grade: each drafted case with the fixed prompt the service built for it
    (the email as a mail client shows it, the question, the draft, and on an impersonation card
    the identity), and its schema. A card that no longer matches its built file, or text holding
    a delimiter, is refused by name. A calibration record is pinned to its pilot card by card
    (operator decision 2026-10-28, D1): only its own cards must be unchanged, and the views it
    was built from are used. `prompts=False` leaves out the prompts and schemas (the pages)."""
    from ecf.eval import grade as grading  # noqa: PLC0415 - the labeller's view, eval only

    record = _complete(resolve(data_dir, run))
    calib = read_json(folder(data_dir) / f"{record.run_id}{CALIB_SUFFIX}")
    rows: list[tuple[DraftCase, str, str, str, str | None]] = []  # case, card, view, question, id
    if calib:
        now = _card_digests(Path(calib["root"]), set(calib["cards"].values()))
        for card, digest in calib["card_digests"].items():
            if now.get(card) != digest:
                raise InvalidInputError(f"{card}: changed since the pilot this calibration was"
                                        " built from")  # fmt: skip
        for dc in record.cases:
            card = calib["cards"][dc.id]
            v = calib["views"][card]
            rows.append((dc, card, v["view"], v["question"], v["identity"]))
    else:
        root = root_of(data_dir, record.run_id)
        cases = {c.id: c for c in _set(root, record.set_version)}
        for dc in record.cases:
            c = cases.get(dc.id)
            if dc.text is None or c is None:
                continue
            spec = drafts.parse(c.expected)
            if spec is None:
                continue
            if not c.confirmed:  # edited since its build, or never confirmed (J5C-2)
                raise InvalidInputError(f"{c.id}: its card doesn't match its confirmed, built file")
            view = grading.case_view(root, c.id)
            if view is None:
                continue
            rows.append((dc, c.id, view, spec.question,
                         None if spec.identity is None else spec.identity.name))  # fmt: skip
    out: list[dict[str, Any]] = []
    for dc, card, view, question, identity in rows:
        if dc.text is None:
            continue
        if draftjudge.has_delimiter(view, dc.text, question, identity or ""):
            raise InvalidInputError(f"{card}: its email, draft, question or identity holds a"
                                    " prompt delimiter")  # fmt: skip
        row = {"case": dc.id, "card": card, "group": dc.group, "identity": identity,
               "question": question, "view": view, "draft": dc.text}  # fmt: skip
        if prompts:
            row |= {"prompt": draftjudge.prompt(view, question, dc.text, identity),
                    "schema": draftjudge.schema(impersonation=identity is not None)}  # fmt: skip
        out.append(row)
    return {"run_id": record.run_id, "set_version": record.set_version,
            "prompt_digest": draftjudge.template_digest(), "items": out}  # fmt: skip


def _card_digests(root: Path, cards: set[str]) -> dict[str, str]:
    """The cards' digests as built now (their message and labels)."""
    cases, _version = evalrun.load(root, fraud_only=False)
    return {c.id: draftruns.card_digest(c.path.read_bytes(), c.expected)
            for c in cases if c.id in cards}  # fmt: skip


def views(data_dir: Path, run: str) -> dict[str, Any]:
    """The pages' view of a run's drafts: `items` without prompts and schemas."""
    return items(data_dir, run, prompts=False)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def write_json(path: Path, doc: dict[str, Any]) -> None:
    """Replace a file whole: a unique 0600 temporary file, fsynced, renamed, the folder fsynced."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(doc, indent=1, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def judgements(data_dir: Path, run: str) -> dict[str, Any]:
    record = resolve(data_dir, run)
    doc = read_json(folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}")
    return {"run_id": record.run_id, "judges": doc.get("judges", {}),
            "spent": doc.get("spent", {}), "prompt_digest": doc.get("prompt_digest")}  # fmt: skip


def save(data_dir: Path, run: str, judge: str, answers: dict[str, Any], prompt_digest: str,
         spent: float | None = None) -> dict[str, Any]:  # fmt: skip
    """A plan judge's answers for some of a run's drafts, checked and kept, and its spend so far;
    a case already answered by that judge is refused (never re-graded)."""
    name = draftjudge.judge_name(judge)
    if name is None:
        raise InvalidInputError(f"{judge}: the judges are {' and '.join(draftjudge.JUDGES)} (M8"
                                " plan P5); never Claude, which wrote the cards and"
                                " drafts")  # fmt: skip
    if prompt_digest != draftjudge.template_digest():
        raise InvalidInputError("the judge prompt changed since the items were read: read them"
                                " again")  # fmt: skip
    record = _complete(resolve(data_dir, run))
    impersonation = {c.id: c.kind == "impersonate" for c in record.cases if c.text is not None}
    path = folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}"
    with LOCK:
        doc = read_json(path) or {"format": FORMAT, "run_id": record.run_id, "judges": {}}
        if doc.get("prompt_digest", prompt_digest) != prompt_digest:
            raise InvalidInputError("this run's judgements were made with another judge prompt")
        mine: dict[str, Any] = doc["judges"].setdefault(name, {})
        for case, answer in answers.items():
            if case not in impersonation:
                raise InvalidInputError(f"{case}: no draft of this run to judge")
            if case in mine:
                raise InvalidInputError(f"{case}: already judged by {name}")
            if not isinstance(answer, dict):
                raise InvalidInputError(f"{case}: an answer is an object")
            fields = cast(dict[str, Any], answer)
            try:
                mine[case] = draftjudge.check_answer(fields, impersonation=impersonation[case])
            except ValueError as exc:
                raise InvalidInputError(f"{case}: {exc}") from exc
        if spent is not None:  # a judge's spend across resumes (J5C-7)
            book: dict[str, float] = doc.setdefault("spent", {})
            book[name] = max(float(spent), float(book.get(name, 0.0)))
        doc["prompt_digest"] = prompt_digest
        write_json(path, doc)
        done = all(set(impersonation) <= set(doc["judges"].get(j, {})) for j in draftjudge.JUDGES)
        if done:
            _pin(data_dir, record.run_id)
    return {"run_id": record.run_id, "judge": name, "judged": len(mine), "of": len(impersonation)}


def _pin(data_dir: Path, run_id: str) -> str | None:
    """Pin a run to the calibration in force, once (L3): when both judges have graded every draft,
    or at its first record. A pinned run keeps its calibration whatever is scored later, so no
    recalibration re-scores it (K10). A calibration record is never pinned (the null floor may
    be; it is harmless). Nothing is pinned to a calibration the run could never count under: one
    made under another judge prompt than the run's judgements, or one with a field both judges
    failed, which waits for the operator's decision (M1); the run then follows the next one.
    Call under LOCK."""
    path = folder(data_dir) / f"{run_id}{drafts.DRAFTS_SUFFIX}"
    record = DraftsFile.model_validate_json(path.read_text(encoding="utf-8"))
    judged = read_json(folder(data_dir) / f"{run_id}{JUDGEMENTS_SUFFIX}").get("prompt_digest")
    if record.calibration_digest is not None:
        pinned, _d = calibrated(data_dir, record.calibration_digest)
        if pinned.get("prompt_digest") in (None, judged):
            return record.calibration_digest
        # re-judged under another prompt (OD-580): that calibration can never count for it, so
        # the run follows the one in force now, as a run never pinned does (M1)
        record = record.model_copy(update={"calibration_digest": None})
        write_json(path, record.model_dump(mode="json"))
    if read_json(folder(data_dir) / f"{run_id}{CALIB_SUFFIX}"):
        return None
    doc, digest = calibrated(data_dir)
    if digest is None or judged is None or doc.get("prompt_digest") != judged \
            or doc.get("both_failed"):  # fmt: skip
        return None
    write_json(path, record.model_copy(update={"calibration_digest": digest}).model_dump(
        mode="json"))  # fmt: skip
    return digest


def calibrated(data_dir: Path, digest: str | None = None) -> tuple[dict[str, Any], str | None]:
    """The calibration (`passed`: the fields that count; `judges`: each judge's passed fields;
    `settle_all`: the strict fields a judge failed; `both_failed`; `prompt_digest`) and its
    digest: the current one, or a kept one by its digest (K10). Empty until
    `ecf eval calibration-score` writes it."""
    name = CALIBRATION if digest is None else f"draft-calibration-{digest}.json"
    path = folder(data_dir) / name
    if not path.is_file():
        return {"passed": []}, None
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    doc["passed"] = [str(f) for f in doc.get("passed", []) if f in draftjudge.SCALES]
    return doc, hashlib.sha256(path.read_bytes()).hexdigest()


def score(data_dir: Path, run: str) -> dict[str, Any]:
    """A draft run's scores: per group, and per case what is still to settle (no draft text). A
    pinned run is scored with its own calibration (K10, L3), an unpinned one with the current;
    a run judged under another judge prompt than the calibration's counts no judged field. Each
    cause of provisional scores is named (L4). Reads only."""
    record = resolve(data_dir, run)
    doc = read_json(folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}")
    judged: dict[str, dict[str, Any]] = doc.get("judges", {})
    settle_doc = read_json(folder(data_dir) / f"{record.run_id}{SETTLED_SUFFIX}")
    settled: dict[str, dict[str, str]] = settle_doc.get("cases", {})
    doc_cal: dict[str, Any]
    doc_cal, cal_digest = calibrated(data_dir, record.calibration_digest)
    warnings: list[str] = []
    why: list[str] = []  # why the scores are provisional, each cause named (L4)
    if cal_digest is None and record.calibration_digest is not None:
        why.append(f"its calibration {record.calibration_digest[:12]} isn't kept any more")
    elif cal_digest is None:
        why.append("no calibration yet, so OK reads the deterministic checks only")
    elif doc_cal.get("prompt_digest") and doc.get("prompt_digest") \
            and doc_cal["prompt_digest"] != doc["prompt_digest"]:  # fmt: skip
        warnings.append("judged under another judge prompt than the calibration's: no judged"
                        " field counts")  # fmt: skip
        why.append("judged under another judge prompt than the calibration's")
        doc_cal = {"passed": []}
    elif not doc_cal["passed"]:
        why.append("no field passed calibration, so OK reads the deterministic checks only")
    if doc_cal.get("both_failed"):
        why.append(f"both judges under the floor on {', '.join(doc_cal['both_failed'])}: waits"
                   " for your decision (M8 plan §4)")  # fmt: skip
    if doc.get("prompt_digest") and doc["prompt_digest"] != draftjudge.template_digest():
        warnings.append("judged under an older judge prompt: re-grade it (OD-580)")
        why.append("judged under an older judge prompt")
    cal: list[str] = list(doc_cal["passed"])
    judge_fields = cast(dict[str, list[str]] | None, doc_cal.get("judges"))
    settle_all = cast(dict[str, list[str]] | None, doc_cal.get("settle_all"))
    both_failed = cast(list[str], doc_cal.get("both_failed", []))
    scored = [draftjudge.score_case(
        c, {j: a[c.id] for j, a in judged.items() if c.id in a}, settled.get(c.id, {}), cal,
        forced=record.forced, judge_fields=judge_fields,
        settle_all=settle_all) for c in record.cases]  # fmt: skip
    taps: dict[str, bool] = settle_doc.get("taps", {})
    summary = draftjudge.summary(scored, calibrated=cal, both_failed=both_failed)
    summary["taps"] = {"hidden": len(taps), "tapped": sum(taps.values())}  # K13
    if summary["stated_reads"]:
        why.append(f"{summary['stated_reads']} stated-fault reads unanswered (OD-576)")
    # drafts a judge called a soft commitment that still carry a date, amount or term the checker
    # refused: listed for the operator, since soft passes (R12, S14)
    soft_terms = sorted(c.id for c in record.cases if c.checker and c.checker.refuse
                        and any(a.get(c.id, {}).get("commits") == "soft"
                                for a in judged.values()))  # fmt: skip
    summary["soft_with_terms"] = soft_terms
    summary["provisional"] = summary["provisional"] or bool(warnings)
    summary["provisional_why"] = why if summary["provisional"] else []
    return {"run_id": record.run_id, "forced": record.forced, "complete": record.complete,
            "state": record.state, "set_version": record.set_version, "judges": sorted(judged),
            "spent": doc.get("spent", {}), "prompt_digest": doc.get("prompt_digest"),
            "calibration_digest": cal_digest, "checker_digest": record.checker_digest,
            "actor_prompt_digest": record.actor_prompt_digest, "warnings": warnings,
            "summary": summary,
            "cases": [{"case": s.case, "group": s.group, "unsafe": s.unsafe, "ok": s.ok,
                       "ok_without_refusals": s.ok_without_refusals,
                       "would_draft": s.would_draft, "unsafe_pending": s.unsafe_pending,
                       "awaiting": s.awaiting, "to_settle": s.to_settle}
                      for s in scored]}  # fmt: skip


def null_floor(data_dir: Path, root: Path, now: str) -> dict[str, Any]:
    """The null floor (M8 plan §4 "Null floor", P6, Q6): no model, the fixed text wherever a draft
    is allowed (the card's `allowed` already encodes the policy, Q1), on the confirmed draft set,
    as a complete forced record the judges grade like any run's, one card at a time (a stated
    reading of "judged once": once per card, by each judge). A pipeline and judge-anchor check,
    never a gate."""
    from ecf_server import draftcheck, drafteval  # noqa: PLC0415

    cases, version = evalrun.load(root, fraud_only=False)
    specs = {c.id: drafts.parse(c.expected) for c in cases}
    if not any(specs.values()):
        raise InvalidInputError(f"{root} isn't a draft set: no card has a draft block")
    if unconfirmed := sorted(c.id for c in cases if not c.confirmed):
        raise InvalidInputError(f"{len(unconfirmed)} card(s) aren't confirmed: {unconfirmed[:5]}")
    run_id = hashlib.sha256(f"null|{version}".encode()).hexdigest()[:32]
    out: list[drafts.DraftCase] = []
    for c in cases:
        spec = specs[c.id]
        if spec is None:
            continue
        text = NULL_TEXT if spec.allowed else None
        out.append(drafts.DraftCase(
            id=c.id, group=spec.group, kind=spec.kind, sender=spec.sender, allowed=spec.allowed,
            stage="done", text=text, undecided=not spec.allowed))  # fmt: skip
    record = DraftsFile(run_id=run_id, set_version=version, created_at=now, forced=True,
                        complete=True, state="done", models={}, actor_prompt_digest="null",
                        checker_digest=draftcheck.digest(), cases=out)  # fmt: skip
    with LOCK:
        write_json(folder(data_dir) / f"{run_id}{NULL_SUFFIX}",
               {"root": str(root.resolve()), "set_version": version})  # fmt: skip
        drafteval.write(folder(data_dir), record)
    return {"run_id": run_id, "cases": len(out), "drafts": sum(c.text is not None for c in out)}


# Settling (M8 plan §4 "Settling"; Step 5c-2b): one page across the runs being compared, blind to
# the run, the arm and the judges; its token map kept here, each token bound to its run, case and
# the draft text shown (K2, K11); the operator's values kept per run, with the taps of hidden
# lines; each run's record then filled with its scores.

PAGE_PREFIX = "draft-settle-"


def settle_page(data_dir: Path, runs: list[str]) -> dict[str, Any]:
    """Each draft with something to settle, from every run named, shuffled into one page: its
    email, question, identity and draft (the name-only last line hidden behind a tap where the
    rule allows), and the fields to settle, under a token; the page's id names the service's own
    token map. Never the run, the case, the card or the judges' answers."""
    import random  # noqa: PLC0415
    import secrets  # noqa: PLC0415

    from ecf_server import draftcheck  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    specs: dict[str, dict[str, drafts.DraftSpec | None]] = {}  # per run, loaded once (diff review)
    for run in runs:
        got = views(data_dir, run)
        by = {i["case"]: i for i in got["items"]}
        for c in score(data_dir, got["run_id"])["cases"]:
            if c["to_settle"] and c["case"] in by:
                rows.append({"run": got["run_id"], "c": c, "it": by[c["case"]]})
    random.Random(secrets.token_hex(8)).shuffle(rows)  # noqa: S311 - an order, not a secret
    page: list[dict[str, Any]] = []
    tokens: dict[str, dict[str, Any]] = {}
    for r in rows:
        it, c = r["it"], r["c"]
        view = str(it["view"])

        def refuses(line: str, email: str = view) -> bool:
            return bool(draftcheck.check(line, email, draftcheck.links(email), ()).refuse)

        fields = sorted({"impersonates" if f == "impersonates_read" else f for f in c["to_settle"]})
        shown, hidden = draftjudge.hide_line(str(it["draft"]), c["to_settle"], refuses)
        marks: list[list[int]] = []
        if "stated_fault_read" in fields:
            if r["run"] not in specs:
                specs[r["run"]] = _specs(data_dir, r["run"])
            spec = specs[r["run"]].get(str(it["card"]))
            marks = [] if spec is None else pattern_marks(spec, shown)
        t = secrets.token_hex(8)
        tokens[t] = {"run": r["run"], "case": c["case"], "hidden": hidden is not None,
                     "fields": fields,
                     "sha": hashlib.sha256(str(it["draft"]).encode()).hexdigest()}  # fmt: skip
        page.append({"t": t, "view": view, "question": it["question"],
                     "identity": it["identity"], "draft": shown, "hidden": hidden,
                     "fields": fields, "marks": marks,
                     "scales": {f: list(_scale(f)) for f in fields}})  # fmt: skip
    page_id = secrets.token_hex(8)
    write_json(folder(data_dir) / f"{PAGE_PREFIX}{page_id}.json", {"tokens": tokens})
    return {"page": page_id, "rubric": draftjudge.RUBRIC, "items": page}


def _scale(f: str) -> tuple[str, ...]:
    """A settled field's values: a judge field's scale, or a settle-only key's (OD-580)."""
    return draftjudge.SCALES.get(f) or draftjudge.SETTLE_ONLY[f]


def _specs(data_dir: Path, run_id: str) -> dict[str, drafts.DraftSpec | None]:
    """The run's draft set's card blocks, by card."""
    cases = evalrun.load(root_of(data_dir, run_id), fraud_only=False)[0]
    return {c.id: drafts.parse(c.expected) for c in cases}


def pattern_marks(spec: drafts.DraftSpec, text: str) -> list[list[int]]:
    """Where the card's `forbid` and `followed` patterns match the text, every match of each,
    overlaps merged, as UTF-16 offsets (what the page's JavaScript slices by, so a character
    beyond U+FFFF before a match can't shift the highlight); found when the page is built and
    never stored (OD-580, F3)."""
    import re  # noqa: PLC0415

    patterns = [*spec.forbid, *([spec.followed_pattern] if spec.followed_pattern else [])]
    spans = sorted({(m.start(), m.end()) for p in patterns for m in re.finditer(p, text)
                    if m.end() > m.start()})  # fmt: skip
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    def utf16(i: int) -> int:
        return len(text[:i].encode("utf-16-le")) // 2

    return [[utf16(a), utf16(b)] for a, b in merged]


def settle(data_dir: Path, page: str, answers: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912 - one check per answer
    """The operator's values from a settle page (`{token: {field: value, "tapped": bool}}`),
    mapped through the service's own token map; a token whose draft changed since the page is
    refused; a draft not fully answered is left for later; a draft that now needs a field the
    page didn't ask (judged or calibrated since) is counted as stale, for a new page (L5). Taps
    are kept only for hidden lines, never overwritten."""
    if not page or not all(c in "0123456789abcdef" for c in page):
        raise InvalidInputError("name the settle page by its id")
    tokens: dict[str, dict[str, Any]] = read_json(
        folder(data_dir) / f"{PAGE_PREFIX}{page}.json").get("tokens", {})  # fmt: skip
    if not tokens:
        raise InvalidInputError(f"no settle page {page}")
    asked: dict[str, dict[str, set[str]]] = {}
    texts: dict[str, dict[str, str | None]] = {}
    for run in {v["run"] for v in tokens.values()}:
        asked[run] = {c["case"]: {"impersonates" if f == "impersonates_read" else f
                                  for f in c["to_settle"]}
                      for c in score(data_dir, run)["cases"] if c["to_settle"]}  # fmt: skip
        texts[run] = {c.id: c.text for c in resolve(data_dir, run).cases}
    done = left = stale = 0
    with LOCK:
        docs: dict[str, dict[str, Any]] = {}
        for t, a in answers.items():
            ref = tokens.get(t)
            if ref is None or not isinstance(a, dict):
                raise InvalidInputError(f"token {t!r} isn't on this page")
            run, case = str(ref["run"]), str(ref["case"])
            text = texts[run].get(case)
            if text is None or hashlib.sha256(text.encode()).hexdigest() != ref["sha"]:
                raise InvalidInputError(f"token {t!r}: its draft changed since the page")
            given = cast(dict[str, Any], a)
            need = asked[run].get(case)
            if need is None:
                continue  # settled already
            if need - set(ref.get("fields", need)):
                stale += 1
                continue
            if any(given.get(f) is None for f in need):
                left += 1
                continue
            for f in need:
                if given[f] not in _scale(f):
                    raise InvalidInputError(f"{f}: {given[f]!r} isn't on the scale")
            doc = docs.setdefault(run, read_json(folder(data_dir) / f"{run}{SETTLED_SUFFIX}")
                                  or {"run_id": run, "cases": {}, "taps": {}})  # fmt: skip
            doc["cases"].setdefault(case, {}).update({f: given[f] for f in need})
            if ref["hidden"] and case not in doc["taps"]:
                doc["taps"][case] = bool(given.get("tapped"))
            done += 1
        for run, doc in docs.items():
            write_json(folder(data_dir) / f"{run}{SETTLED_SUFFIX}", doc)
    return {"settled": done, "left": left, "stale": stale, "runs": sorted(asked)}


def record_scores(data_dir: Path, run: str) -> dict[str, Any]:
    """A complete run's draft record filled from its scores: each case's `draft_ok` (None while
    anything is still to settle) and judgements, and the judge prompt and calibration digests it
    was scored with; written whole and atomically, the calibration kept by its digest (K10, K16)."""
    run_id = _complete(resolve(data_dir, run)).run_id
    with LOCK:  # the pin, the scores and the write as one step
        _pin(data_dir, run_id)
        record = resolve(data_dir, run_id)
        s = score(data_dir, run_id)
        judged: dict[str, dict[str, Any]] = read_json(
            folder(data_dir) / f"{run_id}{JUDGEMENTS_SUFFIX}").get("judges", {})  # fmt: skip
        ok = {c["case"]: None if c["to_settle"] else c["ok"] for c in s["cases"]}
        cases = [c.model_copy(update={"draft_ok": ok.get(c.id),
                                      "judgements": {j: a[c.id] for j, a in judged.items()
                                                     if c.id in a}})
                 for c in record.cases]  # fmt: skip
        new = record.model_copy(update={"cases": cases,
                                        "judge_prompt_digest": s["prompt_digest"]})  # fmt: skip
        write_json(folder(data_dir) / f"{run_id}{drafts.DRAFTS_SUFFIX}",
                   new.model_dump(mode="json"))  # fmt: skip
    return {
        "run_id": run_id,
        "recorded": sum(v is not None for v in ok.values()),
        "to_settle": sum(bool(c["to_settle"]) for c in s["cases"]),
        "calibration_digest": record.calibration_digest,
    }
