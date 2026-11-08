"""Generated eval sets (`ecf eval generate`; planning-docs/EVAL-GENERATE-GRADE.md).

A generated set has the synthetic set's layout plus `manifest.json`. It is never the go-live
gate's evidence (plan P1): the service checks `is_generated` before a run can count for the gate
or re-point the gate's set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

MANIFEST = "manifest.json"
KIND = "ecf-generated-set"
AUTHOR = "generated"


def is_generated(root: Path) -> bool:
    """True when `root` holds a generated set: a manifest of this kind, any manifest that can't be
    read (fail closed), or a card whose author is `generated` in labels.jsonl. For a checkout set
    both halves count: a generated card in the private half makes the set generated (P1, P7;
    EVAL-PRIVATE.md, Step 3 review round 13, B1)."""
    from ecf.eval import merged  # noqa: PLC0415 - merged's own imports must not load genset

    halves = [root]
    try:  # from either half's path: both halves (a private path maps to its public one; round 14)
        public = merged.public_of(root) if merged.is_private_root(root) else root
        priv = merged.paths(public).private
        halves += [public] + ([priv] if priv is not None else [])
    except (merged.Unreadable, PermissionError):  # a half that can't be found: generated, failing
        return True  # closed (review round 17)
    except Exception:  # noqa: S110 - a location `paths` can't work out: the folder alone (refused elsewhere)
        pass
    try:
        return any(_generated(h) for h in dict.fromkeys(halves))
    except OSError:  # a half that can't be read: generated (fail closed, as an unreadable
        return True  # manifest is; review round 16)


def _generated(root: Path) -> bool:
    manifest = root / MANIFEST
    if manifest.exists():
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        if not isinstance(doc, dict) or cast(dict[str, Any], doc).get("kind") == KIND:
            return True
    index = root / "labels.jsonl"
    try:
        lines = index.read_text(encoding="utf-8").splitlines() if index.is_file() else []
    except OSError:
        return True
    for line in filter(None, lines):
        try:
            row: Any = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and cast(dict[str, Any], row).get("author") == AUTHOR:
            return True
    return False


def undecided(author: str, expected: dict[str, Any]) -> bool:
    """A generated case with no expected rule (eval plan draft 21, OD-549): its labels are
    scored, never its rule, safety or `correct`. The one definition the run and the report use."""
    return author == AUTHOR and expected.get("rule") is None
