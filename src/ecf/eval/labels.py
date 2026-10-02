"""Label confirmations (SPEC §16.1; OD-229, OD-241; V1.3 step 8a).

A case's `expected` values count toward the gates only after the operator confirms them with
`ecf eval label`. The confirmation is written into the committed `labels.jsonl` (`confirmed`: the
built file's SHA-256, a SHA-256 of the expected values, and the date), so it survives a fresh
install, a second machine and `ecf destroy`. It binds both hashes: editing the card's message or
its expected labels undoes it, and `ecf eval build` keeps a confirmation only while both still
match. The service checks the same hashes when an eval run starts.
"""

from __future__ import annotations

import hashlib
import json
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


def write(root: Path, rows: list[dict[str, Any]]) -> None:
    (root / "labels.jsonl").write_text(
        "".join(json.dumps(x, sort_keys=True) + "\n" for x in rows), encoding="utf-8"
    )


def is_confirmed(row: dict[str, Any]) -> bool:
    c: dict[str, Any] = row.get("confirmed") or {}
    return c.get("sha256") == row.get("sha256") and c.get("expected") == expected_hash(
        row.get("expected") or {}
    )


def carry_over(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For `ecf eval build`: keep each confirmation whose case is unchanged."""
    before = {r["id"]: r for r in old}
    out: list[dict[str, Any]] = []
    for r in new:
        prev = before.get(r["id"])
        kept = r | {"confirmed": prev["confirmed"]} if prev and prev.get("confirmed") else r
        out.append(kept if is_confirmed(kept) else r)
    return out


def pending(root: Path) -> list[dict[str, Any]]:
    return [r for r in read(root) if not is_confirmed(r)]


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


def counts(root: Path) -> dict[str, int]:
    rows = read(root)
    return {"cases": len(rows), "confirmed": sum(is_confirmed(r) for r in rows)}
