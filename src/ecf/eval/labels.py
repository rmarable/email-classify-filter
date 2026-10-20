"""Label confirmations (SPEC §16.1; OD-229, OD-241; V1.3 step 8a).

A case's `expected` values count toward the gates only after the operator confirms them with
`ecf eval label`. The confirmation is written into the committed `labels.jsonl` (`confirmed`: the
built file's SHA-256, a SHA-256 of the expected values, and the date), so it survives a fresh
install, a second machine and `ecf destroy`. It binds both hashes: editing the card's message or
its expected labels undoes it, and `ecf eval build` keeps a confirmation only while both still
match. The service checks the same hashes when an eval run starts.

On a generated set, labels the judges agree on are confirmed by `ecf eval agree` with
`confirmed.by: "models"` (OD-547); those count only on a generated set (`models_ok`), never on the
synthetic set or anything built from copied cards (J8). A confirmation with no `by` is the
operator's.
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


def write(root: Path, rows: list[dict[str, Any]]) -> None:
    """Through a temporary file and a rename, so a crash never leaves a half-written index."""
    path = root / "labels.jsonl"
    tmp = root / ".labels.jsonl.tmp"
    tmp.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows), encoding="utf-8")
    os.replace(tmp, path)


def models_ok(root: Path) -> bool:
    """Whether judge-agreed confirmations count here: only on a generated set."""
    from ecf.eval import genset  # noqa: PLC0415 - genset is tiny; avoids a cycle

    return genset.is_generated(root)


def source(row: dict[str, Any]) -> str | None:
    """Who confirmed this case: `operator`, `models`, or None."""
    c: dict[str, Any] = row.get("confirmed") or {}
    if not c:
        return None
    return MODELS if c.get("by") == MODELS else "operator"


def is_confirmed(row: dict[str, Any], *, models_ok: bool = False) -> bool:
    c: dict[str, Any] = row.get("confirmed") or {}
    if c.get("by") == MODELS and not models_ok:
        return False
    return c.get("sha256") == row.get("sha256") and c.get("expected") == expected_hash(
        row.get("expected") or {}
    )


def carry_over(old: list[dict[str, Any]], new: list[dict[str, Any]], *,
               models_ok: bool = False) -> list[dict[str, Any]]:  # fmt: skip
    """For `ecf eval build`: keep each confirmation whose case is unchanged; a judges'
    confirmation is dropped unless the set is a generated one."""
    before = {r["id"]: r for r in old}
    out: list[dict[str, Any]] = []
    for r in new:
        prev = before.get(r["id"])
        kept = r | {"confirmed": prev["confirmed"]} if prev and prev.get("confirmed") else r
        out.append(kept if is_confirmed(kept, models_ok=models_ok) else r)
    return out


def pending(root: Path) -> list[dict[str, Any]]:
    ok = models_ok(root)
    return [r for r in read(root) if not is_confirmed(r, models_ok=ok)]


def confirm(root: Path, case_id: str, on: date) -> dict[str, Any]:
    rows = read(root)
    for i, r in enumerate(rows):
        if r["id"] == case_id:
            if not (root / r["file"]).exists() and not r["file"].startswith(".build/"):
                raise InvalidInputError(f"{case_id}: its .eml is missing; run `ecf eval build`")
            rows[i] = r | {"confirmed": {"sha256": r["sha256"],
                                         "expected": expected_hash(r["expected"]),
                                         "on": on.isoformat()}}  # fmt: skip
            write(root, rows)
            return rows[i]
    raise NotFoundError(f"no case {case_id!r} in labels.jsonl; run `ecf eval build`")


def confirm_many(root: Path, case_ids: list[str], on: date, by: dict[str, Any]) -> int:
    """Confirm several cases in one atomic write, with `by` added to each confirmation (the
    judges' agreement, OD-547). Returns how many were confirmed."""
    want = set(case_ids)
    rows = read(root)
    done = 0
    for i, r in enumerate(rows):
        if r["id"] in want:
            rows[i] = r | {"confirmed": {"sha256": r["sha256"],
                                         "expected": expected_hash(r["expected"]),
                                         "on": on.isoformat()} | by}  # fmt: skip
            done += 1
    write(root, rows)
    return done


def counts(root: Path) -> dict[str, int]:
    rows = read(root)
    ok = models_ok(root)
    return {"cases": len(rows), "confirmed": sum(is_confirmed(r, models_ok=ok) for r in rows)}


def by_source(root: Path) -> dict[str, int]:
    """Confirmed cases by who confirmed them: the judges (`models`) or the operator."""
    ok = models_ok(root)
    got = [source(r) for r in read(root) if is_confirmed(r, models_ok=ok)]
    return {"models": got.count(MODELS), "operator": got.count("operator")}
