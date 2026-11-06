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

    def spy(*a: Any) -> Any:
        during.append(_locked(pub))
        return real(*a)

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
