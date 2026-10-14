"""Blind review of the synthetic set's labels (`ecf eval label --review`; SPEC §16.1; classifier
plan 0.2).

The operator answers the chosen fields of each card from the email alone: its headers as a mail
client shows them, its attachment names and its text. The card's id, title, notes and expected
values, and any model answer, stay hidden until every field is answered; an answer that differs
from the card's label is then shown beside it and the operator picks one. Each answer is appended
to the committed `reviews.jsonl`, bound to the built file's SHA-256, so a sitting can stop and
resume, a changed message is reviewed again, and the plan's rescore can count labels that moved
apart from labels newly added. A changed label is written into the card's one-line `labels:` map;
the caller rebuilds the set and confirms the card once the rest of its expected values are checked.
"""

from __future__ import annotations

import fnmatch
import json
import random
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval.cards import Card, parse_card
from ecf.schema import (
    NOT_DERIVED_LABELS,
    REMOVED_BY_FIELD,
    CompiledSchema,
    derive_labels,
    load_schema,
)

LOG = "reviews.jsonl"
REVIEW, RECHECK = "review", "recheck"
# Outcomes: the answer matched the label; it differed and the answer replaced the label; it
# differed and the label was kept; the field had no label and the answer became one; it had no
# label and stays without one (--add, answered "-").
CONFIRMED, MOVED, KEPT, ADDED, UNLABELLED = "confirmed", "moved", "kept", "added", "unlabelled"
OUTCOMES = (CONFIRMED, MOVED, KEPT, ADDED, UNLABELLED)
_LABELS_LINE = re.compile(r"^(  labels: )\{.*\}[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Task:
    """One card to review: its built row and the fields to ask, in schema order."""

    card: Card
    row: dict[str, Any]
    fields: tuple[str, ...]


def read_log(root: Path) -> list[dict[str, Any]]:
    path = root / LOG
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_log(root: Path, rows: list[dict[str, Any]]) -> None:
    with (root / LOG).open("a", encoding="utf-8") as f:
        f.writelines(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def entry(row: dict[str, Any], field: str, *, before: Any, answer: Any, final: Any,
          outcome: str, on: date, kind: str = REVIEW) -> dict[str, Any]:  # fmt: skip
    return {"id": row["id"], "field": field, "sha256": row["sha256"], "before": before,
            "answer": answer, "final": final, "outcome": outcome, "on": on.isoformat(),
            "kind": kind}  # fmt: skip


def outcome(before: Any, answer: Any, final: Any) -> str:
    if before is None:
        return UNLABELLED if final is None else ADDED
    if answer == before:
        return CONFIRMED
    return MOVED if final == answer else KEPT


def fields_for(spec: str, schema: CompiledSchema) -> tuple[str, ...] | None:
    """The fields `--review` names, in schema order; None for `all` (each card's labelled ones)."""
    if spec.strip() == "all":
        return None
    asked = [x.strip() for x in spec.split(",") if x.strip()]
    if unknown := sorted(set(asked) - schema.fields.keys()):
        raise InvalidInputError(f"not a schema field: {', '.join(unknown)}"
                                f" (fields: {', '.join(schema.fields)})")  # fmt: skip
    if not asked:
        raise InvalidInputError("--review needs field names, or all")
    return tuple(f for f in schema.fields if f in asked)


def _matches(case_id: str, globs: tuple[str, ...]) -> bool:
    return not globs or any(fnmatch.fnmatchcase(case_id, g) for g in globs)


def _done(log: list[dict[str, Any]], kind: str) -> set[tuple[str, str, str]]:
    return {(x["id"], x["field"], x["sha256"]) for x in log if x.get("kind") == kind}


def tasks(cards: list[Card], rows: list[dict[str, Any]], schema: CompiledSchema,
          fields: tuple[str, ...] | None, *, add: bool, globs: tuple[str, ...] = (),
          redo: bool = False, log: list[dict[str, Any]] | None = None) -> list[Task]:  # fmt: skip
    """Each built card with something to ask: the chosen fields it labels (with `add`, also the
    ones it leaves unlabelled), less those already reviewed on this built file unless `redo`."""
    by_id = {c.id: c for c in cards}
    done = _done([] if redo else log or [], REVIEW)
    out: list[Task] = []
    for row in rows:
        card = by_id.get(row["id"])
        if card is None or not _matches(row["id"], globs):
            continue
        labelled = card.expected.labels
        wanted = [f for f in (fields or tuple(schema.fields))
                  if (f in labelled or add)
                  and (row["id"], f, row["sha256"]) not in done]  # fmt: skip
        if wanted:
            out.append(Task(card, row, tuple(wanted)))
    return out


def recheck_tasks(cards: list[Card], rows: list[dict[str, Any]], log: list[dict[str, Any]],
                  n: int, *, seed: int = 0, globs: tuple[str, ...] = ()) -> list[Task]:  # fmt: skip
    """A seeded sample of `n` reviewed cards not rechecked yet, each with the fields reviewed on
    its current built file, to ask again (a self-consistency figure; nothing on the card moves)."""
    current = {r["id"]: r for r in rows}
    reviewed: dict[str, set[str]] = {}
    for x in log:
        row = current.get(x["id"])
        if x.get("kind") == REVIEW and row and x["sha256"] == row["sha256"] \
                and x["outcome"] != UNLABELLED and _matches(x["id"], globs):  # fmt: skip
            reviewed.setdefault(x["id"], set()).add(x["field"])
    rechecked = {x["id"] for x in log if x.get("kind") == RECHECK
                 and x["sha256"] == current.get(x["id"], {}).get("sha256")}  # fmt: skip
    by_id = {c.id: c for c in cards}
    pool = sorted(i for i in reviewed if i not in rechecked and i in by_id)
    picked = random.Random(seed).sample(  # noqa: S311 - a sample, not a secret
        pool, min(n, len(pool))
    )
    return [Task(by_id[i], current[i], tuple(f for f in by_id[i].expected.labels
                                             if f in reviewed[i])) for i in picked]  # fmt: skip


def latest_final(log: list[dict[str, Any]], case_id: str, field: str) -> Any:
    """The value the last review of this field settled on."""
    finals = [x["final"] for x in log if x.get("kind") == REVIEW
              and x["id"] == case_id and x["field"] == field]  # fmt: skip
    return finals[-1] if finals else None


def _yaml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", v):
        raise InvalidInputError(f"can't write {v!r} into a card's labels line")
    return v


def set_labels(path: Path, new: dict[str, Any]) -> None:
    """Rewrite the card's one-line `labels:` map, keeping every other line; checks the card
    reads back with exactly these labels."""
    text = path.read_text(encoding="utf-8")
    lines = _LABELS_LINE.findall(text)
    if len(lines) != 1:
        raise InvalidInputError(f"{path.name}: expected one `  labels: {{...}}` line;"
                                " edit it by hand")  # fmt: skip
    body = ", ".join(f"{k}: {_yaml_value(v)}" for k, v in new.items())
    changed = _LABELS_LINE.sub(lambda m: f"{m.group(1)}{{{body}}}", text, count=1)
    if parse_card(changed, source=path.name).expected.labels != new:
        raise InvalidInputError(f"{path.name}: the labels didn't read back as written")
    path.write_text(changed, encoding="utf-8")


def merged(old: dict[str, Any], finals: dict[str, Any]) -> dict[str, Any]:
    """The card's labels after review: its order kept, changed values in place, new ones last,
    a field settled on None dropped."""
    out = {k: finals.get(k, v) for k, v in old.items()}
    out |= {k: v for k, v in finals.items() if k not in old}
    return {k: v for k, v in out.items() if v is not None}


def summary(log: list[dict[str, Any]], since: int = 0) -> list[str]:
    """Per field: review outcomes (this sitting, then all) and recheck agreement."""
    lines: list[str] = []
    for label, part in (("this sitting", log[since:]), ("all reviews", log)):
        counts: dict[str, Counter[str]] = {}
        for x in part:
            if x.get("kind") == REVIEW:
                counts.setdefault(x["field"], Counter())[x["outcome"]] += 1
        if counts:
            lines.append(f"{label}:")
            lines += [f"  {f}: " + ", ".join(f"{o} {c[o]}" for o in OUTCOMES if c[o])
                      for f, c in counts.items()]  # fmt: skip
    agree: dict[str, list[bool]] = {}
    for x in log:
        if x.get("kind") == RECHECK:
            agree.setdefault(x["field"], []).append(x["answer"] == x["before"])
    if agree:
        lines.append("rechecks (your answer again vs your review's):")
        lines += [f"  {f}: {sum(a)} of {len(a)} the same" for f, a in agree.items()]
    return lines


# A card review page's answers (S3b, OD-500): the card fields a page may set, and writing its
# answers into the cards' labels, keeping the fixed-item labels a replaced value implied.

# Card fields a page's answers can set (`ecf eval label --import-fixed`, beside the fixed items):
# the two whose values schema v3 removed (P11), and `fraud_risk` (a new card's blind labels)
CARD_FIELDS = ("category", "sender_type", "fraud_risk")
PAGE = "page"  # the reviews.jsonl kind of a card review page's answer to a card field
HOW = frozenset({"single", "bulk"})


def kept_items(field: str, old: dict[str, Any], new: dict[str, Any]) -> dict[str, bool]:
    """When a page replaces a removed value (S3b), the fixed-item labels the card's old labels
    implied (`derive_labels`) that its new labels no longer imply the same way: kept on the card
    as stated labels, so the relabel moves no item label (`category: vendor_change_request` to
    `invoice` keeps `bank_detail_change: true` rather than deriving it false; `sender_type: team`
    to `unknown` keeps `claims_internal: true` and the items `team` made false). `phishing` is
    never kept (`NOT_DERIVED_LABELS`): the operator split `spam_or_phishing` by hand, in
    `fixed_labels.jsonl`. Items the card states itself stay as they are."""
    if old.get(field) not in REMOVED_BY_FIELD.get(field, frozenset()) or new == old:
        return {}
    before, after = derive_labels(old), derive_labels(new)
    return {
        k: v
        for k, v in before.items()
        if k not in NOT_DERIVED_LABELS and k not in old and after.get(k) != v
    }


def record_page(root: Path, field: str, answers: dict[str, Any], on: date,
                rebuild: Callable[[Path], None]) -> dict[str, Any]:  # fmt: skip
    """Write a card review page's answers for one card field (`{case id: {"label": value, "how":
    "single" | "bulk"}}`) into each card's `labels:` line, with the fixed items a replaced
    removed value implied (`kept_items`); rebuild the set once (`rebuild(root)`); log each
    answer in `reviews.jsonl` (kind `page`, with how); and confirm again, as the operator's,
    each card that was confirmed before (the answers are the operator's own; the rest of its
    expected values were confirmed already). A card that wasn't confirmed stays pending: its
    rule, safety and other labels need `ecf eval label <id>`. Every id must be a case and every
    value one of the field's values in the shipped schema; nothing is written otherwise."""
    spec = load_schema().fields.get(field)
    if field not in CARD_FIELDS or spec is None:
        raise InvalidInputError(f"{field!r} isn't a card field a page sets"
                                f" ({', '.join(CARD_FIELDS)})")  # fmt: skip
    rows = {r["id"]: r for r in label_file.read(root)}
    unknown = sorted(set(answers) - set(rows))
    if unknown:
        raise InvalidInputError(f"not cases in labels.jsonl: {', '.join(unknown[:5])}")
    for case_id, given in answers.items():
        a: dict[str, Any] = cast(dict[str, Any], given) if isinstance(given, dict) else {}
        if a.get("how") not in HOW:
            raise InvalidInputError(f"{case_id}: needs label and how single/bulk")
        if _str(a.get("label")) not in spec.values:
            raise InvalidInputError(f"{case_id}: {a.get('label')!r} isn't a {field} value")
    paths = {parse_card(p.read_text(encoding="utf-8"), source=p.name).id: p
             for p in sorted((root / "cases").glob("*.md"))}  # fmt: skip
    missing = sorted(set(answers) - set(paths))
    if missing:
        raise InvalidInputError(f"no card file for: {', '.join(missing[:5])}")
    was_confirmed = {i for i in answers if label_file.is_confirmed(rows[i])}
    log: list[dict[str, Any]] = []
    changed: list[str] = []
    for case_id, a in answers.items():
        path = paths[case_id]
        old = parse_card(path.read_text(encoding="utf-8"), source=path.name).expected.labels
        before = old.get(field)
        keep = kept_items(field, old, merged(old, {field: a["label"]}))
        new = merged(old, {field: a["label"]} | keep)
        if new != old:
            set_labels(path, new)
            changed.append(case_id)
        log.append(entry(rows[case_id], field, before=before, answer=a["label"],
                         final=a["label"], outcome=outcome(before, a["label"], a["label"]),
                         on=on, kind=PAGE) | {"how": a["how"]}
                   | ({"kept_items": sorted(keep)} if keep else {}))  # fmt: skip
    if changed:
        rebuild(root)
    append_log(root, log)
    reconfirmed = [i for i in sorted(was_confirmed)
                   if not label_file.is_confirmed(_row(root, i))]  # fmt: skip
    for case_id in reconfirmed:
        label_file.confirm(root, case_id, on)
    return {"answers": len(answers), "changed": len(changed), "reconfirmed": len(reconfirmed),
            "pending": sorted(set(answers) - was_confirmed),
            "kept_items": sum(1 for e in log if e.get("kept_items"))}  # fmt: skip


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def _row(root: Path, case_id: str) -> dict[str, Any]:
    return next(r for r in label_file.read(root) if r["id"] == case_id)
