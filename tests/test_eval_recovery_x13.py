"""X13: at service start a lost draft run's set is refreshed if stale, under plain lock waits,
and recovery then runs as today; a set that can't be read closes its records with a fixed detail
(EVAL-PRIVATE.md, "The merged set")."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf_server import claude_eval, draftruns
from ecf_server.clock import FakeClock
from tests.test_claude_eval import connector, start
from tests.test_draft_eval import make_droot

pytestmark = pytest.mark.usefixtures("conn")


@pytest.fixture(autouse=True)
def _reset() -> None:
    claude_eval.reset()


@pytest.fixture
def pub(tmp_path: Path) -> Path:
    return make_split_drafts(tmp_path)


def make_split_drafts(tmp_path: Path) -> Path:
    """The test draft set split: d-inject in the private half, merged and fresh."""
    p = make_droot(tmp_path / "tests" / "eval")
    priv = tmp_path / "eval" / "private" / "drafts"
    for d in ("cases", "eml"):
        (priv / d).mkdir(parents=True)
        for f in (p / d).glob("d-inject.*"):
            shutil.move(f, priv / d / f.name)
    rows = (p / "labels.jsonl").read_text().splitlines()
    (p / "labels.jsonl").write_text("".join(r + "\n" for r in rows if '"d-inject"' not in r))
    (priv / "labels.jsonl").write_text("".join(r + "\n" for r in rows if '"d-inject"' in r))
    mg.refresh_merged(p)
    return p


def _restart(db_path: Path, clock: FakeClock) -> list[str]:
    claude_eval.reset()
    from tests.test_claude_eval import _inline  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    return claude_eval.recover(connector(db_path), clock, db_path.parent, spawn=_inline)


def _saved(db_path: Path, run_id: str) -> bool:
    """The run's results are written: its draft record beside the result files."""
    from ecf.eval import drafts  # noqa: PLC0415

    return (db_path.parent / "evals" / f"{run_id}{drafts.DRAFTS_SUFFIX}").is_file()


def _rec(db_path: Path, run_id: str) -> draftruns.Registration:
    rec = draftruns.load(db_path.parent).get(run_id)
    assert rec is not None
    return rec


def test_a_fresh_set_is_not_refreshed_and_the_run_resumes(db_path: Path, clock: FakeClock,
                                                           pub: Path) -> None:  # fmt: skip
    start(db_path, clock, pub)
    run = claude_eval.current()
    assert run is not None
    merge = (mg.paths(pub).merged or pub) / "merge.json"
    before = merge.stat().st_mtime_ns
    _restart(db_path, clock)
    assert merge.stat().st_mtime_ns == before  # not refreshed
    resumed = claude_eval.current()
    assert resumed is not None and resumed.open and resumed.run_id != run.run_id


def test_a_stale_set_is_refreshed_results_saved_and_the_resume_refused(
    db_path: Path, clock: FakeClock, pub: Path
) -> None:
    start(db_path, clock, pub)
    run_id = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    lab = (mg.paths(pub).private or pub) / "labels.jsonl"  # a git pull without a build
    row = json.loads(lab.read_text())
    lab.write_text(json.dumps(row | {"note": "pulled"}, sort_keys=True) + "\n")
    before = label_file.set_version(mg.paths(pub).merged or pub)
    _restart(db_path, clock)
    assert mg.is_fresh(mg.paths(pub))  # refreshed at start
    assert label_file.set_version(mg.paths(pub).merged or pub) != before
    rec = _rec(db_path, run_id)
    assert not rec.open and rec.attempt_over  # the resume refused (new version)
    assert rec.resumed_by is None
    assert _saved(db_path, run_id)  # its results saved after the start-up refresh


def test_a_set_held_by_another_installs_run_closes_with_the_fixed_detail(
    db_path: Path, clock: FakeClock, pub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_eval, "SET_WAIT_S", 0.3)
    start(db_path, clock, pub)
    run_id = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")  # stale
    lock = mg.paths(pub).lock
    assert lock is not None
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)  # another install's running eval
    try:
        t0 = time.monotonic()
        lines = _restart(db_path, clock)
        took = time.monotonic() - t0
        assert claude_eval.SET_WAIT_S <= took < 3  # closed after the wait, not before
    finally:
        os.close(fd)
    rec = _rec(db_path, run_id)
    assert rec.attempt_over and not rec.open and rec.end == "lost"
    assert rec.end_detail == (
        f"the eval set {pub.resolve()} couldn't be read at service start (its lock stayed busy or"
        f" it is still stale); this attempt is over"
    )
    assert any("couldn't be read" in x for x in lines)


def test_two_installs_on_one_stale_set_both_end_with_it_fresh(pub: Path) -> None:
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")
    out: list[tuple[bool, str | None]] = []

    def install() -> None:
        held, why = claude_eval._set_for_recovery(pub)  # pyright: ignore[reportPrivateUsage]
        out.append((held is not None, why))
        time.sleep(0.05)  # recovering under the shared lock
        if held is not None:
            held.release()

    threads = [threading.Thread(target=install) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out == [(True, None)] * 3
    doc = json.loads(((mg.paths(pub).merged or pub) / "merge.json").read_text())
    assert doc["format"] == mg.FORMAT


def test_a_set_without_a_private_half_needs_no_lock(tmp_path: Path) -> None:
    root = make_droot(tmp_path / "tests" / "eval")
    assert claude_eval._set_for_recovery(root) == (None, None)  # pyright: ignore[reportPrivateUsage]


# Several installs on one stale set (C39, C40): the plan's offsets and durations, unscaled
# (the 5 s wait), with real locks and threads. Every install must end with the set fresh
# and recovered, and exactly one refreshes. At a 3 s refresh with three installs a set
# may close (round 27: 3 of 6); that rate is recorded, never required to be 0.

SCALE = 1.0  # the plan's real timings: a 20 ms poll and machine load don't scale down


def _installs(
    pub: Path,
    monkeypatch: pytest.MonkeyPatch,
    n: int,
    offsets: list[float],
    resolve_s: float,
    refresh_s: float,
) -> tuple[list[tuple[bool, str | None]], int]:
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")  # stale
    monkeypatch.setattr(claude_eval, "SET_WAIT_S", 5.0 * SCALE)
    real_fresh, real_build = mg.is_fresh, mg._build  # pyright: ignore[reportPrivateUsage]
    refreshed = [0]
    guard = threading.Lock()

    def slow_fresh(sp: mg.SetPaths) -> bool:
        time.sleep(resolve_s * SCALE)
        return real_fresh(sp)

    def slow_build(sp: mg.SetPaths) -> None:
        with guard:
            refreshed[0] += 1
        time.sleep(refresh_s * SCALE)
        real_build(sp)

    monkeypatch.setattr(mg, "is_fresh", slow_fresh)
    monkeypatch.setattr(mg, "_build", slow_build)
    out: list[tuple[bool, str | None]] = []

    def install(delay: float) -> None:
        time.sleep(delay * SCALE)
        held, why = claude_eval._set_for_recovery(pub)  # pyright: ignore[reportPrivateUsage]
        with guard:
            out.append((held is not None, why))
        time.sleep(0.02)  # recovering under the shared lock
        if held is not None:
            held.release()

    threads = [threading.Thread(target=install, args=(offsets[i],)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    return out, refreshed[0]


@pytest.mark.parametrize("n", [2, 3])
@pytest.mark.parametrize("offsets", [[0.0, 0.0, 0.0], [0.0, 0.5, 1.0], [1.0, 0.3, 0.0]])
@pytest.mark.parametrize(("resolve_s", "refresh_s"), [(0.03, 1.0), (0.5, 2.0), (0.03, 2.0)])
def test_installs_on_one_stale_set_all_recover_and_one_refreshes(
    pub: Path, monkeypatch: pytest.MonkeyPatch, n: int, offsets: list[float], resolve_s: float,
    refresh_s: float,
) -> None:  # fmt: skip
    out, refreshed = _installs(pub, monkeypatch, n, offsets, resolve_s, refresh_s)
    assert out == [(True, None)] * n
    assert refreshed == 1
    assert mg.is_fresh(mg.paths(pub))


def test_a_3_s_refresh_with_three_installs_may_close_and_the_rate_is_recorded(
    pub: Path, monkeypatch: pytest.MonkeyPatch, record_property: Any
) -> None:
    out, refreshed = _installs(pub, monkeypatch, 3, [0.0, 0.5, 1.0], 0.03, 3.0)
    closed = sum(1 for held, _ in out if not held)
    record_property("closed_of_3", closed)
    assert refreshed == 1
    assert all(held or why == "its lock stayed busy or it is still stale" for held, why in out)


# Several sets at one start (C42, C44, C45, C53): each set's outcome is its own.


def _second_set(tmp_path: Path) -> Path:
    other = tmp_path / "b"
    p = make_droot(other / "tests" / "eval")
    priv = other / "eval" / "private" / "drafts"
    for d in ("cases", "eml"):
        (priv / d).mkdir(parents=True)
        for f in (p / d).glob("d-inject.*"):
            shutil.move(f, priv / d / f.name)
    rows = (p / "labels.jsonl").read_text().splitlines()
    (p / "labels.jsonl").write_text("".join(r + "\n" for r in rows if '"d-inject"' not in r))
    (priv / "labels.jsonl").write_text("".join(r + "\n" for r in rows if '"d-inject"' in r))
    mg.refresh_merged(p)
    return p


RB = "feedface" * 4  # set b's record: a fixed ID no real run takes


def _two_runs(db_path: Path, clock: FakeClock, a: Path, b: Path) -> tuple[str, str]:
    """An open run on set a, and a copy of its record on set b (only one draft run can be open
    at a time, so b's is written into the registry, with its journal)."""
    start(db_path, clock, a)
    ra = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    reg = draftruns.load(db_path.parent)
    rec = reg.get(ra)
    assert rec is not None
    rb = RB
    assert rb != ra
    reg.put(rec.model_copy(update={"run_id": rb, "root": str(b), "slot": rec.slot + "-b",
                                   "options": rec.options | {"root": str(b)}}))  # fmt: skip
    draftruns.save(db_path.parent, reg)
    shutil.copy(
        draftruns.journal_path(db_path.parent, ra), draftruns.journal_path(db_path.parent, rb)
    )
    return ra, rb


def _spy_recovered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The run IDs that reach today's recovery (step 3), each returning a stub line."""
    seen: list[str] = []
    real = claude_eval._recover_one  # pyright: ignore[reportPrivateUsage]

    def spy(connect: Any, clock_: Any, data_dir: Path, rec: Any, spawn: Any) -> str:
        seen.append(rec.run_id)
        if rec.run_id == RB:
            return f"{rec.run_id[:8]} recovered"
        return real(connect, clock_, data_dir, rec, spawn)

    monkeypatch.setattr(claude_eval, "_recover_one", spy)
    return seen


def test_a_held_set_closes_after_its_wait_and_the_other_set_recovers(
    db_path: Path, clock: FakeClock, pub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert claude_eval.SET_WAIT_S == 5.0  # the plan's limit; shortened below for the test
    monkeypatch.setattr(claude_eval, "SET_WAIT_S", 0.3)
    other = _second_set(tmp_path)
    a, b = _two_runs(db_path, clock, pub, other)
    seen = _spy_recovered(monkeypatch)
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")
    lock = mg.paths(pub).lock
    assert lock is not None
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)  # another install's run on set a
    try:
        _restart(db_path, clock)
    finally:
        os.close(fd)
    ra = _rec(db_path, a)
    assert ra.attempt_over and "couldn't be read at service start" in ra.end_detail
    assert seen == [b]  # set b went on to today's recovery


def test_a_record_whose_root_is_gone_closes_as_today(db_path: Path, clock: FakeClock,
                                                     pub: Path) -> None:  # fmt: skip
    start(db_path, clock, pub)
    run_id = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    shutil.rmtree(pub.parents[2] / "eval")  # the private half and its merged view
    shutil.rmtree(pub)
    lines = _restart(db_path, clock)
    rec = _rec(db_path, run_id)
    assert not rec.open and rec.attempt_over
    # today's handler (its cases can't be restored), not X13's "couldn't be read at service start"
    assert "its cases couldn't be read back" in rec.end_detail
    assert "at service start" not in rec.end_detail
    assert lines


@pytest.mark.parametrize("how", ["refresh raises", "rows don't parse"])
def test_a_set_that_fails_leaves_the_other_recovered(
    db_path: Path, clock: FakeClock, pub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:  # fmt: skip
    other = _second_set(tmp_path)
    a, b = _two_runs(db_path, clock, pub, other)
    seen = _spy_recovered(monkeypatch)
    priv = mg.paths(pub).private or pub
    if how == "refresh raises":
        card = priv / "cases" / "d-inject.md"
        card.write_text(card.read_text() + "\n")
        real = mg.refresh_merged

        def boom(root: Path, **kw: Any) -> str:
            if Path(root) == pub:
                raise OSError("disk")
            return real(root, **kw)

        monkeypatch.setattr(mg, "refresh_merged", boom)
    else:
        (priv / "labels.jsonl").write_text("{not json\n")
    lines = _restart(db_path, clock)  # never raises: the service stays bound
    ra = _rec(db_path, a)
    assert not ra.open and ra.attempt_over
    assert b in seen and len(lines) == 2


def test_a_raise_in_step_3_closes_only_that_record(
    db_path: Path, clock: FakeClock, pub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _second_set(tmp_path)
    a, b = _two_runs(db_path, clock, pub, other)
    seen: list[str] = []

    def flaky(connect: Any, clock_: Any, data_dir: Path, rec: Any, spawn: Any) -> str:
        del connect, clock_, data_dir, spawn
        seen.append(rec.run_id)
        if rec.run_id == a:
            raise RuntimeError("boom")
        return "ok"

    monkeypatch.setattr(claude_eval, "_recover_one", flaky)
    lines = _restart(db_path, clock)
    ra, rb = _rec(db_path, a), _rec(db_path, b)
    assert (
        ra.attempt_over and ra.end_detail == "recovery failed (RuntimeError; this attempt is over)"
    )
    assert rb.open and not rb.attempt_over and b in seen and "ok" in lines  # only a closed


def test_steps_1_2_raising_close_that_sets_records_with_the_fixed_detail(
    db_path: Path, clock: FakeClock, pub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _second_set(tmp_path)
    a, b = _two_runs(db_path, clock, pub, other)
    seen = _spy_recovered(monkeypatch)
    real = mg.paths

    def broken(root: Path) -> mg.SetPaths:
        if Path(root) == pub:
            raise OSError("permission")
        return real(root)

    monkeypatch.setattr(mg, "paths", broken)
    _restart(db_path, clock)
    monkeypatch.setattr(mg, "paths", real)
    ra = _rec(db_path, a)
    assert ra.attempt_over and ra.end_detail.startswith(f"the eval set {pub} couldn't be read")
    assert "(OSError)" in ra.end_detail
    assert seen == [b]


def test_a_record_already_ended_lost_keeps_end_lost(
    db_path: Path, clock: FakeClock, pub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record ended lost but not resumed (a crash between the two) whose set can't be read
    keeps `end` "lost" when X13 closes it."""
    monkeypatch.setattr(claude_eval, "SET_WAIT_S", 0.2)
    start(db_path, clock, pub)
    run_id = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    claude_eval._update_record(db_path.parent, run_id, end="lost",  # pyright: ignore[reportPrivateUsage]
                               ended_at=clock.now(), complete=False)  # fmt: skip
    lock = mg.paths(pub).lock
    assert lock is not None
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)
    try:
        _restart(db_path, clock)
    finally:
        os.close(fd)
    rec = _rec(db_path, run_id)
    assert rec.end == "lost" and rec.attempt_over


def test_an_open_run_past_48_hours_on_a_stale_set_still_has_its_result_saved(
    db_path: Path, clock: FakeClock, pub: Path
) -> None:
    from datetime import timedelta  # noqa: PLC0415

    start(db_path, clock, pub)
    run_id = (claude_eval.current() or pytest.fail()).run_id
    claude_eval.reset()
    clock.advance(timedelta(hours=49).total_seconds())
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")  # stale: refreshed at start
    _restart(db_path, clock)
    rec = _rec(db_path, run_id)
    assert not rec.open and rec.ended_at is not None and rec.end in ("lost", "expired")
    assert rec.resumed_by is None
    assert _saved(db_path, run_id)


def test_a_freshness_check_that_raises_leaves_no_lock(
    pub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L8: if the check raises while recovery holds the shared lock, the lock is released, so a
    later refresh isn't refused as busy."""

    def boom(_sp: mg.SetPaths) -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(mg, "is_fresh", boom)
    held, why = claude_eval._set_for_recovery(pub)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.undo()
    assert held is None and why == "PermissionError"
    card = (mg.paths(pub).private or pub) / "cases" / "d-inject.md"
    card.write_text(card.read_text() + "\n")
    assert mg.refresh_merged(pub, wait=0.0) == "refreshed"  # no lock left behind
