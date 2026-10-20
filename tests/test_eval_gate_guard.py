"""A generated eval set is never the go-live gate's evidence (eval generate plan P1): a run on it
is never for the gate or pinned, never re-points the gate's set, and the gate's synthetic check
refuses its folder."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from ecf.eval import genset, labels
from ecf.eval.builder import build_all
from ecf_server import claude_eval, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock
from tests.test_claude_eval import BEC, REQUEST, drain
from tests.test_claude_eval import start as claude_start
from tests.test_evalrun import _clear  # pyright: ignore[reportPrivateUsage]
from tests.test_evalrun import _start as null_start  # pyright: ignore[reportPrivateUsage]
from tests.test_gate import DIGEST, _eval  # pyright: ignore[reportPrivateUsage]

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
CASES = ("starter-bec", "starter-control", "starter-injection")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "generated"
    (r / "cases").mkdir(parents=True)
    for name in CASES:
        shutil.copy(SYNTHETIC / "cases" / f"{name}.md", r / "cases" / f"{name}.md")
    assert not build_all(r).findings
    labels.confirm(r, "starter-bec", date(2026, 10, 1))
    return r


def _mark(root: Path) -> Path:
    (root / genset.MANIFEST).write_text(json.dumps({"kind": genset.KIND}), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _reset(conn: sqlite3.Connection) -> None:
    del conn
    claude_eval.reset()
    _clear()


def _remembered(conn: sqlite3.Connection) -> str | None:
    return slack_admin.setting(conn, evalrun.EVAL_ROOT)


def test_is_generated_reads_the_manifest_or_the_cards_author(tmp_path: Path) -> None:
    assert not genset.is_generated(tmp_path)
    (tmp_path / genset.MANIFEST).write_text("{not json", encoding="utf-8")
    assert genset.is_generated(tmp_path)  # unreadable: fail closed
    (tmp_path / genset.MANIFEST).write_text('{"kind": "other"}', encoding="utf-8")
    assert not genset.is_generated(tmp_path)
    (tmp_path / "labels.jsonl").write_text('{"id": "a", "author": "generated"}\n',
                                           encoding="utf-8")  # fmt: skip
    assert genset.is_generated(tmp_path)  # the manifest removed or replaced: the cards say so


def test_a_claude_run_on_a_generated_set_is_unpinned_and_never_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = claude_start(db_path, clock, _mark(root))
    assert r["pinned"] is False and r["for_gate"] is False
    assert not _remembered(conn)  # the gate's set isn't re-pointed
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    drain(db_path, clock, lambda cid: by_case[cid])
    row = conn.execute("SELECT digest, metrics FROM eval_runs").fetchone()
    m = json.loads(row["metrics"])
    assert m["options"]["for_gate"] is False and m["gate_passed"] is False
    assert str(row["digest"]).startswith("eval-")  # not the pins' key: the gate never reads it


def test_not_for_gate_on_the_synthetic_set_also_keeps_the_gate_away(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = claude_start(db_path, clock, root, for_gate=False)
    assert r["pinned"] is False and r["for_gate"] is False
    assert not _remembered(conn)
    claude_eval.reset()
    r = claude_start(db_path, clock, root)  # without it, as before
    assert r["pinned"] is True and "for_gate" not in r
    assert _remembered(conn) == str(root.resolve())


def test_an_ecf_eval_run_on_a_generated_set_is_never_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    null_start(db_path, clock, _mark(root))
    result = evalrun.RUN.snapshot()["result"]
    assert result["options"]["for_gate"] is False and result["gate_passed"] is False
    assert not _remembered(conn)


def test_the_gate_refuses_a_generated_sets_folder(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    _eval(conn, clock, root)
    assert gate.synthetic(conn, DIGEST, "C").ok
    _mark(root)
    check = gate.synthetic(conn, DIGEST, "C")
    assert not check.ok and "generated set" in check.detail
