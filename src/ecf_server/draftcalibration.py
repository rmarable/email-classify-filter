"""The draft judges' calibration on the service side (M8 plan §4 "Calibration first (Q4)"; Step
5c-2a): the calibration record, the operator's blind rating page, the ratings kept, and the floors
written to `draft-calibration.json`, which every draft run's scores then read.

The record is built from the baseline's complete forced pilot run (its drafted cases, picked by
`draftcalib.pick_real`) and the seeds file, mixed in one order under opaque item ids: a complete
forced draft record with a sidecar (`<id>.calib.json`) naming each item's card and whether it is
real, the seeds' key, and each card's digest and view, so the calibration stays usable through the
set's later rewrites of other cards (operator decision 2026-10-28, D1). It is built after Step 6's
rewrites, the plan's order: a real draft is picked only where its card is unchanged since the
pilot, checked card by card against the pilot's registration (L1).
Both judges grade it with `ecf eval judge-drafts`, like any run. The operator rates the same items
on a phone page that shows neither the key nor which items are seeds; its token map stays here
(K2). The key is revealed only when the floors are scored, and the ratings are frozen then (K10).
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
from pathlib import Path
from typing import Any, cast

from ecf.errors import ConflictError, InvalidInputError
from ecf.eval import draftcalib, draftjudge, drafts
from ecf.eval.drafts import DraftCase, DraftsFile
from ecf_server import draftjudging, draftruns, evalrun

RATE_FIELDS = (*draftjudge.PRIMARY, "impersonates")
RATING_PAGE_SUFFIX = ".ratingpage.json"


def build(data_dir: Path, pilot: str, seeds_doc: Any, now: str, *,  # noqa: PLR0915 - one record, its checks
          minimums: dict[tuple[str, str], int] | None = None,
          real_min: dict[str, int] | None = None,
          real_total: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The calibration record from the baseline's complete forced pilot run and the seeds file."""
    from ecf.eval import grade as grading  # noqa: PLC0415 - the labeller's view
    from ecf_server import drafteval, outbound_plan  # noqa: PLC0415

    record = draftjudging.resolve(data_dir, pilot)
    if not record.complete or not record.forced:
        raise InvalidInputError("calibration takes a complete forced pilot run of today's actor")
    reg = draftruns.load(data_dir).get(record.run_id)
    if reg is None or not reg.baseline or not reg.slot.startswith("pilot-"):
        raise InvalidInputError("calibration takes the baseline's pilot run (K15)")
    if not reg.card_digests:
        raise InvalidInputError("the pilot run's registration keeps no card digests: run the"
                                " pilot again on this version of ecf")  # fmt: skip
    root = draftjudging.root_of(data_dir, record.run_id)
    cases, version = evalrun.load(root, fraud_only=False)
    specs = {c.id: drafts.parse(c.expected) for c in cases}
    now_digests = {c.id: draftruns.card_digest(c.path.read_bytes(), c.expected) for c in cases}
    try:  # the seeds file's own faults are the caller's, named (not a server error)
        sd = draftcalib.seeds(seeds_doc, minimums=draftcalib.MINIMUMS if minimums is None
                              else minimums, signoff=draftcalib.SIGNOFF_SEEDS if minimums is None
                              else 0)  # fmt: skip
    except ValueError as exc:
        raise InvalidInputError(f"seeds file: {exc}") from exc
    # The plan's order is pilot, Step 6's rewrites, re-labelling, then calibration (L1): a real
    # draft is rated only where its card is still the one the pilot drafted for, card by card
    # against the pilot's registration (D1); a rewritten card's draft answered another email.
    # Only drafts on cards where a draft is allowed: the plan's bait, injection and benign groups
    # (a forced draft on a no-draft card is a deterministic unsafe result, never judged).
    unchanged = {k for k, d in reg.card_digests.items() if now_digests.get(k) == d}
    pool = [c for c in record.cases if c.text is not None and c.allowed]
    drafted = [{"case": c.id, "group": c.group} for c in pool if c.id in unchanged]
    left_out = sorted(c.id for c in pool if c.id not in unchanged)
    changed = len(left_out)
    try:
        real = draftcalib.pick_real(drafted, record.run_id,
                                    minimum=draftcalib.REAL_MIN if real_min is None else real_min,
                                    total=draftcalib.REAL_TOTAL if real_total is None
                                    else real_total)  # fmt: skip
    except ValueError as exc:
        raise InvalidInputError(f"{exc} ({changed} drafts left out: their card was rewritten"
                                " since the pilot)") from exc  # fmt: skip
    by = {c.id: c for c in record.cases}
    salt = secrets.token_hex(8)

    def opaque(name: str) -> str:  # an item id that says nothing about it (K14)
        return "c-" + hashlib.sha256(f"{salt}|{name}".encode()).hexdigest()[:12]

    items: list[DraftCase] = []
    cards: dict[str, str] = {}
    real_items: list[str] = []
    for cid in real:
        item = opaque(f"r|{cid}")
        items.append(by[cid].model_copy(update={"id": item}))
        cards[item] = cid
        real_items.append(item)
    key: dict[str, dict[str, str]] = {}
    for s in sd:
        spec = specs.get(s.card)
        if spec is None or not spec.allowed:
            raise InvalidInputError(f"seed {s.id}: card {s.card} isn't a draft card of this set")
        if "impersonates" in s.key and spec.identity is None:
            raise InvalidInputError(f"seed {s.id}: impersonates on a card with no identity")
        if outbound_plan.clean_draft(s.text) != s.text:  # reads as a cleaned real draft (K14)
            raise InvalidInputError(f"seed {s.id}: its text changes when cleaned like a real"
                                    " draft (trailing spaces or blank lines): write it"
                                    " clean")  # fmt: skip
        item = opaque(f"s|{s.id}")
        items.append(DraftCase(id=item, group=spec.group, kind=spec.kind, sender=spec.sender,
                               allowed=True, stage="done", text=s.text))  # fmt: skip
        cards[item], key[item] = s.card, s.key
    confirmed = {c.id: c.confirmed for c in cases}  # rewritten cards are re-labelled first (M2)
    if unconfirmed := sorted(c for c in set(cards.values()) if not confirmed.get(c)):
        raise InvalidInputError(f"card(s) {', '.join(unconfirmed)} aren't confirmed as built:"
                                " re-label them (`ecf eval label`) before calibration")  # fmt: skip
    random.Random(secrets.token_hex(8)).shuffle(items)  # noqa: S311 - an order, not a secret
    views: dict[str, dict[str, Any]] = {}
    for card in set(cards.values()):
        spec = specs[card]
        assert spec is not None  # noqa: S101 - checked above
        identity = None if spec.identity is None else spec.identity.name
        views[card] = {"view": grading.case_view(root, card), "question": spec.question,
                       "identity": identity}  # fmt: skip
    digests = {card: now_digests[card] for card in set(cards.values())}
    seeds_digest = hashlib.sha256(json.dumps([record.run_id, [s.model_dump() for s in sd]],
                                             sort_keys=True).encode()).hexdigest()  # fmt: skip
    run_id = hashlib.sha256(f"calib|{seeds_digest}|{salt}".encode()).hexdigest()[:32]
    calib = DraftsFile(run_id=run_id, set_version=version, created_at=now, forced=True,
                       complete=True, state="done", models=record.models,
                       actor_prompt_digest=record.actor_prompt_digest,
                       checker_digest=record.checker_digest, cases=items)  # fmt: skip
    folder = draftjudging.folder(data_dir)
    draftjudging.write_json(folder / f"{run_id}{draftjudging.CALIB_SUFFIX}",
                            {"root": str(root), "pilot": record.run_id, "cards": cards,
                             "real": real_items, "key": key, "card_digests": digests,
                             "left_out_changed": left_out,
                             "views": views, "seeds_digest": seeds_digest})  # fmt: skip
    drafteval.write(folder, calib)
    return {
        "run_id": run_id,
        "real": len(real),
        "seeds": len(sd),
        "items": len(items),
        "left_out_changed": changed,
    }


def _side(data_dir: Path, run: str) -> tuple[DraftsFile, dict[str, Any]]:
    record = draftjudging.resolve(data_dir, run)
    side = draftjudging.read_json(draftjudging.folder(data_dir)
                                  / f"{record.run_id}{draftjudging.CALIB_SUFFIX}")  # fmt: skip
    if not side:
        raise InvalidInputError(f"run {record.run_id[:8]} isn't a calibration record")
    return record, side


def _ratings_path(data_dir: Path, run_id: str) -> Path:
    return draftjudging.folder(data_dir) / f"{run_id}{draftjudging.RATINGS_SUFFIX}"


def export(data_dir: Path, run: str) -> dict[str, Any]:
    """The rating page's data: each item's email view, question, identity and draft, shuffled,
    under a token, never its id, card, key or whether it is a seed; the token map stays here, and
    the page id names it (K2)."""
    record, _side_doc = _side(data_dir, run)
    got = draftjudging.views(data_dir, record.run_id)
    rows = list(got["items"])
    random.Random(secrets.token_hex(8)).shuffle(rows)  # noqa: S311 - an order, not a secret
    page: list[dict[str, Any]] = []
    tokens: dict[str, str] = {}
    for it in rows:
        t = secrets.token_hex(8)
        tokens[t] = it["case"]
        fields = [f for f in RATE_FIELDS if f != "impersonates" or it["identity"] is not None]
        page.append({"t": t, "view": it["view"], "question": it["question"],
                     "identity": it["identity"], "draft": it["draft"], "fields": fields,
                     "scales": {f: list(draftjudge.SCALES[f]) for f in fields}})  # fmt: skip
    page_id = secrets.token_hex(8)
    draftjudging.write_json(draftjudging.folder(data_dir)
                            / f"{record.run_id}.{page_id}{RATING_PAGE_SUFFIX}",
                            {"tokens": tokens})  # fmt: skip
    return {"page": page_id, "rubric": draftjudge.RUBRIC, "items": page}


def rate(data_dir: Path, run: str, page: str, answers: dict[str, Any]) -> dict[str, Any]:
    """The operator's ratings from a rating page (`{token: {field: value}}`), mapped through the
    service's token map; refused once the floors are scored (K10); an item not fully rated is
    left for later."""
    record, _side_doc = _side(data_dir, run)
    if not page or not all(c in "0123456789abcdef" for c in page):
        raise InvalidInputError("name the rating page by its id")
    tokens: dict[str, str] = draftjudging.read_json(
        draftjudging.folder(data_dir) / f"{record.run_id}.{page}{RATING_PAGE_SUFFIX}").get(
        "tokens", {})  # fmt: skip
    if not tokens:
        raise InvalidInputError(f"no rating page {page} for this calibration")
    kinds = {c.id: c.kind for c in record.cases}
    path = _ratings_path(data_dir, record.run_id)
    with draftjudging.LOCK:
        doc = draftjudging.read_json(path) or {"run_id": record.run_id, "ratings": {}}
        if doc.get("scored"):
            raise ConflictError("the floors were scored and the key revealed: the ratings are"
                                " frozen")  # fmt: skip
        done = left = 0
        for t, a in answers.items():
            item = tokens.get(t)
            if item is None or not isinstance(a, dict):
                raise InvalidInputError(f"token {t!r} isn't on this page")
            got = cast(dict[str, Any], a)
            need = [f for f in RATE_FIELDS if f != "impersonates" or kinds[item] == "impersonate"]
            if any(got.get(f) is None for f in need):
                left += 1
                continue
            for f in need:
                if got[f] not in draftjudge.SCALES[f]:
                    raise InvalidInputError(f"{f}: {got[f]!r} isn't on the scale")
            doc["ratings"][item] = {f: got[f] for f in need}
            done += 1
        draftjudging.write_json(path, doc)
    return {"rated": done, "left": left, "of": len(kinds), "total_rated": len(doc["ratings"])}


def score(data_dir: Path, run: str, null_run: str, reason: str | None = None) -> dict[str, Any]:
    """The floors, from both judges' complete grades, the operator's complete ratings, and the
    null floor both judges graded (K8, K9); written to a kept file named by its digest and to the
    current pointer, with the judge prompt digest and the judges (K10). Once the set is
    confirmed, a new calibration needs a recorded reason. The key revealed, the ratings frozen."""
    record, side = _side(data_dir, run)
    folder = draftjudging.folder(data_dir)
    ratings_doc = draftjudging.read_json(_ratings_path(data_dir, record.run_id))
    ratings: dict[str, dict[str, Any]] = ratings_doc.get("ratings", {})
    ids = {c.id for c in record.cases}
    if missing := sorted(ids - set(ratings)):
        raise InvalidInputError(f"{len(missing)} items aren't rated yet: finish the page first")
    by_judge: dict[str, dict[str, Any]] = draftjudging.judgements(data_dir, record.run_id)[
        "judges"]  # fmt: skip
    for j in draftjudge.JUDGES:
        if short := len(ids - set(by_judge.get(j, {}))):
            raise InvalidInputError(f"{j} hasn't graded {short} items: calibration needs every"
                                    " item graded by both judges")  # fmt: skip
    null_id = draftjudging.resolve(data_dir, null_run).run_id
    if not draftjudging.read_json(folder / f"{null_id}{draftjudging.NULL_SUFFIX}"):
        raise InvalidInputError(f"run {null_id[:8]} isn't the null floor")
    null_cases = {c.id for c in draftjudging.resolve(data_dir, null_id).cases if c.text}
    nj: dict[str, dict[str, Any]] = draftjudging.judgements(data_dir, null_id)["judges"]
    for j in draftjudge.JUDGES:
        if null_cases - set(nj.get(j, {})):
            raise InvalidInputError(f"{j} hasn't graded every null-floor draft")
    # one specific: yes on any card fails specific (a stated reading of the plan's rule)
    null_specific = {j: any(a.get("specific") == "yes" for a in nj[j].values())
                     for j in draftjudge.JUDGES}  # fmt: skip
    current = folder / draftjudging.CALIBRATION
    confirmed = draftruns.load(data_dir).confirmation is not None
    if current.is_file() and confirmed and not (reason or "").strip():
        raise InvalidInputError("the set is confirmed and a calibration is in force: a new one"
                                " needs a recorded reason (--reason)")  # fmt: skip
    result = draftcalib.calibrate(by_judge, ratings, side["real"], side["key"], null_specific)
    judged_doc = draftjudging.read_json(folder / f"{record.run_id}{draftjudging.JUDGEMENTS_SUFFIX}")
    result |= {"calibration_run": record.run_id, "pilot": side["pilot"],
               "left_out_changed": side.get("left_out_changed", []),
               "seeds_digest": side["seeds_digest"], "null_run": null_id,
               "prompt_digest": judged_doc.get("prompt_digest"), "reason": reason}  # fmt: skip
    digest = hashlib.sha256(json.dumps(result, indent=1, sort_keys=True).encode()).hexdigest()
    with draftjudging.LOCK:
        draftjudging.write_json(folder / f"draft-calibration-{digest}.json", result)
        draftjudging.write_json(current, result)
        draftjudging.write_json(_ratings_path(data_dir, record.run_id),
                                ratings_doc | {"scored": True})  # fmt: skip
    return result | {"digest": digest}
