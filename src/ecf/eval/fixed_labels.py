"""Fixed-item labels set by hand (schema doc §15, S2 decisions; operator decision 2026-10-13).

Most fixed-item labels are derived at load from a card's v2 labels (`ecf.schema.derive_labels`).
One that can't be derived (first `phishing`: plain spam isn't phishing) is set by the operator,
usually on a card review page, and kept in the committed `fixed_labels.jsonl` beside
`labels.jsonl`, never in the card files, so card text and the label confirmations don't change.

One row per card: `{"id", "sha256", "labels": {item: bool}, "confirmed": {"sha256", "labels",
"on"}}`. Like `labels.jsonl` (`ecf.eval.labels`), a confirmation binds the built message's SHA-256
and a hash of the labels: when a card's message changes, its row no longer matches the case's
`sha256` in `labels.jsonl` and its labels are ignored (`stale`) until the operator confirms them
again. Each decision is also logged in `reviews.jsonl` (kind `fixed`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval import review
from ecf.schema import FIXED_ITEMS

FILE = "fixed_labels.jsonl"
KIND = "fixed"  # the reviews.jsonl kind
# a page's answers: how each was given (one by one, or confirmed with others in one step)
HOW = frozenset({"single", "bulk"})


def labels_hash(labels: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(labels), sort_keys=True).encode()).hexdigest()


def read(root: Path) -> list[dict[str, Any]]:
    path = root / FILE
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(root: Path, rows: list[dict[str, Any]]) -> None:
    (root / FILE).write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in sorted(rows, key=lambda r: r["id"])),
        encoding="utf-8",
    )


def is_confirmed(row: Mapping[str, Any], sha256: str | None) -> bool:
    """Confirmed for the message whose SHA-256 is `sha256` (the case's, from `labels.jsonl`)."""
    c = cast(dict[str, Any], row.get("confirmed") or {})
    labels = cast(dict[str, Any], row.get("labels") or {})
    return (sha256 is not None and c.get("sha256") == sha256 == row.get("sha256")
            and c.get("labels") == labels_hash(labels))  # fmt: skip


def current(root: Path) -> tuple[dict[str, dict[str, bool]], list[str]]:
    """Each case's confirmed fixed-item labels, and the ids whose row is stale (the card's
    message changed since, or the row names no case in `labels.jsonl`)."""
    shas = {r["id"]: r.get("sha256") for r in label_file.read(root)}
    good: dict[str, dict[str, bool]] = {}
    stale: list[str] = []
    for r in read(root):
        if is_confirmed(r, shas.get(r["id"])):
            good[r["id"]] = dict(r["labels"])
        else:
            stale.append(r["id"])
    return good, sorted(stale)


def record(root: Path, item: str, answers: Mapping[str, Mapping[str, Any]], on: date) -> int:
    """Write the operator's answers for one fixed item (`{case id: {"label": bool, "how":
    "single" | "bulk"}}`), each confirmed against the case's current message, and log each in
    `reviews.jsonl`. Every id must be a case in `labels.jsonl`; nothing is written otherwise.
    Returns how many labels changed or were added."""
    if item not in FIXED_ITEMS:
        raise InvalidInputError(f"{item!r} isn't a fixed item ({', '.join(FIXED_ITEMS)})")
    cases = {r["id"]: r for r in label_file.read(root)}
    unknown = sorted(set(answers) - set(cases))
    if unknown:
        raise InvalidInputError(f"not cases in labels.jsonl: {', '.join(unknown[:5])}")
    for case_id, a in answers.items():
        if not isinstance(a.get("label"), bool) or a.get("how") not in HOW:
            raise InvalidInputError(f"{case_id}: needs label true/false and how single/bulk")
    rows = {r["id"]: r for r in read(root)}
    log: list[dict[str, Any]] = []
    for case_id, a in answers.items():
        case = cases[case_id]
        old = rows.get(case_id) or {}
        before = cast(dict[str, Any], old.get("labels") or {}).get(item)
        labels = cast(dict[str, Any], old.get("labels") or {}) | {item: a["label"]}
        rows[case_id] = {"id": case_id, "sha256": case["sha256"], "labels": labels,
                         "confirmed": {"sha256": case["sha256"], "labels": labels_hash(labels),
                                       "on": on.isoformat()}}  # fmt: skip
        log.append(review.entry(case, item, before=before, answer=a["label"], final=a["label"],
                                outcome=review.outcome(before, a["label"], a["label"]), on=on,
                                kind=KIND) | {"how": a["how"]})  # fmt: skip
    write(root, list(rows.values()))
    review.append_log(root, log)
    return sum(e["before"] != e["final"] for e in log)
