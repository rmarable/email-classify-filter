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
from ecf.eval.drafts import DraftsFile
from ecf_server import draftruns, evalrun

JUDGEMENTS_SUFFIX = ".judgements.json"
SETTLED_SUFFIX = ".settled.json"
NULL_SUFFIX = ".null.json"
CALIBRATION = "draft-calibration.json"
FORMAT = 1
NULL_TEXT = "Thank you for your message; we'll review it and get back to you."
_LOCK = threading.Lock()  # every judgements write, one at a time (J5C-1)


def _folder(data_dir: Path) -> Path:
    return draftruns.folder(data_dir)


def resolve(data_dir: Path, run: str) -> DraftsFile:
    """A draft run's record, by its ID or the first 8 characters of it."""
    if not run or not all(c in "0123456789abcdef" for c in run):
        raise InvalidInputError("name a draft run by its ID (hex)")
    found = sorted(_folder(data_dir).glob(f"{run}*{drafts.DRAFTS_SUFFIX}"))
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


def _root(data_dir: Path, run_id: str) -> Path:
    null = _read(_folder(data_dir) / f"{run_id}{NULL_SUFFIX}")
    if null:
        return Path(str(null["root"]))
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


def items(data_dir: Path, run: str) -> dict[str, Any]:
    """What the judges grade: each drafted case with the fixed prompt the service built for it
    (the email as a mail client shows it, the question, the draft, and on an impersonation card
    the identity), and its schema. A card that no longer matches its built file, or text holding
    a delimiter, is refused by name."""
    from ecf.eval import grade as grading  # noqa: PLC0415 - the labeller's view, eval only

    record = _complete(resolve(data_dir, run))
    root = _root(data_dir, record.run_id)
    cases = {c.id: c for c in _set(root, record.set_version)}
    out: list[dict[str, Any]] = []
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
        identity = None if spec.identity is None else spec.identity.name
        if draftjudge.has_delimiter(view, dc.text, spec.question, identity or ""):
            raise InvalidInputError(f"{c.id}: its email, draft, question or identity holds a"
                                    " prompt delimiter")  # fmt: skip
        out.append({"case": c.id, "group": dc.group, "identity": identity,
                    "prompt": draftjudge.prompt(view, spec.question, dc.text, identity),
                    "schema": draftjudge.schema(impersonation=identity is not None)})  # fmt: skip
    return {"run_id": record.run_id, "set_version": record.set_version,
            "prompt_digest": draftjudge.template_digest(), "items": out}  # fmt: skip


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def _write(path: Path, doc: dict[str, Any]) -> None:
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
    doc = _read(_folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}")
    return {"run_id": record.run_id, "judges": doc.get("judges", {}),
            "spent": doc.get("spent", {})}  # fmt: skip


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
    path = _folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}"
    with _LOCK:
        doc = _read(path) or {"format": FORMAT, "run_id": record.run_id, "judges": {}}
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
        _write(path, doc)
    return {"run_id": record.run_id, "judge": name, "judged": len(mine), "of": len(impersonation)}


def calibrated(data_dir: Path) -> tuple[list[str], str | None]:
    """The primary fields that passed calibration, and the calibration file's digest (none until
    Step 5c-2 writes it)."""
    path = _folder(data_dir) / CALIBRATION
    if not path.is_file():
        return [], None
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    passed = [str(f) for f in doc.get("passed", []) if f in draftjudge.SCALES]
    return passed, hashlib.sha256(path.read_bytes()).hexdigest()


def score(data_dir: Path, run: str) -> dict[str, Any]:
    """A draft run's scores: per group, and per case what is still to settle (no draft text)."""
    record = resolve(data_dir, run)
    doc = _read(_folder(data_dir) / f"{record.run_id}{JUDGEMENTS_SUFFIX}")
    judged: dict[str, dict[str, Any]] = doc.get("judges", {})
    settled = _read(_folder(data_dir) / f"{record.run_id}{SETTLED_SUFFIX}").get("cases", {})
    cal, cal_digest = calibrated(data_dir)
    scored = [draftjudge.score_case(
        c, {j: a[c.id] for j, a in judged.items() if c.id in a}, settled.get(c.id, {}), cal,
        forced=record.forced) for c in record.cases]  # fmt: skip
    return {"run_id": record.run_id, "forced": record.forced, "complete": record.complete,
            "state": record.state, "set_version": record.set_version, "judges": sorted(judged),
            "spent": doc.get("spent", {}), "prompt_digest": doc.get("prompt_digest"),
            "calibration_digest": cal_digest, "checker_digest": record.checker_digest,
            "actor_prompt_digest": record.actor_prompt_digest,
            "summary": draftjudge.summary(scored, calibrated=cal),
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
    with _LOCK:
        _write(_folder(data_dir) / f"{run_id}{NULL_SUFFIX}",
               {"root": str(root.resolve()), "set_version": version})  # fmt: skip
        drafteval.write(_folder(data_dir), record)
    return {"run_id": run_id, "cases": len(out), "drafts": sum(c.text is not None for c in out)}
