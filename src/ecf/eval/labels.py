"""Label confirmations (SPEC §16.1; OD-229, OD-241; V1.3 step 8a).

A case's `expected` values count toward the gates only after the operator confirms them with
`ecf eval label`. The confirmation is written into the committed `labels.jsonl` (`confirmed`: the
built file's SHA-256, a SHA-256 of the expected values, and the date), so it survives a fresh
install, a second machine and `ecf destroy`. It binds both hashes: editing the card's message or
its expected labels undoes it, and `ecf eval build` keeps a confirmation only while both still
match. The service checks the same hashes when an eval run starts.

On a generated set, a row whose labels the judges set carries `labelled_by` (eval plan draft 21,
OD-549): the judges, the fields the operator settled, and a SHA-256 of the labels at its
last confirmation. Such rows count only on a generated set (`models_ok`), never on the synthetic
set or anything built from copied cards (J8), and only `ecf eval agree`, `settle` and `expect`
confirm them. A row without it is the operator's.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError, NotFoundError


def expected_hash(expected: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()


def read(root: Path) -> list[dict[str, Any]]:
    path = root / "labels.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


MODELS = "models"  # `confirmed.by` of a label the judges agreed on (OD-547)
# A judge-labelled row's provenance key (eval plan draft 21, OD-549): on such a row
# `sender_type` and `priority` are never scored, in ecf's figures or the judges'
LABELLED_BY = "labelled_by"
UNASKED = ("sender_type", "priority")
MAX_VARIANTS = 9  # combinations per case: at most 3 judges (OD-547), 3 values for each field


def write(root: Path, rows: list[dict[str, Any]]) -> None:
    """Through a temporary file and a rename, so a crash never leaves a half-written index."""
    path = root / "labels.jsonl"
    tmp = root / ".labels.jsonl.tmp"
    tmp.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows), encoding="utf-8")
    os.replace(tmp, path)


def judged(row: dict[str, Any]) -> bool:
    """A row whose labels the judges set (`labelled_by`), whatever its confirmation."""
    return bool(row.get(LABELLED_BY))


def settled(row: dict[str, Any]) -> list[str]:
    """The fields the operator settled on a judged row (`labelled_by.settled`)."""
    lb: dict[str, Any] = row.get(LABELLED_BY) or {}
    items: list[Any] = lb.get("settled") or []
    return [str(f) for f in items]


def models_ok(root: Path) -> bool:
    """Whether judge-agreed confirmations count here: only on a generated set."""
    from ecf.eval import genset  # noqa: PLC0415 - genset is tiny; avoids a cycle

    return genset.is_generated(root)


def labels_hash(row: dict[str, Any]) -> str:
    """A SHA-256 of the row's expected labels (`labelled_by.labels_sha256`)."""
    expected: dict[str, Any] = row.get("expected") or {}
    return hash_labels(expected.get("labels") or {})


def hash_labels(labels: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()


def edited(row: dict[str, Any], card_labels: dict[str, Any]) -> bool:
    """A judged row whose labels (in labels.jsonl, or on its card even before a rebuild) differ
    from those at its last agree or confirmation: a hand edit."""
    lb: dict[str, Any] = row.get(LABELLED_BY) or {}
    want = lb.get("labels_sha256")
    if want is None:
        return False
    return want != labels_hash(row) or want != hash_labels(card_labels)


def source(row: dict[str, Any]) -> str | None:
    """Who confirmed this case: `operator`, `models`, or None. A judged row is the operator's
    once they settled an asked field (a disputed one, or every field of a whole case);
    answering only sender_type or priority leaves it the judges'."""
    c: dict[str, Any] = row.get("confirmed") or {}
    if not c:
        return None
    if judged(row):
        return "operator" if any(f not in UNASKED for f in settled(row)) else MODELS
    return MODELS if c.get("by") == MODELS else "operator"


def is_confirmed(row: dict[str, Any], *, models_ok: bool = False) -> bool:
    c: dict[str, Any] = row.get("confirmed") or {}
    if (judged(row) or c.get("by") == MODELS) and not models_ok:
        return False
    return c.get("sha256") == row.get("sha256") and c.get("expected") == expected_hash(
        row.get("expected") or {}
    )


def carry_over(old: list[dict[str, Any]], new: list[dict[str, Any]], *,
               models_ok: bool = False) -> list[dict[str, Any]]:  # fmt: skip
    """For `ecf eval build`: keep each confirmation whose case is unchanged; on a generated
    set a row's `labelled_by` is kept whatever its hashes (`expect` re-confirms it); elsewhere it
    goes with its confirmation (J8)."""
    before = {r["id"]: r for r in old}
    out: list[dict[str, Any]] = []
    for fresh in new:
        prev = before.get(fresh["id"]) or {}
        if judged(prev) and not models_ok:
            out.append(fresh)  # a judges' row never counts outside a generated set (J8)
            continue
        r = fresh | {LABELLED_BY: prev[LABELLED_BY]} if judged(prev) else fresh
        kept = r | {"confirmed": prev["confirmed"]} if prev.get("confirmed") else r
        out.append(kept if is_confirmed(kept, models_ok=models_ok) else r)
    return out


def pending(root: Path) -> list[dict[str, Any]]:
    ok = models_ok(root)
    return [r for r in read(root) if not is_confirmed(r, models_ok=ok)]


def confirm(root: Path, case_id: str, on: date) -> dict[str, Any]:
    rows = read(root)
    for i, r in enumerate(rows):
        if r["id"] == case_id:
            refuse_judged(root, [r])
            if not (root / r["file"]).exists() and not r["file"].startswith(".build/"):
                raise InvalidInputError(f"{case_id}: its .eml is missing; run `ecf eval build`")
            rows[i] = r | {"confirmed": {"sha256": r["sha256"],
                                         "expected": expected_hash(r["expected"]),
                                         "on": on.isoformat()}}  # fmt: skip
            write(root, rows)
            return rows[i]
    raise NotFoundError(f"no case {case_id!r} in labels.jsonl; run `ecf eval build`")


def confirm_many(root: Path, provenance: dict[str, dict[str, Any]], on: date) -> int:
    """Confirm several judged cases in one atomic write, each with its `labelled_by` (judges,
    settled fields) and the hash of its labels now. Returns how many were confirmed."""
    rows = read(root)
    done = 0
    for i, r in enumerate(rows):
        if (lb := provenance.get(r["id"])) is not None:
            rows[i] = r | {"confirmed": {"sha256": r["sha256"],
                                         "expected": expected_hash(r["expected"]),
                                         "on": on.isoformat()},
                           LABELLED_BY: lb | {"labels_sha256": labels_hash(r)}}  # fmt: skip
            done += 1
    write(root, rows)
    return done


def refuse_judged(root: Path, rows: list[dict[str, Any]]) -> None:
    """`ecf eval label` never confirms or rewrites a row the judges labelled on a generated set."""
    hit = [r["id"] for r in rows if judged(r)]
    if hit and models_ok(root):
        raise InvalidInputError(
            f"{len(hit)} of these emails were labelled by the judges ({hit[0]}...): settle"
            " their disagreements with `ecf eval settle`, then run `ecf eval expect`; after a"
            " hand edit, run `ecf eval agree` again first (or revert the edit)"
        )


def counts(root: Path) -> dict[str, int]:
    rows = read(root)
    ok = models_ok(root)
    return {"cases": len(rows), "confirmed": sum(is_confirmed(r, models_ok=ok) for r in rows)}


def by_source(root: Path) -> dict[str, int]:
    """Confirmed cases by who confirmed them: the judges (`models`) or the operator."""
    ok = models_ok(root)
    got = [source(r) for r in read(root) if is_confirmed(r, models_ok=ok)]
    return {"models": got.count(MODELS), "operator": got.count("operator")}
