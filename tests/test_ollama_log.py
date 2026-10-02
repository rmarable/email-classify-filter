"""Rotating the Ollama login item's log (V1.3.1; SPEC §7.5, OD-266): by size and by age, copy
then truncate, gzip and 0600, unique names, pruning by age and count, one service at a time."""

from __future__ import annotations

import fcntl
import gzip
import os
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

from ecf_server import ollama_log, settings
from ecf_server.clock import FakeClock

DAY = 86400


def _live(root: Path, text: bytes = b"") -> Path:
    live = root / "ollama" / "ollama.log"
    live.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    live.write_bytes(text)
    return live


def _set(conn: sqlite3.Connection, clock: FakeClock, name: str, value: str) -> None:
    settings.set_value(conn, clock, name, value, address=None, actor="test")


def test_nothing_to_do_without_ecfs_login_item(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    assert ollama_log.check(conn, clock, tmp_path) is None
    assert not (tmp_path / "ollama").exists()


def test_a_log_over_the_size_limit_is_copied_then_truncated(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    _set(conn, clock, "ollama_log_max_mb", "1")
    live = _live(tmp_path, b"time=... level=INFO msg=served\n" * 40_000)  # about 1.2 MB
    writer = os.open(live, os.O_WRONLY | os.O_APPEND)  # as launchd hands it to Ollama
    try:
        copy = ollama_log.check(conn, clock, tmp_path)
        assert copy is not None and copy.name == "ollama.log.2026-10-01.gz"
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
        assert gzip.decompress(copy.read_bytes()).count(b"served") == 40_000
        assert live.stat().st_size == 0
        os.write(writer, b"next line\n")  # Ollama's next write lands at the new start
    finally:
        os.close(writer)
    assert live.read_bytes() == b"next line\n"
    row = conn.execute("SELECT data FROM audit WHERE event = 'ollama_log.rotated'").fetchone()
    assert row is not None and '"ollama.log.2026-10-01.gz"' in row[0]
    assert ollama_log.check(conn, clock, tmp_path) is None  # small again


def test_by_age_after_the_set_days_and_never_when_empty(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    live = _live(tmp_path, b"a line\n")
    assert ollama_log.check(conn, clock, tmp_path) is None  # the first look starts the clock
    clock.advance(6 * DAY)
    assert ollama_log.check(conn, clock, tmp_path) is None
    clock.advance(DAY)
    first = ollama_log.check(conn, clock, tmp_path)
    assert first is not None and live.stat().st_size == 0
    clock.advance(8 * DAY)
    assert ollama_log.check(conn, clock, tmp_path) is None  # nothing written since
    _set(conn, clock, "ollama_log_rotate_days", "1")
    live.write_bytes(b"more\n")
    second = ollama_log.check(conn, clock, tmp_path)
    assert second is not None and second != first


def test_a_second_rotation_the_same_day_gets_its_own_name(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    _set(conn, clock, "ollama_log_max_mb", "1")
    big = b"x" * (2 * ollama_log.MB)
    live = _live(tmp_path, big)
    a = ollama_log.check(conn, clock, tmp_path)
    live.write_bytes(big)
    clock.advance(60)
    b = ollama_log.check(conn, clock, tmp_path)
    assert a is not None and b is not None
    assert (a.name, b.name) == ("ollama.log.2026-10-01.gz", "ollama.log.2026-10-01-2.gz")


def test_old_copies_and_those_beyond_twelve_are_deleted(tmp_path: Path, clock: FakeClock) -> None:
    folder = _live(tmp_path).parent
    now = clock.now().timestamp()
    for i in range(15):
        p = folder / f"ollama.log.2026-09-{i + 1:02d}.gz"
        p.write_bytes(b"")
        os.utime(p, (now - (15 - i) * DAY, now - (15 - i) * DAY))
    gone = ollama_log.prune(folder, clock.now(), days=90)
    assert sorted(gone) == ["ollama.log.2026-09-01.gz", "ollama.log.2026-09-02.gz",
                            "ollama.log.2026-09-03.gz"]  # fmt: skip
    assert len(list(folder.glob("ollama.log.*.gz"))) == ollama_log.KEEP == 12
    gone = ollama_log.prune(folder, clock.now(), days=10)  # log_retention_days lowered
    assert len(gone) == 2 and len(list(folder.glob("ollama.log.*.gz"))) == 10


def test_one_service_at_a_time(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    _set(conn, clock, "ollama_log_max_mb", "1")
    live = _live(tmp_path, b"x" * (2 * ollama_log.MB))
    fd = os.open(live.parent / ollama_log.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # another install's service is rotating
        assert ollama_log.check(conn, clock, tmp_path) is None
        assert live.stat().st_size == 2 * ollama_log.MB
    finally:
        os.close(fd)
    assert ollama_log.check(conn, clock, tmp_path) is not None


def test_the_settings_and_their_defaults(conn: sqlite3.Connection) -> None:
    assert settings.get(conn, "ollama_log_max_mb") == 10  # OD-266
    assert settings.get(conn, "ollama_log_rotate_days") == 7
    assert (12, timedelta(hours=1)) == (ollama_log.KEEP, ollama_log.CHECK_EVERY)
