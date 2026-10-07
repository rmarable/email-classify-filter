"""Labels for a real-mail corpus (SPEC §16.7; OD-466; R28, R68, R86, R90, R141, R146).

They sit beside the corpus file as `<corpus>.labels.jsonl` (0600, never in git), one row per
message, keyed by the message's `content_hash` and `identity_digest` (which survive a re-fetch or a
merge). A row holds the schema's field values, or a mark: `s` (skipped) or `u` (unsure), both left
out of scoring. Nothing in a row comes from the message's text. Rows are written sorted by key and
`date` changes only when the label does, so the file's hash, which becomes part of a run's set
version, moves only with the labels.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.schema import FieldKind, load_schema_v1

SUFFIX = ".labels.jsonl"
MARKS = ("s", "u")
AUTHOR = "operator"

Key = tuple[str, str]  # (content_hash, identity_digest)


@dataclass(frozen=True)
class Label:
    key: Key
    corpus_id: str
    labels: dict[str, str | bool] | None  # every schema field, or None with a mark
    mark: str | None  # "s" skipped, "u" unsure
    date: str

    @property
    def confirmed(self) -> bool:
        """Every field authored, no mark (R68). Whether the key matches the message is checked
        where the corpus is open."""
        return self.mark is None and self.labels is not None

    def row(self) -> dict[str, Any]:
        return {"key": {"content_hash": self.key[0], "identity_digest": self.key[1]},
                "corpus_id": self.corpus_id, "labels": self.labels, "mark": self.mark,
                "confirmed": self.confirmed, "author": AUTHOR, "date": self.date}  # fmt: skip


def path_for(corpus: Path) -> Path:
    return corpus.with_name(corpus.name + SUFFIX)


def check_values(values: dict[str, Any]) -> dict[str, str | bool]:
    """Every schema field, each a value from its closed vocabulary."""
    schema = load_schema_v1()
    if set(values) != set(schema.fields):
        raise InvalidInputError("a label needs exactly the schema's fields")
    out: dict[str, str | bool] = {}
    for name, spec in schema.fields.items():
        v = values[name]
        ok = isinstance(v, bool) if spec.kind is FieldKind.BOOLEAN else v in spec.values
        if not ok:
            raise InvalidInputError(f"{name}: {v!r} isn't one of its values")
        out[name] = cast(str | bool, v)
    return out


def make(key: Key, corpus_id: str, values: dict[str, Any] | None, mark: str | None,
         today: date) -> Label:  # fmt: skip
    if (values is None) == (mark is None):
        raise InvalidInputError("a label is either values or a mark (s or u)")
    if mark is not None and mark not in MARKS:
        raise InvalidInputError("a mark is s (skip) or u (unsure)")
    return Label(key, corpus_id, check_values(values) if values is not None else None, mark,
                 today.isoformat())  # fmt: skip


def load(path: Path) -> dict[Key, Label]:
    if not path.exists():
        return {}
    out: dict[Key, Label] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            key = (str(r["key"]["content_hash"]), str(r["key"]["identity_digest"]))
            raw = r.get("labels")
            values = check_values(raw) if raw is not None else None
            out[key] = Label(key, str(r["corpus_id"]), values, r.get("mark"), str(r["date"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidInputError(f"{path}: line {n} isn't a label row") from exc
    return out


def put(labels: dict[Key, Label], new: Label) -> bool:
    """Add or replace a label; keeps the old date when nothing but the date would change.
    Returns whether the label changed."""
    old = labels.get(new.key)
    if old is not None and (old.labels, old.mark) == (new.labels, new.mark):
        return False
    labels[new.key] = new
    return True


def save(path: Path, labels: dict[Key, Label]) -> str:
    """Write every row, sorted by key, to a 0600 temp file, then rename it into place (R90).
    Returns the file's sha256."""
    body = "".join(json.dumps(labels[k].row(), sort_keys=True) + "\n" for k in sorted(labels))
    tmp = path.with_name(f".{path.name}.partial")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return hashlib.sha256(body.encode()).hexdigest()


def file_hash(path: Path) -> str:
    """The labels file's sha256 ("" when there is none); a run's set version takes 12 hex."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
