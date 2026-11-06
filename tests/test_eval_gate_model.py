"""The gate's promise, checked over every combination of its inputs (EVAL-PRIVATE.md, "The gate
guard"; Step 3 review round 3): a run counts only if it read the whole merged set of a checkout,
fresh, committed and pushed. Each case builds the set state, records a run, and compares
`gate.synthetic` with one oracle that states the promise directly; no case may pass the gate
unless the oracle says it should, and every case the oracle passes must pass."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from ecf.eval import gitcheck
from ecf_server import claude_pins, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.eval_checkout import checkout_set, git
from tests.test_gate import DIGEST

PLACES = ["canonical", "respelled", "symlink", "outside", "private path", "merged path"]
HALVES = ["both", "public only"]
FRESH = ["fresh", "stale"]
RUNS = ["counts", "no counts", "private 0", "no proof", "old version"]
GITS = ["clean", "dirty", "unpushed"]


def oracle(place: str, halves: str, fresh: str, run: str, git_state: str) -> bool:
    """The promise: a checkout's own set (however its path is written), both halves, fresh,
    committed and pushed, and a run that recorded reading it (or a run from before the guard
    on the same version)."""
    return (
        place in ("canonical", "respelled", "symlink")
        and halves == "both"
        and fresh == "fresh"
        and git_state == "clean"
        and run in ("counts", "no counts")
    )


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    top = tmp_path_factory.mktemp("tmpl")
    checkout_set(top, ("starter-bec", "starter-control"), ("starter-injection",),
                 ("starter-bec", "starter-control", "starter-injection"))  # fmt: skip
    return top


def _set_up(top: Path, halves: str, fresh: str, git_state: str) -> None:
    priv = top / "eval" / "private"
    card = priv / "synthetic" / "cases" / "starter-injection.md"
    if fresh == "stale":  # a source changed, committed and pushed, the view not rebuilt
        card.write_text(card.read_text() + "\n")
        git(priv, "commit", "-qam", "edit")
        git(priv, "push", "-q")
    if git_state == "dirty":  # an untracked file in the set: no card, so the view stays fresh
        (priv / "synthetic" / "notes.txt").write_text("x")
    elif git_state == "unpushed":
        git(priv, "commit", "-q", "--allow-empty", "-m", "local only")
    if halves == "public only":
        shutil.rmtree(priv / "synthetic")


def _root(top: Path, tmp: Path, place: str) -> Path:
    pub = top / "tests" / "eval" / "synthetic"
    if place == "canonical":
        return pub
    if place == "respelled":
        return top / "Tests" / "EVAL" / "Synthetic"
    if place == "symlink":
        (tmp / "alias").symlink_to(pub)
        return tmp / "alias"
    if place == "outside":  # the public half, copied out of any checkout
        out = tmp / "out" / "synthetic"
        shutil.copytree(pub, out)
        return out
    if place == "private path":
        return top / "eval" / "private" / "synthetic"
    return top / "eval" / "private" / ".merged" / "synthetic"


def _record(conn: sqlite3.Connection, clock: FakeClock, root: Path, top: Path, run: str) -> None:
    """A complete, safe run as the gate reads it, with what it recorded per `run`."""
    try:
        version = evalrun.set_version(root)
    except Exception:
        version = "f" * 16
    try:  # the set's own references, as a run that read it would record them
        refs = evalrun.reference_counts(evalrun.read_set(root).selected(fraud_only=False))
    except Exception:
        refs = {"confirmed": 3, "fraud_guard_cases": 2}
    m: dict[str, object] = dict(refs)
    m |= {
        "unsafe": [], "fraud_cases": 2, "complete": True,
        "fraud_guard_missed": [], "fraud_guard_recall": 100.0,
        "options": {"classifier": True, "actor": True}}  # fmt: skip
    head = gitcheck.read_head(top / "eval" / "private") or "0" * 40
    if run in ("counts", "private 0", "no proof"):
        m |= {"public_cases": 2, "private_cases": 0 if run == "private 0" else 1}
        if run != "no proof":
            m |= {"merged_version": version, "private_commit": head}
    if run == "old version":
        version = "0" * 16
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, digest, set_version, created_at,"
                     " metrics, gate_passed, path) VALUES ('ce3be1df00', ?, ?, ?, ?, ?, 1, 'p')",
                     (claude_pins.PAIR["C"], DIGEST, version, now, json.dumps(m)))  # fmt: skip
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, str(root), now, actor="test")


def _cases() -> list[object]:
    out: list[object] = []
    for place in PLACES:
        for halves in HALVES:
            for fresh in FRESH:
                for run in RUNS:
                    for g in GITS:
                        marks = [pytest.mark.macos] if place == "respelled" else []  # APFS
                        out.append(
                            pytest.param(
                                place,
                                halves,
                                fresh,
                                run,
                                g,
                                marks=marks,
                                id=f"{place}|{halves}|{fresh}|{run}|{g}",
                            )
                        )
    return out


@pytest.mark.parametrize(("place", "halves", "fresh", "run", "git_state"), _cases())
def test_the_gate_passes_exactly_when_the_promise_holds(  # noqa: PLR0913, PLR0917 - the inputs
    conn: sqlite3.Connection, clock: FakeClock, template: Path, tmp_path: Path,
    place: str, halves: str, fresh: str, run: str, git_state: str,
) -> None:  # fmt: skip
    if place == "respelled":
        assert sys.platform == "darwin", "respelled paths need a case-insensitive file system"
    top = tmp_path / "top"
    shutil.copytree(template, top, symlinks=True)
    priv = top / "eval" / "private"
    git(priv, "remote", "set-url", "origin", str(top / "remote.git"))
    git(priv, "fetch", "-q", "origin")
    _set_up(top, halves, fresh, git_state)
    root = _root(top, tmp_path, place)
    gate.reset_cache()
    gitcheck.reset_backoff()
    _record(conn, clock, root, top, run)
    got = gate.synthetic(conn, DIGEST)
    assert got.ok == oracle(place, halves, fresh, run, git_state), got.detail
