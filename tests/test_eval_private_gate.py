"""The gate guard on a set with a private half (EVAL-PRIVATE.md "The gate guard", V2, OD-643):
the version first, then counts by membership, then the references from the set itself."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf_server import claude_pins, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]
from tests.test_gate import DIGEST


@pytest.fixture
def split(tmp_path: Path) -> Path:
    pub, _priv, _total = _split_set(tmp_path)
    mg.refresh_merged(pub)
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
    _run(conn, clock, split, _counts(split) | {"public_cases": 300, "private_cases": 12})
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
    before = gate.inputs(conn, "ap", DIGEST)
    assert mg.refresh_merged(split) == "fresh"  # same bytes: nothing rewritten
    assert gate.inputs(conn, "ap", DIGEST) == before
    card = next((priv / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    assert mg.refresh_merged(split) == "refreshed"  # merge.json rewritten
    assert gate.inputs(conn, "ap", DIGEST) != before
