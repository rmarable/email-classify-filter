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
    read (fail closed), or a card whose author is `generated` in labels.jsonl."""
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
