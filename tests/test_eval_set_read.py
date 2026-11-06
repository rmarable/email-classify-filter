"""The one set reader (`evalrun.read_set`) and the gate's one cached set state (EVAL-PRIVATE.md,
"Fail closed", "Torn reads", "The gate guard": the set-level part read once and shared).
A row that can't run is a problem, never silently dropped; a run for the gate and the gate
refuse on any; a case is re-checked against its hash when it runs."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import gitcheck
from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf.eval.builder import build_all
from ecf_server import claude_eval, evalrun, gate
from ecf_server.clock import FakeClock
from tests.test_claude_eval import CASES, SYNTHETIC
from tests.test_claude_eval import start as claude_start
from tests.test_eval_private_gate import DIGEST, _counts, _run, repos  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _clear  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _start as null_start  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The three starter cards, built and confirmed, outside a checkout's layout."""
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in CASES:
        shutil.copy(SYNTHETIC / "cases" / f"{name}.md", r / "cases" / f"{name}.md")
    assert not build_all(r).findings
    for name in ("starter-bec", "starter-injection"):
        label_file.confirm(r, name, date(2026, 10, 1))
    return r


@pytest.fixture(autouse=True)
def _reset() -> Any:
    claude_eval.reset()
    gate.reset_cache()
    _clear()
    yield
    claude_eval.reset()
    gate.reset_cache()
    _clear()


def _eml(root: Path, case_id: str) -> Path:
    return root / "eml" / f"{case_id}.eml"


def test_a_complete_set_reads_with_no_problems(root: Path) -> None:
    s = evalrun.read_set(root)
    assert s.problems == () and len(s.cases) == 3
    assert s.version == label_file.set_version(root)
    assert all(c.sha256 for c in s.cases)


@pytest.mark.parametrize("why", [evalrun.MISSING, evalrun.CHANGED, evalrun.OVERSIZED,
                                 evalrun.OUTSIDE])  # fmt: skip
def test_each_kind_of_problem_is_reported_not_dropped(
    root: Path, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if why == evalrun.MISSING:
        _eml(root, "starter-bec").unlink()
    elif why == evalrun.CHANGED:
        _eml(root, "starter-bec").write_bytes(b"edited")
    elif why == evalrun.OVERSIZED:
        monkeypatch.setattr(evalrun, "MAX_CASE_BYTES", 10)
    else:
        rows = [r | ({"file": "../elsewhere.eml"} if r["id"] == "starter-bec" else {})
                for r in label_file.read(root)]  # fmt: skip
        (root / "labels.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                                                   for r in rows))  # fmt: skip
    s = evalrun.read_set(root)
    got = dict(s.problems)
    if why == evalrun.OVERSIZED:
        assert set(got.values()) == {why} and len(got) == 3
        return
    assert got == {"starter-bec": why}
    ids = {c.id: c for c in s.cases}
    if why == evalrun.CHANGED:  # kept, unconfirmed (today's behaviour)
        assert not ids["starter-bec"].confirmed
    else:
        assert "starter-bec" not in ids


def test_a_run_for_the_gate_refuses_on_any_problem(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    del conn
    _eml(root, "starter-control").unlink()
    with pytest.raises(InvalidInputError, match=r"starter-control \(missing\)"):
        claude_start(db_path, clock, root, preset="C")
    assert claude_eval.current() is None


def test_another_run_goes_ahead_and_lists_the_problems(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    del conn
    _eml(root, "starter-control").unlink()
    null_start(db_path, clock, root, backend="null")
    assert evalrun.RUN.state == "done", evalrun.RUN.detail
    summary = evalrun.RUN.snapshot()["result"]
    assert summary["set_problems"] == [["starter-control", "missing"]]


def test_a_case_edited_under_a_run_fails_it(conn: sqlite3.Connection, db_path: Path,
                                            clock: FakeClock, root: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    del conn
    real = evalrun._run  # pyright: ignore[reportPrivateUsage]

    def edit_then_run(*a: Any, **kw: Any) -> Any:
        _eml(root, "starter-bec").write_bytes(b"edited after the set was read")
        return real(*a, **kw)

    monkeypatch.setattr(evalrun, "_run", edit_then_run)
    null_start(db_path, clock, root, backend="null")
    assert evalrun.RUN.state == "failed"
    assert "starter-bec" in evalrun.RUN.detail and "changed while the run" in evalrun.RUN.detail


def test_case_bytes_checks_the_hash(root: Path) -> None:
    case = next(c for c in evalrun.read_set(root).cases if c.id == "starter-bec")
    assert evalrun.case_bytes(case) == _eml(root, "starter-bec").read_bytes()
    _eml(root, "starter-bec").write_bytes(b"x")
    with pytest.raises(evalrun.CaseChanged, match="starter-bec"):
        evalrun.case_bytes(case)


# The gate: one read per state, shared; problems fail it; a refresh in progress is a transient
# failed check, never cached past a pass.


@pytest.fixture
def split(tmp_path: Path) -> Path:
    pub, _priv, _total = _split_set(tmp_path)
    repos(tmp_path)
    mg.refresh_merged(pub)
    gitcheck.reset_backoff()
    return pub


def test_the_gate_reads_the_set_once_while_nothing_changes(
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(conn, clock, split, _counts(split))
    reads: list[Path] = []
    real = evalrun.read_set

    def counted(root: Path) -> evalrun.SetRead:
        reads.append(root)
        return real(root)

    monkeypatch.setattr(evalrun, "read_set", counted)
    for _ in range(5):  # a status over several addresses, then a tick
        assert gate.synthetic(conn, DIGEST).ok
    assert len(reads) == 1
    assert gate.synthetic(conn, DIGEST, fresh=True).ok  # `stage set live`
    assert len(reads) == 2
    priv = mg.paths(split).private or split
    card = next((priv / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")  # a change: the next check reads again
    gate.synthetic(conn, DIGEST)
    assert len(reads) == 3


def test_a_missing_file_fails_the_gate(conn: sqlite3.Connection, clock: FakeClock,
                                       split: Path) -> None:  # fmt: skip
    _run(conn, clock, split, _counts(split))
    merged_eml = next((mg.paths(split).merged or split).glob("eml/*.eml"))
    merged_eml.chmod(0o644)
    merged_eml.unlink()  # gone from the view; the sources say it should be there
    check = gate.synthetic(conn, DIGEST, fresh=True)
    assert not check.ok
    assert "out of date" in check.detail or "can't run as read" in check.detail


def test_a_missing_file_in_a_fresh_view_fails_the_gate(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    """A set whose own row names a file nobody built (the high finding): the view is fresh, the
    version and counts match, and the gate still fails, naming the card."""
    rows = label_file.read(split)
    rows.append(rows[0] | {"id": "zz-never-built", "file": "eml/zz-never-built.eml"})
    (split / "cases" / "zz-never-built.md").write_text("x")
    (split / "labels.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                                                for r in rows))  # fmt: skip
    mg.refresh_merged(split)
    _run(conn, clock, split, _counts(split))
    check = gate.synthetic(conn, DIGEST, fresh=True)
    assert not check.ok and "zz-never-built (missing)" in check.detail


def test_a_refresh_holding_the_lock_is_a_transient_failed_check(
    conn: sqlite3.Connection, clock: FakeClock, split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(conn, clock, split, _counts(split))
    lock = mg.paths(split).lock
    assert lock is not None
    retry = gate._RETRY[0]  # pyright: ignore[reportPrivateUsage]
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        check = gate.synthetic(conn, DIGEST)
        assert not check.ok and "a refresh is running" in check.detail
    finally:
        os.close(fd)
    assert gate._RETRY[0] == retry + 1  # pyright: ignore[reportPrivateUsage]
    assert not gate.synthetic(conn, DIGEST).ok  # the same pass shares the busy result
    later = gate._now() + gate.PASS_S + 1  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(gate, "_now", lambda: later)
    assert gate.synthetic(conn, DIGEST).ok  # the next pass reads again


# The git check: one timeout for the whole check, and "no private half" is never clean.


def test_every_git_call_in_a_check_shares_one_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _split_set(tmp_path, "heldout3", 4)
    repos(tmp_path)
    gitcheck.reset_backoff()
    clock = [0.0]
    timeouts: list[float] = []
    real: Any = gitcheck._run_git  # pyright: ignore[reportPrivateUsage]

    def slow(cmd: list[str], **kw: Any) -> Any:
        timeouts.append(kw["timeout"])
        clock[0] += 2.0  # each call takes 2 s
        return real(cmd, **kw)

    monkeypatch.setattr(gitcheck, "_now", lambda: clock[0])
    monkeypatch.setattr(gitcheck, "_run_git", slow)
    got = gitcheck.clean_and_pushed(tmp_path, "heldout3")
    assert timeouts[:3] == [5.0, 3.0, 1.0]  # what is left of the one 5 s timeout
    assert not got.ok and got.error and "timed out" in got.detail
    assert len(timeouts) == 3  # the fourth call isn't started


def test_no_private_half_is_never_clean(tmp_path: Path) -> None:
    _split_set(tmp_path, "heldout3", 4)
    repos(tmp_path)
    shutil.rmtree(tmp_path / "eval" / "private" / "heldout3")
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(tmp_path, "heldout3")
    assert not got.ok and got.no_private and not got.error


def test_an_in_place_write_through_a_hard_link_refuses_a_run_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """C5: with the hard-link path forced, writing in place through the merged folder changes the
    source too; the set reads as stale or changed, and a run for the gate refuses."""
    del conn
    pub, priv, _n = _split_set(tmp_path)
    repos(tmp_path)
    (priv / ".build").mkdir(exist_ok=True)
    big = priv / ".build" / f"{mg.card_ids(priv)[0]}.eml"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr(mg, "_CLONE", None)
    mg.refresh_merged(pub)
    linked = (mg.paths(pub).merged or pub) / ".build" / big.name
    assert linked.stat().st_ino == big.stat().st_ino
    with linked.open("r+b") as f:
        f.write(b"y")
    with pytest.raises(InvalidInputError, match="out of date"):
        claude_start(db_path, clock, pub, preset="C")
    assert claude_eval.current() is None
