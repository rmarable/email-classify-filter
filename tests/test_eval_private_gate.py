"""The gate guard on a set with a private half (EVAL-PRIVATE.md "The gate guard", V2, OD-643):
the version first, then counts by membership, then the references from the set itself."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import gitcheck
from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf_server import claude_pins, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]
from tests.test_gate import DIGEST


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}  # fmt: skip
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env,
                       check=False)  # fmt: skip
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout


def repos(top: Path) -> Path:
    """`top` as a committed public repository, and its `eval/private` as a private one whose
    branch tracks a pushed upstream on a local bare remote."""
    (top / ".gitignore").write_text("eval/private/\nremote.git/\n")
    git(top, "init", "-q", "-b", "main")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "set")
    priv = top / "eval" / "private"
    (priv / ".gitignore").write_text(".merged/\n.build/\n*.tmp\n")
    git(priv, "init", "-q", "-b", "main")
    git(priv, "add", "-A")
    git(priv, "commit", "-q", "-m", "private")
    bare = top / "remote.git"
    git(top, "init", "-q", "--bare", str(bare))
    git(priv, "remote", "add", "origin", str(bare))
    git(priv, "push", "-q", "-u", "origin", "main")
    return priv


@pytest.fixture
def split(tmp_path: Path) -> Path:
    pub, _priv, _total = _split_set(tmp_path)
    repos(tmp_path)
    mg.refresh_merged(pub)
    gitcheck.reset_backoff()
    return pub


def _counts(pub: Path, *, fraud_only: bool = False) -> dict[str, int]:
    return evalrun.reference_counts(evalrun.load(pub, fraud_only=fraud_only)[0])


def _run(conn: sqlite3.Connection, clock: FakeClock, pub: Path, metrics: dict[str, Any], *,
         version: str | None = None) -> None:  # fmt: skip
    base: dict[str, Any] = {
        "unsafe": [], "fraud_cases": 1, "complete": True, "fraud_guard_missed": [],
        "fraud_guard_recall": 100.0, "options": {"classifier": True, "actor": True},
    }  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, digest, set_version, created_at,"
                     " metrics, gate_passed, path) VALUES ('ce3be1df00', ?, ?, ?, ?, ?, 1, 'p')",
                     (claude_pins.PAIR["C"], DIGEST, version or evalrun.set_version(pub), now,
                      json.dumps(base | metrics)))  # fmt: skip
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, evalrun.logical(pub), now, actor="test")


def test_summarize_records_counts_by_half_only_for_a_set_with_a_private_half(split: Path) -> None:
    cases, _ = evalrun.load(split, fraud_only=False)
    results = [evalrun.score_parts(c, None, None, set(), None) for c in cases]
    pub_ids, priv_ids = mg.membership(split) or (frozenset(), frozenset())
    m = evalrun.summarize(results, 0, membership=(pub_ids, priv_ids))
    assert m["public_cases"] + m["private_cases"] == len(cases)
    assert m["private_cases"] == sum(c.id in priv_ids for c in cases) > 0
    assert "public_cases" not in evalrun.summarize(results, 0)


def test_a_run_matching_the_set_passes_the_guard(conn: sqlite3.Connection, clock: FakeClock,
                                                  split: Path) -> None:  # fmt: skip
    _run(conn, clock, split, _counts(split) | {
        "public_cases": 300, "private_cases": 12, "merged_version": evalrun.set_version(split),
        "private_commit": "0" * 40})  # fmt: skip
    assert gate.synthetic(conn, DIGEST).ok


@pytest.mark.parametrize("half", ["public", "private"])
def test_a_run_that_read_none_of_a_half_is_refused(conn: sqlite3.Connection, clock: FakeClock,
                                                   split: Path, half: str) -> None:  # fmt: skip
    counts = {"public_cases": 300, "private_cases": 12} | {f"{half}_cases": 0}
    _run(conn, clock, split, _counts(split) | counts)
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and f"read none of the {half} set" in check.detail


def test_a_pre_guard_run_with_other_counts_is_refused_with_the_exact_message(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    want = _counts(split)
    _run(conn, clock, split, {"confirmed": want["confirmed"] - 2,
                              "fraud_guard_cases": want["fraud_guard_cases"] - 2})  # fmt: skip
    check = gate.synthetic(conn, DIGEST)
    assert check.detail == (
        f"run ce3be1df's counts don't match the set: confirmed {want['confirmed'] - 2} (set"
        f" {want['confirmed']}), fraud_guard_cases {want['fraud_guard_cases'] - 2} (set"
        f" {want['fraud_guard_cases']})"
    )


def test_a_pre_guard_run_matching_the_set_is_accepted(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    _run(conn, clock, split, _counts(split))
    assert gate.synthetic(conn, DIGEST).ok


def test_the_version_comes_first(conn: sqlite3.Connection, clock: FakeClock,
                                 split: Path) -> None:  # fmt: skip
    _run(
        conn,
        clock,
        split,
        {"confirmed": 1, "fraud_guard_cases": 1},
        version=label_file.set_version(split),
    )  # the public half's version: another version
    check = gate.synthetic(conn, DIGEST)
    assert "on an older version of the set" in check.detail


def test_a_fraud_only_run_is_checked_on_fraud_guard_cases_only(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    fo = _counts(split, fraud_only=True)
    opts = {"classifier": True, "actor": True, "fraud_only": True}
    _run(conn, clock, split, {"confirmed": 1, "fraud_guard_cases": fo["fraud_guard_cases"],
                              "options": opts})  # fmt: skip
    assert gate.synthetic(conn, DIGEST).ok


def test_a_stale_merged_set_is_a_failed_check_not_an_error(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    _run(conn, clock, split, _counts(split))
    card = next(((mg.paths(split).private or split) / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok and "ecf eval build" in check.detail


def test_the_fingerprint_follows_both_halves_and_merge_json(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    _run(conn, clock, split, _counts(split))
    before = gate.inputs(conn, "ap", DIGEST)
    priv = mg.paths(split).private or split
    lab = priv / "labels.jsonl"
    lab.write_text(lab.read_text())  # rewritten: a new mtime
    assert gate.inputs(conn, "ap", DIGEST) != before
    # same bytes: nothing copied, but merge.json records the new stat once, so the file is no
    # longer hashed on every freshness check
    assert mg.refresh_merged(split) == "fresh"
    restat: list[tuple[str, str, str]] = []
    assert mg.is_fresh(mg.paths(split), restat) and restat == []
    before = gate.inputs(conn, "ap", DIGEST)
    assert mg.refresh_merged(split) == "fresh"
    assert gate.inputs(conn, "ap", DIGEST) == before
    card = next((priv / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    assert mg.refresh_merged(split) == "refreshed"  # merge.json rewritten
    assert gate.inputs(conn, "ap", DIGEST) != before


def test_the_gate_and_a_for_gate_run_need_the_set_committed_and_pushed(
    conn: sqlite3.Connection, clock: FakeClock, split: Path
) -> None:
    from ecf.errors import InvalidInputError  # noqa: PLC0415
    from ecf_server import claude_eval  # noqa: PLC0415

    _run(conn, clock, split, _counts(split))
    check = gate.synthetic(conn, DIGEST)
    assert check.ok and "committed and pushed" in check.detail
    clean = claude_eval._check_clean(split)  # pyright: ignore[reportPrivateUsage]
    assert clean is not None and clean.private_commit
    priv = mg.paths(split).private or split
    git(priv.parent, "commit", "-q", "--allow-empty", "-m", "unpushed")
    gitcheck.reset_backoff()
    check = gate.synthetic(conn, DIGEST, fresh=True)
    assert not check.ok and "not pushed" in check.detail
    with pytest.raises(InvalidInputError, match="committed and pushed"):
        claude_eval._check_clean(split)  # pyright: ignore[reportPrivateUsage]


def test_a_public_only_for_gate_run_is_refused(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    """D2b: a set in a checkout's tests/eval/ without its private half (a worktree or clone
    without the private repository) can't be a gate run; --not-for-gate still runs."""
    from datetime import date  # noqa: PLC0415

    from ecf.errors import InvalidInputError  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415
    from ecf_server import claude_eval  # noqa: PLC0415
    from tests.test_claude_eval import CASES, SYNTHETIC  # noqa: PLC0415
    from tests.test_claude_eval import start as claude_start  # noqa: PLC0415

    pub = tmp_path / "tests" / "eval" / "synthetic"
    (pub / "cases").mkdir(parents=True)
    for name in CASES:
        src = SYNTHETIC / "cases" / f"{name}.md"
        (pub / "cases" / f"{name}.md").write_bytes(src.read_bytes())
    assert not build_all(pub).findings
    label_file.confirm(pub, "starter-bec", date(2026, 10, 1))
    del conn  # the database, migrated
    claude_eval.reset()
    try:
        with pytest.raises(InvalidInputError, match="no eval/private/synthetic"):
            claude_start(db_path, clock, pub, preset="C")
        assert claude_eval.current() is None
        assert claude_start(db_path, clock, pub, preset="C", for_gate=False)["state"] == "ready"
    finally:
        claude_eval.reset()
