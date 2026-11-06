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
    assert not rec.open and rec.attempt_over  # results saved; the resume refused (new version)
    assert rec.resumed_by is None


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
        assert time.monotonic() - t0 < 3
    finally:
        os.close(fd)
    rec = _rec(db_path, run_id)
    assert rec.attempt_over and not rec.open and rec.end == "lost"
    assert rec.end_detail == (
        f"the eval set {pub.resolve()} couldn't be read at service start (its lock stayed busy or"
        f" it is still stale): run `ecf eval build --root {pub.resolve()}`; this attempt can't be"
        " resumed"
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
