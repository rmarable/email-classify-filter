"""Window tuning step 8: the classifier reads 4,000 characters (plan
planning-docs/CLASSIFIER-WINDOW-TUNING.md, operator decision 2026-10-26). The window and the
excerpt version, Jev kept at its 1,500 cut, the stored window and the excerpt's limit (0042),
`ecf item show` and the size of a classifier's `get_message` reply."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf import cli
from ecf.cli_items import describe
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import load_result
from ecf.text import plain
from ecf_server import claude_review, db, evalrun, excerpt, inbox, jev, message, ruletest, triggers
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from tests.test_claude_review import add, read_msg, waiting_item, with_tokens
from tests.test_eval_jev import run_jev
from tests.test_eval_window import FILLER, LONG
from tests.test_inbox import PLAIN, _address, _item  # pyright: ignore[reportPrivateUsage]
from tests.test_jev import body
from tests.test_message import build

ASK = "Please confirm the new remittance account before Friday's payment run."


def _filler(n: int) -> str:
    words = "The quarterly order review went well and the team thanks you for the update. "
    return (words * (n // len(words) + 1))[:n]


def test_the_classifier_reads_4000_characters() -> None:
    assert message.CLASSIFIER_CHARS == message.ACTOR_CHARS == 4000
    assert cli.CLASSIFIER_WINDOW == 4000
    late = _filler(2800) + "\n\n" + ASK + "\n\n" + _filler(3000)
    cls, act = message.parse(build(text=late, attachment=None)).excerpts(triggers.redact_injection)
    assert ASK in cls and len(cls) <= 4000 and cls == act


def test_the_excerpt_version_moves_once() -> None:
    """A new excerpt version moves C's pin key, so every C address goes back to assist until a
    gate run on the new window passes."""
    assert excerpt.VERSION == 3


@pytest.mark.parametrize("text", [
    "", "short text", "a" * 1500, "a" * 1501, "a" * 6000, _filler(1500), _filler(1501),
    _filler(3999), _filler(6000), "漢" * 5000, ("word " * 299) + "x" * 600,
], ids=lambda t: f"{len(t)}-{t[:1] or 'empty'}")  # fmt: skip
def test_jev_reads_the_classifiers_4000_characters(text: str) -> None:
    """OD-561 (operator 2026-10-26; it read 1,500 under OD-558): Jev reads the window Claude's
    classifier reads, so the stored excerpt reaches it whole."""
    assert jev.WINDOW == jev.EXCERPT_CHARS == message.CLASSIFIER_CHARS == 4000
    stored = message.cut(text, message.CLASSIFIER_CHARS)
    assert jev.excerpt_text(stored) == stored
    assert jev.excerpt_cap()["excerpt_chars"] == 4000


def test_the_excerpts_limits_hold(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """A stored classifier excerpt is at most 4,000 characters and its window 500 to 4,000; the
    window written at fetch: test_fetch.test_page_creates_items_with_excerpts."""
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    with pytest.raises(sqlite3.IntegrityError), write_tx(conn):
        conn.execute("UPDATE excerpts SET classifier_text = ? WHERE stable_id = ?",
                     ("x" * 4001, sid))  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError), write_tx(conn):
        conn.execute("UPDATE excerpts SET classifier_chars = 8000 WHERE stable_id = ?", (sid,))


def _before(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 42])
    c = db.connect(db_path)
    db.migrate(c)
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    return c


def test_0042_marks_earlier_excerpts_as_cut_at_1500(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    c.execute("PRAGMA foreign_keys=OFF")  # excerpt rows alone; their items don't matter here
    with write_tx(c):
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES ('a1', ?)",
                  ("x" * 1501,))  # an excerpt cut before OD-552 could be one over  # fmt: skip
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES ('a2', NULL)")
    c.execute("PRAGMA foreign_keys=ON")
    assert db.migrate(c) == ["0042_classifier_window.sql"]
    got = dict(c.execute("SELECT stable_id, classifier_chars FROM excerpts").fetchall())
    assert got == {"a1": 1500, "a2": None}
    c.close()


def test_item_show_prints_the_first_1500_characters_unless_full(
        conn: sqlite3.Connection, clock: FakeClock) -> None:  # fmt: skip
    """Q3 (operator 2026-10-26): about 60 lines of excerpt is too much by default."""
    text = "\n".join(f"line {i:04d} " + "y" * 60 for i in range(55))  # 3,849 characters
    _address(conn, clock)
    _item(conn, clock, "a" * 64, PLAIN)
    with write_tx(conn):
        conn.execute("UPDATE excerpts SET classifier_text = ?", (text,))
    d = inbox.show(conn, "aaaaaaaa")

    def _shown(t: str, *, full: bool = False) -> str:
        return "\n".join(plain(x) for x in describe(d | {"excerpt": t}, full=full))

    shown = _shown(text)
    assert "line 0000" in shown and "line 0054" not in shown
    rest = len(text) - len(text[:1500].rsplit("\n", 1)[0])
    assert f"{rest:,} more characters: ecf item show {'a' * 8} --full" in shown
    assert "line 0054" in _shown(text, full=True) and "more characters" not in _shown(
        text, full=True
    )
    assert "more characters" not in _shown("short")


def test_a_classifiers_message_at_the_full_window_stays_small(
        conn: sqlite3.Connection, clock: FakeClock) -> None:  # fmt: skip
    """The largest classify reply: a 4,000-character excerpt of three-byte characters and every
    signal. The bound leaves room for a schema extension's text."""
    add(conn, clock, "c", "C")
    every = {"sender_origin": "external", "bec_opener": True, "tax_form_request": True,
             "impersonates_internal": True, "gift_card_keyword": True}  # fmt: skip
    sid = waiting_item(conn, clock, "c", facts=every)
    with write_tx(conn):
        conn.execute("UPDATE excerpts SET classifier_text = ? WHERE stable_id = ?",
                     ("漢" * 4000, sid))  # fmt: skip
    q = with_tokens(claude_review.review_queue(conn, clock, "s1"))
    got = read_msg(conn, clock, "s1", sid, q["items"][0]["claim_token"])
    assert got["need"] == "classify" and len(got["untrusted_email"]["text"]) == 4000
    assert len(json.dumps(got).encode()) <= 48 * 1024


def test_a_jev_run_measures_what_jev_reads(conn: sqlite3.Connection, db_path: Path,
                                           clock: FakeClock, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """R1: a Jev run measures against Jev's own window. Jev reads 4,000 since OD-561, the same as
    Claude, so a smaller window is set here: a signal at about 2,000 characters is in Claude's
    4,000 excerpt but not in a 1,500 one, and the Jev run records it unseen (and its excerpt
    length as Jev's)."""
    monkeypatch.setattr(jev, "WINDOW", 1500)
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    (r / "cases" / "long-signal.md").write_text(LONG.replace(FILLER, FILLER[:2000]))
    assert not build_all(r).findings
    labels.confirm(r, "long-signal", date(2026, 10, 1))
    cases, _ = evalrun.load(r, fraud_only=False)
    scratch = ruletest.Scratch(FakeClock())
    try:
        _, _, claude_m = evalrun.case_inputs_full(cases[0], scratch, triggers.redact_injection)
    finally:
        scratch.close()
    assert claude_m["signal_seen"] is True

    def reply(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body())

    run_jev(db_path, clock, r, reply)
    row = conn.execute("SELECT path FROM eval_runs ORDER BY rowid DESC").fetchone()
    result = load_result(Path(row["path"]))
    assert result.cases[0].signal_seen is False
    summary: dict[str, Any] = dict(result.summary or {})
    assert summary["measurements"]["excerpt_chars"]["max"] <= jev.WINDOW


def test_0042_checkpoints_and_refuses_an_over_long_excerpt_cleanly(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """R3: 0042 writes every excerpt row, so a checkpoint follows. R4: a stored excerpt over
    4,000 characters (only a hand-made or foreign bundle could hold one) fails as a
    MigrationError, an IntegrityError import reports as rows that don't load; nothing changes."""
    assert 42 in db.CHECKPOINT_AFTER
    c = _before(db_path, monkeypatch)
    c.execute("PRAGMA foreign_keys=OFF")
    with write_tx(c):
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES ('a1', ?)",
                  ("x" * 4001,))  # fmt: skip
    c.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(db.MigrationError, match=r"0042_classifier_window\.sql"):
        db.migrate(c)
    assert not c.execute("SELECT 1 FROM schema_migrations WHERE version = 42").fetchone()
    cols = {r[1] for r in c.execute("PRAGMA table_info(excerpts)")}
    assert "classifier_chars" not in cols
    c.close()


def test_status_shows_the_window_of_a_run_saved_at_1500() -> None:
    """R6: a run saved before OD-558 has no window option (1,500 was the default then) but
    records `excerpt_cap`; `ecf eval status` names its window, so it can't pass for a 4,000 run."""
    old = {"options": {"for_gate": True}, "excerpt_cap": {"classifier_chars": 1500,
                                                          "actor_chars": 4000}}  # fmt: skip
    new = {"options": {"for_gate": True}, "excerpt_cap": {"classifier_chars": 4000,
                                                          "actor_chars": 4000}}  # fmt: skip
    st = {"current": {"state": "idle"}, "claude": None, "recent": [
        {"created_at": "2026-10-24T10:00:00Z", "run_id": "aaaaaaaa", "gate_passed": False,
         "metrics": old},
        {"created_at": "2026-10-26T10:00:00Z", "run_id": "bbbbbbbb", "gate_passed": True,
         "metrics": new}]}  # fmt: skip
    lines = cli.status_lines(st)
    at = [i for i, line in enumerate(lines) if "classifier window 1500" in line]
    assert len(at) == 1 and "aaaaaaaa" in lines[at[0] - 2]
    assert not any("classifier window 4000" in line for line in lines)


def test_item_show_cuts_a_long_first_line_at_a_word(conn: sqlite3.Connection,
                                                    clock: FakeClock) -> None:  # fmt: skip
    """R8: no line break near the cut: the head ends at a space, not mid-word; a leading line
    break doesn't empty it."""
    _address(conn, clock)
    _item(conn, clock, "b" * 64, PLAIN)
    d = inbox.show(conn, "bbbbbbbb")
    words = "\n" + " ".join(f"word{i:04d}" for i in range(400))  # one 3,600-character line
    shown = [plain(x) for x in describe(d | {"excerpt": words})]
    body = shown[shown.index("excerpt (stored text, shown only here):") + 1 :]
    assert body[-1].startswith("  [") and "more characters" in body[-1]
    assert body[-2].rstrip().split(" ")[-1].startswith("word") and len(body[-2].split()[-1]) == 8
    assert sum(len(x) for x in body[:-1]) > 1200
