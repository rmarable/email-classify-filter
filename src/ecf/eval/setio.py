"""Writing into an eval set safely (planning-docs/EVAL-PRIVATE.md, "The overlay", W1, OD-647).

Every writer into a set goes through `atomic_write`, so a crash leaves the old file or the new
one, never half of it, and through `writable_root`, so nothing writes into the merged view of a
public and a private set (`eval/private/.merged/<set>/`), which only `refresh_merged` builds."""

from __future__ import annotations

import os
from pathlib import Path

from ecf.errors import InvalidInputError

MERGED_DIR = ".merged"
MERGE_FILE = "merge.json"


def tmp_path(path: Path) -> Path:
    """`.<name>.tmp` beside the file: ignored by both repositories' `.gitignore`, so a crashed
    write never makes a clone dirty."""
    return path.with_name(f".{path.name}.tmp")


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write `data` to a temporary file beside `path`, then rename it over `path`. A stale
    temporary file from a crashed write is removed first."""
    tmp = tmp_path(path)
    tmp.unlink(missing_ok=True)
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def is_merged(root: Path) -> bool:
    """Whether `root`, or any folder above it, is a merged view: a folder named `.merged`, or one
    holding `merge.json`."""
    p = root.resolve()
    return any(d.name == MERGED_DIR or (d / MERGE_FILE).is_file() for d in (p, *p.parents))


def writable_root(root: Path) -> Path:
    """Refuse a set root inside a merged view; return the root unchanged otherwise. Every set
    writer calls this before it writes."""
    if is_merged(root):
        raise InvalidInputError(
            f"{root} is a merged eval set, which is rebuilt from its sources;"
            " write to the public or private set that owns the card"
        )
    return root
