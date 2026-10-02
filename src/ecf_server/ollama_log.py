"""Rotating the log of ecf's Ollama login item (SPEC §7.5; V1.3.1; OD-266).

ecf's login item sends Ollama's stderr to `<data root>/ollama/ollama.log` (`ecf.ollama_unit`).
launchd (or systemd's `StandardError=append:`) opens that file once, in append mode, and hands it
to Ollama, so renaming it would leave Ollama writing into the renamed file. Instead, about once an
hour the service checks it and, when it is over `ollama_log_max_mb` or the last rotation is
`ollama_log_rotate_days` old (and it isn't empty), **copies then truncates**: the content goes to
`ollama.log.YYYY-MM-DD.gz` (`-2`, `-3` for a second rotation that day; 0600), then the live file
is cut to zero, and Ollama's next line, written in append mode, lands at its new end. A line
written between the copy and the cut is lost; at about 200 bytes per model call that is rare.

Rotated files older than `log_retention_days`, and any beyond the newest `KEEP`, are deleted. The
folder is shared by every install on this computer, so the last rotation's time is a file in it
and a lock file keeps two services from rotating at once. Only this log is touched: an Ollama you
run yourself keeps its own logs.
"""

from __future__ import annotations

import fcntl
import gzip
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ecf.ollama_unit import log_path
from ecf_server import retention, settings
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log

MB = 1024 * 1024
KEEP = 12  # rotated files kept at most (operator decision 2026-10-01, OD-266)
CHECK_EVERY = timedelta(hours=1)
STAMP = ".rotated_at"  # in the log's folder: when it was last rotated (shared by installs)
LOCK = ".rotate.lock"
PREFIX = "ollama.log."


def check(conn: sqlite3.Connection, clock: Clock, root: Path) -> Path | None:
    """Rotate the log when it is due and prune old copies; the new copy, or None."""
    live = log_path(root)
    folder = live.parent
    if not folder.is_dir():
        return None  # ecf's login item was never installed here
    with _locked(folder) as mine:
        if not mine:
            return None  # another install's service is at it
        now = clock.now()
        rotated = _rotate(conn, now, live) if _due(conn, now, live) else None
        prune(folder, now, retention.days(conn))
    return rotated


class _locked:
    def __init__(self, folder: Path) -> None:
        self.path = folder / LOCK
        self.fd: int | None = None

    def __enter__(self) -> bool:
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def __exit__(self, *_exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)  # releases the lock


def _due(conn: sqlite3.Connection, now: datetime, live: Path) -> bool:
    try:
        size = live.stat().st_size
    except OSError:
        return False
    if size == 0:
        return False
    if size > int(settings.get(conn, "ollama_log_max_mb")) * MB:
        return True
    last = _last(live.parent)
    if last is None:  # first look: the age counts from now
        _stamp(live.parent, now)
        return False
    return now - last >= timedelta(days=int(settings.get(conn, "ollama_log_rotate_days")))


def _last(folder: Path) -> datetime | None:
    try:
        return from_ts((folder / STAMP).read_text().strip())
    except (OSError, ValueError):
        return None


def _stamp(folder: Path, now: datetime) -> None:
    path = folder / STAMP
    path.write_text(to_ts(now))
    path.chmod(0o600)


def _rotate(conn: sqlite3.Connection, now: datetime, live: Path) -> Path:
    dest = _name(live.parent, now)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with (
        open(live, "rb") as src,
        os.fdopen(fd, "wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb") as out,
    ):
        shutil.copyfileobj(src, out)
        size = src.tell()
    os.truncate(live, 0)  # Ollama appends: its next line starts the file again
    _stamp(live.parent, now)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'ollama_log.rotated',"
            " 'service', 'ok', json_object('file', ?, 'bytes', ?))",
            (to_ts(now), dest.name, size),
        )
    log.info("ollama_log.rotated", file=dest.name, bytes=size)
    return dest


def _name(folder: Path, now: datetime) -> Path:
    day = now.strftime("%Y-%m-%d")
    n = 1
    while True:
        dest = folder / f"{PREFIX}{day}{'' if n == 1 else f'-{n}'}.gz"
        if not dest.exists():
            return dest
        n += 1


def prune(folder: Path, now: datetime, days: int) -> list[str]:
    """Delete rotated copies older than `days` and any beyond the newest `KEEP`; their names."""
    copies = sorted((p for p in folder.glob(f"{PREFIX}*.gz") if p.is_file()),
                    key=lambda p: p.stat().st_mtime, reverse=True)  # fmt: skip
    cutoff = (now - timedelta(days=days)).timestamp()
    gone: list[str] = []
    for i, p in enumerate(copies):
        if i >= KEEP or p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            gone.append(p.name)
    return gone
