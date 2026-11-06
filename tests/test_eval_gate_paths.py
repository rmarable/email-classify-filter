"""The gate and the stored roots on a set with a private half, through the paths a real install
takes (EVAL-PRIVATE.md, "The gate guard", "The stored root", Step 3's pass list): a git failure,
a hanging git, `stage set live`, a tick over a stale set, a merged path stored as the root, a
run from before the guard with the private set gone, and Step 5's own call."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import gitcheck
from ecf.eval import merged as mg
from ecf_server import claude_eval, claude_pins, evalrun, gate, slack_admin, stages
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.test_claude_eval import start as claude_start
from tests.test_eval_private_gate import _counts, _run, repos  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _clear  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _start as null_start  # pyright: ignore[reportPrivateUsage]
from tests.test_gate import DIGEST, make_address


@pytest.fixture(autouse=True)
def _reset() -> Any:
    gate.reset_cache()
    claude_eval.reset()
    _clear()
    yield
    gate.reset_cache()
    claude_eval.reset()
    _clear()


@pytest.fixture
def split(tmp_path: Path) -> Path:
    pub, _priv, _total = _split_set(tmp_path)
    repos(tmp_path)
    mg.refresh_merged(pub)
    gitcheck.reset_backoff()
    return pub


def _count_git(monkeypatch: pytest.MonkeyPatch, fail: BaseException | None = None) -> list[str]:
    calls: list[str] = []
    real: Any = gitcheck._run_git  # pyright: ignore[reportPrivateUsage]

    def wrapped(cmd: list[str], **kw: Any) -> Any:
        calls.append(cmd[-1])
        if fail is not None:
            raise fail
        return real(cmd, **kw)

    monkeypatch.setattr(gitcheck, "_run_git", wrapped)
    return calls


def test_git_failing_refuses_a_for_gate_run_and_the_gate_retries_next_time(  # C6
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, split: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _run(conn, clock, split, _counts(split))
    calls = _count_git(monkeypatch, OSError("no git"))
    with pytest.raises(InvalidInputError, match="committed and pushed: git: git couldn't run"):
        claude_start(db_path, clock, split, preset="C")
    assert claude_eval.current() is None
    first = len(calls)
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and "git couldn't run" in check.detail
    gate.synthetic(conn, DIGEST)  # the same pass: shared
    assert len(calls) == first + 1
    later = gate._now() + gate.PASS_S + 1  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(gate, "_now", lambda: later)
    gate.synthetic(conn, DIGEST)  # the next tick: the error was not kept
    assert len(calls) == first + 2


def test_a_hanging_git_costs_one_timeout_per_pass_then_backs_off(  # C9
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(conn, clock, split, _counts(split))
    calls = _count_git(monkeypatch, subprocess.TimeoutExpired("git", 5))
    for _ in range(4):  # a status over four addresses in one pass
        check = gate.synthetic(conn, DIGEST)
        assert not check.ok and "timed out" in check.detail
    assert len(calls) == 1
    now = [gate._now() + 100.0]  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(gate, "_now", lambda: now[0])
    monkeypatch.setattr(gitcheck, "_now", lambda: now[0])
    for _ in range(5):  # later passes, within the backoff: no git at all
        now[0] += gate.PASS_S + 1
        assert "retried shortly" in gate.synthetic(conn, DIGEST).detail
    assert len(calls) == 1


def test_stage_set_live_runs_the_check_fresh(  # C10
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_address(conn, clock, "assist")
    _run(conn, clock, split, _counts(split))
    gate.compute(conn, "ap")  # the cache is warm
    calls = _count_git(monkeypatch)
    gate.compute(conn, "ap")
    assert calls == []  # cached
    with pytest.raises(Exception):  # noqa: B017 - the gate isn't met here; only the check counts
        stages._live_allowed(conn, "ap", override=False, reason="",  # pyright: ignore[reportPrivateUsage]
                             held=stages.HELD_CHOICES[0])  # fmt: skip
    assert calls  # `stage set live` ran git again


def test_a_tick_over_a_stale_set_fails_the_check_and_the_timer_carries_on(  # C15
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ecf.paths import Paths  # noqa: PLC0415
    from ecf_server import approvals, db, service  # noqa: PLC0415

    pub, _priv, _n = _split_set(tmp_path / "set")
    repos(tmp_path / "set")
    mg.refresh_merged(pub)
    clock = FakeClock()
    svc = service.Service(Paths("t", tmp_path / "home"), clock, dev=True)
    svc.state.db_path = svc.paths.db
    conn = db.connect(svc.paths.db)
    db.migrate(conn)
    make_address(conn, clock, "assist")
    _run(conn, clock, pub, _counts(pub))
    card = next(((mg.paths(pub).private or pub) / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")  # stale
    ran: list[bool] = []
    real = approvals.advance_delays

    def spy(*a: Any, **kw: Any) -> Any:
        ran.append(True)
        return real(*a, **kw)

    monkeypatch.setattr(approvals, "advance_delays", spy)
    svc.tick()
    assert ran == [True]
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and "out of date" in check.detail
    conn.close()


def test_a_merged_path_stored_as_the_root_is_refused(  # C17
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    _run(conn, clock, split, _counts(split))
    with write_tx(conn):
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, str(mg.paths(split).merged),
                                to_ts(clock.now()), actor="test")  # fmt: skip
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and "public set's path" in check.detail


def test_a_pre_guard_run_is_refused_when_the_private_set_is_gone(  # C27
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    counts = _counts(split)
    _run(conn, clock, split, counts)  # recorded on the merged version, no membership keys
    assert gate.synthetic(conn, DIGEST).ok
    shutil.rmtree(split.parents[2] / "eval" / "private")  # a clone without it
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and "older version" in check.detail


def test_step_5s_call_with_the_addresss_own_digest(  # C61
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    """Step 5 calls `gate.synthetic` on a database copy with the digest from
    `claude_pins.address_key`: a wrong digest would stop at "no synthetic-set result"."""
    make_address(conn, clock, "assist")
    digest = claude_pins.address_key(conn, "ap")
    assert digest == DIGEST
    _run(conn, clock, split, _counts(split), version="0" * 16)  # a run on an older version
    check = gate.synthetic(conn, digest)
    assert check.detail.startswith("the latest run (ce3be1df")
    assert gate.synthetic(conn, "null").detail.startswith("no synthetic-set result")


# Stored roots stay logical (B6), and runs record their counts by half (C22).


def test_a_for_gate_run_stores_the_logical_root(conn: sqlite3.Connection, db_path: Path,
                                               clock: FakeClock, split: Path) -> None:  # fmt: skip
    claude_start(db_path, clock, split, preset="C")
    stored = slack_admin.setting(conn, evalrun.EVAL_ROOT)
    assert stored == evalrun.logical(split) and "/eval/private/" not in str(stored)


def test_a_draft_run_and_the_null_floor_store_the_logical_root(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    from ecf_server import draftjudging, draftruns  # noqa: PLC0415
    from tests.test_eval_recovery_x13 import make_split_drafts  # noqa: PLC0415

    dpub = make_split_drafts(tmp_path)
    claude_start(db_path, clock, dpub)
    run = claude_eval.current()
    assert run is not None
    rec = draftruns.load(db_path.parent).get(run.run_id)
    assert rec is not None and rec.options["root"] == str(dpub) and rec.root == str(dpub)
    claude_eval.reset()
    out = draftjudging.null_floor(db_path.parent, dpub, "2026-11-06T00:00:00Z")
    side = draftjudging.folder(db_path.parent) / f"{out['run_id']}{draftjudging.NULL_SUFFIX}"
    assert json.loads(side.read_text())["root"] == evalrun.logical(dpub)


def test_a_null_run_records_its_counts_by_half(conn: sqlite3.Connection, db_path: Path,
                                              clock: FakeClock, split: Path) -> None:  # fmt: skip
    null_start(db_path, clock, split, backend="null")
    assert evalrun.RUN.state == "done", evalrun.RUN.detail
    row = conn.execute("SELECT metrics FROM eval_runs ORDER BY created_at DESC").fetchone()
    m = json.loads(row["metrics"])
    pub_ids, priv_ids = mg.membership(split) or pytest.fail()
    assert m["public_cases"] == len(pub_ids) and m["private_cases"] == len(priv_ids)


def test_membership_reads_no_card_file(split: Path) -> None:  # C32
    before = mg.membership(split)
    sp = mg.paths(split)
    assert sp.private is not None
    for half in (split, sp.private):
        shutil.rmtree(half / "cases")
        shutil.rmtree(half / "eml")
    assert mg.membership(split) == before


def test_routes_read_the_merged_set(split: Path) -> None:  # B24
    from ecf_server import routereport  # noqa: PLC0415

    out = routereport.report([split], [], FakeClock())
    (st,) = out["sets"]
    cases, _ = evalrun.load(split, fraud_only=False)
    assert st["confirmed"] + st["unconfirmed"] == len(cases)


# The gate's cache (review round 2, M7, P1, P2): its key covers every file the read reads, a
# slow transient read is shared by the rest of its pass, and a pass works out the key once.


def test_a_merged_email_lost_after_a_good_check_is_seen_at_the_next_check(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    _run(conn, clock, split, _counts(split))
    assert gate.synthetic(conn, DIGEST).ok  # cached
    m = mg.paths(split).merged
    assert m is not None
    eml = next((m / "eml").glob("*.eml"))
    eml.chmod(0o644)
    eml.unlink()
    check = gate.synthetic(conn, DIGEST)  # not fresh=True: the key moved
    assert not check.ok


def test_a_slow_transient_read_is_shared_by_the_rest_of_its_pass(
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(conn, clock, split, _counts(split))
    now = [gate._now()]  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(gate, "_now", lambda: now[0])
    calls: list[str] = []

    def hang(cmd: list[str], **_kw: Any) -> Any:
        calls.append(cmd[-1])
        now[0] += 3 * gate.PASS_S  # the read takes longer than a pass
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(gitcheck, "_run_git", hang)
    for _ in range(4):
        assert not gate.synthetic(conn, DIGEST).ok
    assert len(calls) == 1


def test_a_pass_works_out_the_sets_key_once(
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_address(conn, clock, "assist")
    _run(conn, clock, split, _counts(split))
    prints: list[int] = []
    real = gitcheck.fingerprint

    def counted(top: Path, name: str) -> Any:
        prints.append(1)
        return real(top, name)

    monkeypatch.setattr(gitcheck, "fingerprint", counted)
    with gate.one_pass():
        for _ in range(3):
            gate.inputs(conn, "ap", DIGEST)
            gate.compute(conn, "ap")
    assert len(prints) == 1
    gate.compute(conn, "ap")  # outside a pass: worked out again, never remembered
    assert len(prints) == 2
    entered: list[bool] = []
    real_pass = gate.one_pass

    def watched() -> Any:
        entered.append(True)
        return real_pass()

    monkeypatch.setattr(gate, "one_pass", watched)
    stages.status(conn, clock.now())
    stages.tick(conn, clock)
    assert entered == [True, True]  # status and the tick each run as one pass


def test_git_timing_out_refuses_a_for_gate_run(  # C6, the timeout half
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, split: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    del conn
    _count_git(monkeypatch, subprocess.TimeoutExpired("git", 5))
    with pytest.raises(InvalidInputError, match="committed and pushed: git: git timed out"):
        claude_start(db_path, clock, split, preset="C")
    assert claude_eval.current() is None


def test_step_5s_call_on_a_database_copy(  # C61, as Step 5 makes it: a backup, read only
    conn: sqlite3.Connection, clock: FakeClock, split: Path, tmp_path: Path
) -> None:
    make_address(conn, clock, "assist")
    _run(conn, clock, split, _counts(split), version="0" * 16)
    copy = tmp_path / "copy.db"
    dst = sqlite3.connect(copy)
    conn.backup(dst)
    dst.close()
    ro = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        digest = claude_pins.address_key(ro, "ap")
        assert gate.synthetic(ro, digest).detail.startswith("the latest run (ce3be1df")
    finally:
        ro.close()
