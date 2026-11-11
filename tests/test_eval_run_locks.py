"""A run holds its set's shared lock from before it reads the set until it ends, and a failure on
the way leaves no lock and no phantom run (EVAL-PRIVATE.md, "The merged set")."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError, InvalidInputError
from ecf.eval import merged as mg
from ecf_server import claude_eval, db, evalrun
from ecf_server.clock import FakeClock
from tests.test_claude_eval import start as claude_start
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _clear  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _start as null_start  # pyright: ignore[reportPrivateUsage]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    _clear()
    claude_eval.reset()
    yield
    _clear()
    claude_eval.reset()


@pytest.fixture
def pub(tmp_path: Path) -> Path:
    p, _priv, _total = _split_set(tmp_path)
    return p


def _locked(pub: Path) -> bool:
    """Whether a shared lock is held: a refresh can't take the exclusive one."""
    card = next(((mg.paths(pub).private or pub) / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")  # stale, so a refresh would write
    try:
        mg.refresh_merged(pub, wait=0.05)
    except mg.RefreshBusy:
        return True
    return False


def test_a_run_refuses_without_a_lock_file_or_during_a_refresh(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    with pytest.raises(InvalidInputError, match="ecf eval build"):
        null_start(db_path, clock, pub, backend="null")
    mg.refresh_merged(pub)
    lock = mg.paths(pub).lock
    assert lock is not None
    import fcntl  # noqa: PLC0415
    import os  # noqa: PLC0415

    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(InvalidInputError, match="a refresh is running"):
            null_start(db_path, clock, pub, backend="null")
    finally:
        os.close(fd)
    assert evalrun.RUN.state == "idle"


def test_a_null_run_holds_the_lock_until_it_ends(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    mg.refresh_merged(pub)
    during: list[bool] = []
    real = evalrun._run  # pyright: ignore[reportPrivateUsage]

    def spy(*a: Any, **kw: Any) -> Any:
        during.append(_locked(pub))
        return real(*a, **kw)

    monkeypatch.setattr(evalrun, "_run", spy)
    null_start(db_path, clock, pub, backend="null")
    assert during == [True]
    assert not _locked(pub)  # released at the end: the refresh went through


def test_a_failed_thread_start_leaves_no_lock_and_no_running_run(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)

    def broken(_work: Callable[[], None]) -> None:
        raise RuntimeError("no threads")

    with pytest.raises(RuntimeError):
        evalrun.start(lambda: db.connect(db_path), clock, db_path.parent,
                      evalrun.Options(pub, backend="null"), spawn=broken)  # fmt: skip
    assert evalrun.RUN.state == "idle"
    assert not _locked(pub)
    null_start(db_path, clock, pub, backend="null")  # the next run starts
    assert evalrun.RUN.state == "done"


def test_the_already_running_refusal_leaves_the_other_runs_state(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)
    evalrun.RUN.set(state="running", run_id="other", done=3, total=9)
    with pytest.raises(ConflictError):
        null_start(db_path, clock, pub, backend="null")
    assert (evalrun.RUN.state, evalrun.RUN.run_id, evalrun.RUN.done) == ("running", "other", 3)
    assert not _locked(pub)


def test_a_claude_run_holds_the_lock_until_it_ends_and_a_stop_releases_it(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)
    claude_start(db_path, clock, pub, for_gate=False, cases=("starter-*",))
    run = claude_eval.current()
    assert run is not None and run.set_lock is not None and run.set_lock.held
    assert _locked(pub)
    claude_eval.stop(claude_eval_connect(db_path), clock)
    assert run.set_lock is None
    run.release_set()  # a second release does nothing
    assert not _locked(pub)


def test_a_claude_runs_failed_spawn_ends_it_and_clears_only_its_slot(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)

    def broken(_work: Callable[[], None]) -> None:
        raise RuntimeError("no threads")

    with pytest.raises(RuntimeError):
        claude_eval.start(claude_eval_connect(db_path), clock, db_path.parent,
                          claude_eval.Options(pub, for_gate=False, cases=("starter-*",)),
                          spawn=broken)  # fmt: skip
    assert claude_eval.current() is None
    assert not _locked(pub)


def claude_eval_connect(db_path: Path) -> Callable[[], sqlite3.Connection]:
    from tests.test_claude_eval import connector  # noqa: PLC0415

    return connector(db_path)


# Every exit of a Claude run releases its lock (C16): done and prepare-failed here; stopped and a
# failed spawn above; expired on a draft run below.


def test_a_claude_run_that_ends_releases_its_lock(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    from tests.test_claude_eval import REQUEST, drain  # noqa: PLC0415

    mg.refresh_merged(pub)
    claude_start(db_path, clock, pub, for_gate=False, cases=("starter-control",))
    run = claude_eval.current()
    assert run is not None and _locked(pub)
    drain(db_path, clock, lambda _cid: REQUEST)
    assert run.state == "done" and run.set_lock is None
    assert not _locked(pub)


def test_a_claude_run_whose_preparation_fails_releases_its_lock(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    mg.refresh_merged(pub)

    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("prepare")

    monkeypatch.setattr(claude_eval, "_prepare", broken)
    claude_start(db_path, clock, pub, for_gate=False, cases=("starter-*",))
    run = claude_eval.current()
    assert run is not None and run.state == "failed" and run.set_lock is None
    assert not _locked(pub)


def test_an_expired_draft_run_releases_its_lock(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    from datetime import timedelta  # noqa: PLC0415

    from tests.test_claude_eval import connector  # noqa: PLC0415
    from tests.test_eval_recovery_x13 import make_split_drafts  # noqa: PLC0415

    dpub = make_split_drafts(tmp_path)
    claude_start(db_path, clock, dpub)
    run = claude_eval.current()
    assert run is not None and run.set_lock is not None and run.set_lock.held
    clock.advance(timedelta(hours=49).total_seconds())
    claude_eval.tick(connector(db_path), clock)
    assert run.state == "expired" and run.set_lock is None
    assert not _locked(dpub)


# A refresh while a run reads the set (C18, C33, C41, C62). The tests that run `ecf eval build`
# need a private half the builder accepts: marked cards only.


@pytest.fixture
def marked(tmp_path: Path) -> Path:
    from tests.test_eval_halves_cli import make_halves  # noqa: PLC0415

    p = make_halves(tmp_path)
    from ecf.eval.builder import build_all  # noqa: PLC0415

    for half in mg.halves(p):
        assert not build_all(half).findings
    mg.refresh_merged(p)
    return p


def test_a_refresh_during_a_run_points_to_ecf_eval_status_and_a_build_after_it_is_fresh(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, marked: Path
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    claude_start(db_path, clock, marked, for_gate=False, cases=("starter-*",))
    card = next(((mg.paths(marked).private or marked) / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    with pytest.raises(mg.RefreshBusy, match="see `ecf eval status`"):
        mg.refresh_merged(marked)
    claude_eval.stop(claude_eval_connect(db_path), clock)
    r = CliRunner().invoke(app, ["eval", "build", "--root", str(marked)])
    assert r.exit_code == 0, r.output
    assert mg.resolve(marked) == mg.paths(marked).merged


def test_a_stale_set_refuses_a_run_until_ecf_eval_build(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, marked: Path
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    card = next(((mg.paths(marked).private or marked) / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    with pytest.raises(InvalidInputError, match="out of date; run `ecf eval build`"):
        null_start(db_path, clock, marked, backend="null")
    assert evalrun.RUN.state == "idle"
    r = CliRunner().invoke(app, ["eval", "build", "--root", str(marked)])
    assert r.exit_code == 0, r.output
    null_start(db_path, clock, marked, backend="null")
    assert evalrun.RUN.state == "done"


def test_a_long_build_holds_no_lock_until_its_refresh(
    marked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl  # noqa: PLC0415
    import os  # noqa: PLC0415

    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415
    from ecf.eval import builder  # noqa: PLC0415

    lock = mg.paths(marked).lock
    assert lock is not None
    free: list[bool] = []
    real = builder.build_all

    def watched(root: Path) -> Any:
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # nobody holds any lock now
            free.append(True)
        except OSError:
            free.append(False)
        finally:
            os.close(fd)
        return real(root)

    monkeypatch.setattr(builder, "build_all", watched)
    r = CliRunner().invoke(app, ["eval", "build", "--root", str(marked)])
    assert r.exit_code == 0, r.output
    assert free == [True, True]  # each half's build, before the refresh


def test_a_refresh_between_a_runs_read_and_its_thread_start_is_excluded(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)
    card = next(((mg.paths(pub).private or pub) / "cases").glob("*.md"))
    tried: list[str] = []

    def spawn(work: Callable[[], None]) -> None:
        card.write_text(card.read_text() + "\n")  # stale after the read
        try:
            mg.refresh_merged(pub)
        except mg.RefreshBusy:
            tried.append("busy")
        work()

    evalrun.start(lambda: db.connect(db_path), clock, db_path.parent,
                  evalrun.Options(pub, backend="null"), spawn=spawn,
                  power=_ac, battery=lambda: 90,
                  thermal=lambda: {"CPU_Speed_Limit": 100})  # fmt: skip
    assert tried == ["busy"]


def _ac() -> Any:
    from ecf_server import schedule  # noqa: PLC0415

    return schedule.Power(laptop=True, on_ac=True)


# A Claude run's own failures on the way (C35, C36).


def test_a_claude_run_whose_remember_root_fails_leaves_no_lock_and_another_starts(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from ecf.eval import gitcheck  # noqa: PLC0415
    from tests.test_eval_private_gate import repos  # noqa: PLC0415

    p, _priv, _n = _split_set(tmp_path)
    repos(tmp_path)
    mg.refresh_merged(p)
    gitcheck.reset_backoff()

    def broken(*_a: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(evalrun, "remember_root", broken)
    with pytest.raises(OSError, match="disk full"):
        claude_start(db_path, clock, p, cases=None)
    assert claude_eval.current() is None and not _locked(p)
    mg.refresh_merged(p)
    claude_start(db_path, clock, p, for_gate=False, cases=("starter-*",))  # the next run starts
    assert (claude_eval.current() or pytest.fail()).state == "ready"


def test_a_claude_run_whose_spawn_fails_lets_the_next_one_start(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path
) -> None:
    mg.refresh_merged(pub)

    def broken(_work: Callable[[], None]) -> None:
        raise RuntimeError("no threads")

    with pytest.raises(RuntimeError):
        claude_eval.start(claude_eval_connect(db_path), clock, db_path.parent,
                          claude_eval.Options(pub, for_gate=False, cases=("starter-*",)),
                          spawn=broken)  # fmt: skip
    claude_start(db_path, clock, pub, for_gate=False, cases=("starter-*",))
    assert (claude_eval.current() or pytest.fail()).state == "ready"


def test_a_claude_run_stopped_while_preparing_releases_its_lock_once(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    mg.refresh_merged(pub)
    closes: list[int] = []
    real = mg.release

    def counted(fd: int | None) -> None:
        if fd is not None:
            closes.append(fd)
        real(fd)

    pending: list[Callable[[], None]] = []
    claude_eval.start(claude_eval_connect(db_path), clock, db_path.parent,
                      claude_eval.Options(pub, for_gate=False, cases=("starter-*",)),
                      spawn=pending.append)  # fmt: skip
    run = claude_eval.current()
    assert run is not None and run.state == "preparing" and run.set_lock is not None
    mine = run.set_lock._fd  # pyright: ignore[reportPrivateUsage]
    assert mine is not None
    monkeypatch.setattr(mg, "release", counted)
    claude_eval.stop(claude_eval_connect(db_path), clock)  # while preparing
    pending[0]()  # the preparation thread runs after the stop
    assert closes.count(mine) == 1 and not _locked(pub)


def test_a_run_stopped_between_registering_and_its_spawn_is_finished_once(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, pub: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    mg.refresh_merged(pub)
    ends: list[str] = []
    real_end = claude_eval._end_record  # pyright: ignore[reportPrivateUsage]

    def counted(clock_: Any, run: Any, state: str, detail: str, *, complete: bool) -> None:
        ends.append(run.run_id)
        real_end(clock_, run, state, detail, complete=complete)

    monkeypatch.setattr(claude_eval, "_end_record", counted)
    newer: list[str] = []

    def spawn(_work: Callable[[], None]) -> None:
        claude_eval.stop(claude_eval_connect(db_path), clock)  # stopped before its thread
        claude_start(db_path, clock, pub, for_gate=False, cases=("starter-*",))
        newer.append((claude_eval.current() or pytest.fail()).run_id)
        raise RuntimeError("no threads")

    with pytest.raises(RuntimeError):
        claude_eval.start(claude_eval_connect(db_path), clock, db_path.parent,
                          claude_eval.Options(pub, for_gate=False, cases=("starter-*",)),
                          spawn=spawn)  # fmt: skip
    first = next(i for i in ends if i != newer[0])
    assert ends.count(first) == 1  # finished once
    assert (claude_eval.current() or pytest.fail()).run_id == newer[0]  # its slot left alone
