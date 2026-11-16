"""Golden-trace harness for v1's item engine (planner step 2; D114 (2), D116).

A scenario builds a world, then drives v1's engine through events (the entry functions the
inventory lists, called as their callers call them). For each event the harness records every
commit's row diff, the return value or exception, and what the fake mailbox, SMTP sender and
notifier saw. The crash sweep reruns each scenario once per commit, killing the process right after
that commit (every later statement on the connection fails), then runs one recovery tick.

Determinism: ids, Message-IDs, step-up codes and tokens come from counters; the clock moves 1 ms at
each COMMIT and never on a read (D116 Q2); events move it further only where a scenario says so.
No engine code is changed: only id and clock sources are patched and the connection is wrapped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any, cast
from unittest import mock

from ecf_server import (
    approvals,
    claude_review,
    db,
    digests,
    draft_actions,
    draft_data,
    leases,
    mailbox_actions,
    send,
    send_actions,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.fetch import close_gone, load_cursor
from ecf_server.mail.fake import FakeMailSource, FakeSender
from ecf_server.notify import FakeNotifier

INSTALL = "t"
MB = 1024 * 1024
HOLDER = "golden-check"
SKIP_TABLES = frozenset({"schema_migrations"})  # applied_at is wall-clock time of the migration
ORIG_CONNECT = sqlite3.connect


class Crash(BaseException):
    """The service died right after a commit."""


# The recorder sees every commit of every connection the engine opens during a run.


@dataclass
class _Recorder:
    path: Path
    clock: FakeClock
    commits: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    crash_at: int | None = None  # 1-based commit number within the current event
    dead: bool = False
    crashed: bool = False
    last: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    recording: bool = False

    def on_commit(self) -> None:
        self.clock.advance(0.001)
        if not self.recording:
            return
        now = dump(self.path)
        self.commits.append(diff(self.last, now))
        self.last = now
        if self.crash_at is not None and len(self.commits) == self.crash_at:
            self.dead = self.crashed = True
            raise Crash


_ACTIVE: list[_Recorder] = []


class TraceConnection(sqlite3.Connection):
    """A connection that reports each COMMIT to the recorder, and refuses every statement once
    the recorder says the process died."""

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        rec = _ACTIVE[-1] if _ACTIVE else None
        if rec is not None and rec.dead:
            raise Crash
        cur = super().execute(sql, parameters)
        if rec is not None and sql.strip().upper() == "COMMIT":
            rec.on_commit()
        return cur


# Whole-database dumps and row diffs. Rows are keyed by their primary key (rowid when none).


def _tables(c: sqlite3.Connection) -> list[str]:
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def _value(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"bytes_sha256": hashlib.sha256(v).hexdigest()[:16], "len": len(v)}
    return v


def dump(path: Path) -> dict[str, dict[str, Any]]:
    c = ORIG_CONNECT(path)
    try:
        out: dict[str, dict[str, Any]] = {}
        for t in _tables(c):
            info = c.execute(f'PRAGMA table_info("{t}")').fetchall()
            pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            cols = [r[1] for r in info]
            key_sql = ", ".join(f'"{k}"' for k in pk) if pk else "rowid"
            rows: dict[str, Any] = {}
            for r in c.execute(f'SELECT {key_sql}, * FROM "{t}"'):  # noqa: S608 - names from sqlite_master
                nk = len(pk) if pk else 1
                key = json.dumps([_value(x) for x in r[:nk]])
                rows[key] = {col: _value(v) for col, v in zip(cols, r[nk:], strict=True)}
            if rows:
                out[t] = rows
        return out
    finally:
        c.close()


def diff(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in sorted(set(a) | set(b)):
        ra, rb = a.get(t, {}), b.get(t, {})
        d: dict[str, Any] = {}
        added = [{"key": k, "row": rb[k]} for k in sorted(set(rb) - set(ra))]
        removed = [{"key": k, "row": ra[k]} for k in sorted(set(ra) - set(rb))]
        changed: list[dict[str, Any]] = []
        for k in sorted(set(ra) & set(rb)):
            if ra[k] != rb[k]:
                cols = {
                    c: [ra[k].get(c), rb[k].get(c)]
                    for c in sorted(set(ra[k]) | set(rb[k]))
                    if ra[k].get(c) != rb[k].get(c)
                }
                changed.append({"key": k, "set": cols})
        if added:
            d["+"] = added
        if removed:
            d["-"] = removed
        if changed:
            d["~"] = changed
        if d:
            out[t] = d
    return out


def apply(state: dict[str, dict[str, Any]], d: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {t: dict(rows) for t, rows in state.items()}
    for t, ch in d.items():
        rows = out.setdefault(t, {})
        for r in ch.get("-", []):
            del rows[r["key"]]
        for r in ch.get("+", []):
            rows[r["key"]] = r["row"]
        for r in ch.get("~", []):
            rows[r["key"]] = rows[r["key"]] | {c: v[1] for c, v in r["set"].items()}
        if not rows:
            del out[t]
    return out


# Mail, SMTP and notifier logs.


def _msg_id(raw: bytes) -> str:
    m = message_from_bytes(raw, policy=policy.default)
    return str(m.get("Message-ID", ""))


def mail_snapshot(src: FakeMailSource | None) -> dict[str, Any]:
    if src is None:
        return {}
    folders: dict[str, Any] = {"INBOX": src._msgs} | src.elsewhere  # pyright: ignore[reportPrivateUsage]
    return {
        name: {
            str(uid): {
                "mid": _msg_id(s.raw),
                "sha": hashlib.sha256(s.raw).hexdigest()[:12],
                "flags": sorted(s.flags),
            }
            for uid, s in sorted(msgs.items())
        }
        for name, msgs in sorted(folders.items())
        if msgs
    }


def _sent_entry(entry: tuple[str, tuple[str, ...], bytes]) -> dict[str, Any]:
    frm, rcpt, raw = entry
    m = message_from_bytes(raw, policy=policy.default)
    return {
        "from": frm,
        "rcpt": list(rcpt),
        "mid": str(m.get("Message-ID", "")),
        "subject": str(m.get("Subject", "")),
        "sha": hashlib.sha256(raw).hexdigest()[:12],
    }


# Plain JSON for return values and exceptions.


def plain(v: Any) -> Any:  # noqa: PLR0911 - one case per type
    if v is None or isinstance(v, bool | int | float | str):
        return v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in cast(dict[Any, Any], v).items()}
    if isinstance(v, list | tuple):
        return [plain(x) for x in cast(list[Any], v)]
    if isinstance(v, set | frozenset):
        return sorted(plain(x) for x in v)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, sqlite3.Row):
        return {k: _value(v[k]) for k in v.keys()}  # noqa: SIM118 - Row has no __iter__ of keys
    if hasattr(v, "__dataclass_fields__"):
        return {"type": type(v).__name__} | {
            k: plain(getattr(v, k)) for k in v.__dataclass_fields__
        }
    raise TypeError(f"golden: no plain form for {type(v).__name__}")


def error(exc: BaseException) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": str(exc)}


# The world a scenario runs in, and the events it drives.


@dataclass
class World:
    path: Path
    clock: FakeClock
    conn: sqlite3.Connection
    src: FakeMailSource | None = None
    smtp: FakeSender = field(default_factory=FakeSender)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    lost: bool = False  # the check's lease is gone
    vars: dict[str, Any] = field(default_factory=dict[str, Any])

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.path)


Event = tuple[str, Callable[[World], Any]]


@dataclass(frozen=True)
class Scenario:
    name: str
    doc: str
    setup: Callable[[World], Any]
    events: tuple[Event, ...]
    mail: bool = False  # the world has a fake mailbox, so recovery runs a check too


# Ticks and checks: the in-scope sweeps, in service.py's order (service.py:310-338), and a
# check's in-lease work, in the order the service registers it (checks.IN_LEASE).
IN_LEASE_ORDER = (
    send.settle_in_check,
    mailbox_actions.run_in_check,
    digests._undos_in_check,  # pyright: ignore[reportPrivateUsage]
    draft_data.track_in_check,
    draft_actions.run_in_check,
)


def tick(w: World, *, awake: float = 60.0, woke: bool = False) -> dict[str, Any]:
    from ecf_server import answers, approvals, claude_queue, decide  # noqa: PLC0415

    return {
        "approvals.expire": approvals.expire(w.conn, w.clock),
        "answers.expire": answers.expire(w.conn, w.clock),
        "decide.sweep": decide.sweep(w.conn, w.clock),
        "claude_queue.sweep": claude_queue.sweep(w.conn, w.clock),
        "approvals.post_held_cards": approvals.post_held_cards(w.conn, w.clock),
        "approvals.advance_delays": approvals.advance_delays(w.conn, w.clock, awake, woke=woke),
    }


def check(w: World) -> dict[str, Any]:
    """The part of a check that moves items: close_gone, then the in-lease work."""
    assert w.src is not None
    lease = leases.acquire(w.conn, w.clock, "ap", HOLDER)
    assert lease is not None
    out: dict[str, Any] = {}
    try:
        cur = load_cursor(w.conn, "ap")
        if cur is not None and cur.uidvalidity is not None:
            out["close_gone"] = close_gone(
                w.conn,
                w.clock,
                w.src,
                lease,
                cur.uidvalidity,
                close_stale=cur.recovering_until == 0,
            )
        for work in IN_LEASE_ORDER:
            r = work(w.conn, w.clock, w.src, "ap", INSTALL, 10 * MB, lambda: w.lost)
            out[f"{work.__module__.rsplit('.', 1)[-1]}.{work.__name__}"] = plain(r)
    finally:
        leases.release(w.conn, lease)
    return out


# Patches: counters for every random value the engine draws.


def seq_hex(n: int, nbytes: int) -> str:
    """The n-th value of a fixed sequence of random-looking hex strings: unique in any prefix, as
    callers slice ids (`[:8]`, `[:12]`)."""
    out = ""
    block = 0
    while len(out) < 2 * nbytes:
        out += hashlib.sha256(f"golden:{n}:{block}".encode()).hexdigest()
        block += 1
    return out[: 2 * nbytes]


@contextlib.contextmanager
def _patched(rec: _Recorder) -> Generator[None]:
    n = {"id": 0, "mid": 0, "tok": 0, "hex": 0}

    def rid() -> str:
        n["id"] += 1
        return seq_hex(n["id"], 16)

    def mid(domain: str) -> str:
        n["mid"] += 1
        return f"<{n['mid']:032x}.ecf@{domain.lower()}>"

    def tok(nbytes: int | None = None) -> str:
        del nbytes
        n["tok"] += 1
        return f"tok{n['tok']:08d}"

    def hexes(nbytes: int | None = None) -> str:
        n["hex"] += 1
        return seq_hex(n["hex"], nbytes or 32)

    with contextlib.ExitStack() as st:
        for owner in (
            "ecf.ids",
            "ecf_server.api",
            "ecf_server.chat",
            "ecf_server.claude_review",
            "ecf_server.scans",
        ):  # modules that import new_random_id by name
            st.enter_context(mock.patch(f"{owner}.new_random_id", rid))
        st.enter_context(mock.patch("secrets.token_hex", hexes))
        st.enter_context(mock.patch("ecf_server.send.new_message_id", mid))
        st.enter_context(mock.patch("ecf_server.alert_mail.new_message_id", mid))
        st.enter_context(mock.patch("secrets.choice", lambda seq: seq[0]))  # pyright: ignore
        st.enter_context(mock.patch("secrets.token_urlsafe", tok))
        st.enter_context(mock.patch("ecf_server.stepup.person", lambda: "operator"))
        st.enter_context(
            mock.patch.object(db.sqlite3, "connect", partial(ORIG_CONNECT, factory=TraceConnection))
        )
        _ACTIVE.append(rec)
        try:
            yield
        finally:
            _ACTIVE.remove(rec)


def _event(w: World, rec: _Recorder, fn: Callable[[World], Any]) -> dict[str, Any]:
    rec.commits, rec.recording = [], True
    sent0, note0, tried0 = len(w.smtp.sent), len(w.notifier.sent), w.smtp.connects
    mail0 = mail_snapshot(w.src)
    out: dict[str, Any] = {}
    try:
        out["result"] = plain(fn(w))
    except Crash:
        out["crashed"] = True
    except Exception as exc:
        out["raises"] = error(exc)
    rec.recording = False
    out["commits"] = rec.commits
    mail1 = mail_snapshot(w.src)
    if mail1 != mail0:
        out["mail"] = {"before": mail0, "after": mail1}
    if len(w.smtp.sent) > sent0:
        out["smtp"] = [_sent_entry(e) for e in w.smtp.sent[sent0:]]
    if w.smtp.connects > tried0:
        out["sends_tried"] = w.smtp.connects - tried0
    if len(w.notifier.sent) > note0:
        out["notify"] = [list(e) for e in w.notifier.sent[note0:]]
    return out


def _sender(smtp: FakeSender) -> Callable[[sqlite3.Connection, str], FakeSender]:
    return lambda _conn, _aid: smtp


# The engine's in-memory state (claude_review's held submissions and grace windows): emptied
# at the start of each run and at the restart after a crash.
_IN_MEMORY: tuple[dict[Any, Any], ...] = (  # every dict claude_review.reset() clears
    claude_review._GRACE,  # pyright: ignore[reportPrivateUsage]
    claude_review._HELD,  # pyright: ignore[reportPrivateUsage]
    claude_review._FREED,  # pyright: ignore[reportPrivateUsage]
    claude_review._MISMATCHES,  # pyright: ignore[reportPrivateUsage]
    claude_review._AWAITING,  # pyright: ignore[reportPrivateUsage]
    claude_review._NEXT_CALL,  # pyright: ignore[reportPrivateUsage]
    claude_review._CALLED,  # pyright: ignore[reportPrivateUsage]
    claude_review._UNREAD,  # pyright: ignore[reportPrivateUsage]
    claude_review._TALLY,  # pyright: ignore[reportPrivateUsage]
)

SIDE_EFFECTS = ("mail", "smtp", "notify", "sends_tried")
RECOVERY_WAIT_S = 180  # past decide.sweep's 2 min and claude_queue.sweep's 1 min


def restart(w: World) -> None:
    """v1's start-up clean-up (service.py:191-197, run before the first tick): leases, claimed
    jobs and review claims all belonged to the dead process."""
    from ecf_server import service  # noqa: PLC0415 - the service module is heavy

    service._clear_stale(w.conn)  # pyright: ignore[reportPrivateUsage]
    w.lost = False  # the new process takes the lease afresh


def run(s: Scenario, tmp: Path, crash: tuple[int, int] | None = None) -> dict[str, Any]:
    """One run. `crash` = (event index, commit number): the process dies after that commit, then
    restarts (a new connection, the clock RECOVERY_WAIT_S on) and runs one recovery tick (and a
    check when the world has a mailbox). Returns the trace."""
    path = tmp / "data" / "ecf.db"
    rec = _Recorder(path, FakeClock())
    with _patched(rec), contextlib.ExitStack() as stack:
        w = World(path, rec.clock, db.connect(path), FakeMailSource() if s.mail else None)
        stack.enter_context(mock.patch.object(approvals, "desktop", w.notifier))
        for memory in _IN_MEMORY:  # a fresh process: in-memory state starts empty
            stack.enter_context(mock.patch.dict(memory, clear=True))
        stack.enter_context(mock.patch.object(send_actions, "sender_for", _sender(w.smtp)))
        db.migrate(w.conn)
        with db.write_tx(w.conn):  # migrations stamp their settings with the wall clock
            w.conn.execute("UPDATE settings SET updated_at = ?", (ts(w),))
            w.conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'install.id'", (json.dumps("1d" * 16),)
            )  # a random ID drawn by migration 0025
        s.setup(w)
        rec.last = start = dump(w.path)
        trace: dict[str, Any] = {"start": start, "mail_start": mail_snapshot(w.src), "events": []}
        for i, (name, fn) in enumerate(s.events):
            pre = rec.last
            rec.crash_at = crash[1] if crash is not None and crash[0] == i else None
            ev: dict[str, Any] = {"name": name} | _event(w, rec, fn)
            rec.crash_at = None
            now = dump(w.path)
            built = pre
            for c in ev["commits"]:
                built = apply(built, c)
            assert built == now, f"{s.name}/{name}: a write landed outside a tracked COMMIT"
            trace["events"].append(ev)
            if rec.crashed:
                rec.dead = False
                with contextlib.suppress(Exception):
                    sqlite3.Connection.close(w.conn)
                w.conn = w.connect()
                for memory in _IN_MEMORY:  # the restart loses what was in memory
                    memory.clear()
                w.clock.advance(RECOVERY_WAIT_S)
                trace["recovery"] = {"restart": _event(w, rec, restart)}
                trace["recovery"]["tick"] = _event(w, rec, tick)
                if s.mail:
                    trace["recovery"]["check"] = _event(w, rec, check)
                break
        w.conn.close()
    return trace


def golden(s: Scenario) -> dict[str, Any]:
    """The scenario's whole golden: the normal run, then one crash run per commit of each event,
    each crash state checked against the normal run's commits up to that point."""
    with tempfile.TemporaryDirectory() as d:
        normal = run(s, Path(d) / "normal")
    crashes: list[dict[str, Any]] = []
    state = normal["start"]
    for i, ev in enumerate(normal["events"]):
        for k in range(1, len(ev["commits"]) + 1):
            with tempfile.TemporaryDirectory() as d:
                t = run(s, Path(d) / "crash", (i, k))
            got = t["events"][i]
            want = state
            for c in ev["commits"][:k]:
                want = apply(want, c)
            have = state
            for c in got["commits"]:
                have = apply(have, c)
            assert got.get("crashed") and have == want, (
                f"{s.name}: crash after {ev['name']} commit {k} isn't the normal run's prefix"
            )
            crashes.append(
                {
                    "event": i,
                    "name": ev["name"],
                    "after_commit": k,
                    "state": diff(state, have),
                    # what the crashed event did outside the database before it died
                    **{
                        key: got[key]
                        for key in ("mail", "smtp", "notify", "sends_tried")
                        if key in got
                    },
                    "recovery": t["recovery"],
                }
            )
        for c in ev["commits"]:
            state = apply(state, c)
    return {
        "scenario": s.name,
        "doc": s.doc,
        "start": normal["start"],
        "mail_start": normal["mail_start"],
        "events": normal["events"],
        "crashes": crashes,
    }


def ts(w: World) -> str:
    return to_ts(w.clock.now())
