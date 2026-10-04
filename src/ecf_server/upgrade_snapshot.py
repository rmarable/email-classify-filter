"""`ecf-server snapshot`: the database copy an upgrade starts from (SPEC §11.10; OD-107, OD-376;
V1.5 step 11a).

Run while the service is stopped (it takes the instance lock, so it refuses while one runs): a
backup-API copy of the database to `<data>/upgrades/<label>/ecf.db` (folder 0700, file 0600).
`ecf upgrade` puts the running version's wheel beside it, so a rollback reinstalls exactly what
ran. Only the newest `KEEP` upgrade folders are kept (operator decision 2026-10-03, OD-376). The
copy holds what the live database does (grants, nonces, jobs); it never leaves this computer.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from ecf.errors import InvalidInputError
from ecf.paths import Paths

KEEP = 2
_LABEL = re.compile(r"^[0-9A-Za-z.+_-]{1,80}$")


def folder(paths: Paths) -> Path:
    return paths.data_dir / "upgrades"


def take(paths: Paths, label: str) -> Path:
    """Copy the database for upgrade `label` (e.g. `0.1.0-to-0.1.1`); returns the folder."""
    from ecf_server import db  # noqa: PLC0415
    from ecf_server.service import acquire_lock  # noqa: PLC0415

    if not _LABEL.fullmatch(label):
        raise InvalidInputError("a snapshot label: letters, digits, '.', '+', '_' and '-'")
    lock = acquire_lock(paths)  # AlreadyRunningError while the service runs
    try:
        base = folder(paths)
        base.mkdir(mode=0o700, exist_ok=True)
        base.chmod(0o700)
        target = base / label
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(mode=0o700)
        src = db.connect(paths.db)
        try:
            dst = sqlite3.connect(target / "ecf.db")
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        (target / "ecf.db").chmod(0o600)
        prune(paths)
        return target
    finally:
        lock.close()


def prune(paths: Paths, keep: int = KEEP) -> list[str]:
    """Delete all but the newest `keep` upgrade folders (by modification time)."""
    base = folder(paths)
    if not base.is_dir():
        return []
    dirs = sorted((d for d in base.iterdir() if d.is_dir() and not d.is_symlink()),
                  key=lambda d: d.stat().st_mtime, reverse=True)  # fmt: skip
    gone: list[str] = []
    for d in dirs[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        gone.append(d.name)
    return gone
