"""The go-live gate and going live (V1.3 step 6b; SPEC §9.3, §6.2; OD-069, OD-234)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf import cli_admin
from ecf.errors import InvalidInputError, PolicyDeniedError, StepupRequiredError
from ecf_server import claude_pins, decide, evalrun, gate, review, slack_admin, stages, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.state_machine import Status
from ecf_server.stepper import FakeStepper
from tests.test_decide import KNOWN_BULK, MARKETING, make_classified
from tests.test_decide import make_address as _address

DIGEST = gate.current_digest()
CUSTOMER: dict[str, Any] = MARKETING | {"category": "customer_request", "requires_reply": True}


def make_address(conn: sqlite3.Connection, clock: FakeClock, stage: str) -> None:
    _address(conn, clock, stage)
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"),
                     ("slack_member_id", "U0ME1"), ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "labels.jsonl").write_text('{"id": "x"}\n')
    return tmp_path


def _eval(conn: sqlite3.Connection, clock: FakeClock, root: Path, *,
          unsafe: list[str] | None = None, version: str | None = None,
          complete: bool = True, actor: bool = True,
          missed: list[str] | None = None) -> None:  # fmt: skip
    metrics = {"confirmed": 150, "unsafe": unsafe or [], "fraud_cases": 60, "complete": complete,
               "fraud_guard_cases": 66, "fraud_guard_missed": missed or [],
               "fraud_guard_recall": round(100 * (66 - len(missed or [])) / 66, 1),
               "options": {"classifier": True, "actor": actor}}  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, digest, set_version, created_at,"
                     " metrics, gate_passed, path) VALUES (?, ?, ?, ?, ?, ?, ?, 'p')",
                     (f"run{now}", gate.PAIR, DIGEST, version or evalrun.set_version(root), now,
                      json.dumps(metrics), int(not unsafe and not missed)))  # fmt: skip
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, str(root), now, actor="test")


def _reviewed(conn: sqlite3.Connection, clock: FakeClock, n: int, *, wrong: int = 0,
              prefix: str = "r") -> None:  # fmt: skip
    for i in range(n):
        sid = make_classified(conn, clock, CUSTOMER, KNOWN_BULK, sid=f"{prefix}{i:04d}x")
        review = {"verdict": "fixed" if i < wrong else "correct", "category_ok": i >= wrong,
                  "bulk": False}  # fmt: skip
        with write_tx(conn):
            conn.execute("UPDATE items SET pinned_models = ?, review = ? WHERE stable_id = ?",
                         (json.dumps({"digest": DIGEST}), json.dumps(review), sid))  # fmt: skip


def _live(conn: sqlite3.Connection, clock: FakeClock, **kw: Any) -> dict[str, Any]:
    with pytest.raises(StepupRequiredError) as ei:
        stages.set_stage(conn, clock, "ap", "live", nonce=None, **kw)
    issued = stepup.issue(conn, clock, FakeStepper(), "stage_set", ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return stages.set_stage(conn, clock, "ap", "live", nonce=issued.nonce_id, **kw)


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def test_a_new_address_misses_every_part_of_the_gate(conn: sqlite3.Connection,
                                                     clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "assist")
    g = gate.compute(conn, "ap")
    assert not g.met and not g.safety_met
    by = {c.name: c for c in g.checks}
    assert by["reviewed"].detail == "0/100 reviewed" and by["reviewed"].waivable
    assert "ecf eval run --fraud-only" in by["synthetic"].detail and not by["synthetic"].waivable
    with pytest.raises(PolicyDeniedError, match="no override can waive"):
        stages.set_stage(conn, clock, "ap", "live", nonce=None, override=True, reason="x")


def test_a_met_gate_goes_live_with_step_up_bound_to_its_snapshot(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    _reviewed(conn, clock, 100, wrong=10)  # 90% >= 85%
    _eval(conn, clock, root)
    g = gate.compute(conn, "ap")
    assert g.met, g.text()
    with pytest.raises(StepupRequiredError) as ei:
        stages.set_stage(conn, clock, "ap", "live", nonce=None)
    target = ei.value.extra["target"]
    assert (target["snapshot"], target["digest"], target["override"]) == (g.snapshot, DIGEST, False)
    issued = stepup.issue(conn, clock, FakeStepper(), "stage_set", target)
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    _reviewed(conn, clock, 1, prefix="s")  # the gate changes between the dialog and the action
    with pytest.raises(StepupRequiredError, match="for something else"):
        stages.set_stage(conn, clock, "ap", "live", nonce=issued.nonce_id)
    r = _live(conn, clock)
    assert (r["stage"], r["changed"]) == ("live", True)
    row = gate.stored(conn, "ap")
    assert row is not None and row["ollama_digest"] == DIGEST and row["passed_at"]
    assert _posts(conn)[-1]["card"]["text"].endswith("Go-live gate: met.")


def test_an_override_waives_only_the_count_and_accuracy(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    _reviewed(conn, clock, 20, wrong=10)  # 20/100, 50%
    _eval(conn, clock, root)
    with pytest.raises(PolicyDeniedError, match="An override"):
        stages.set_stage(conn, clock, "ap", "live", nonce=None)
    with pytest.raises(InvalidInputError, match="reason"):
        stages.set_stage(conn, clock, "ap", "live", nonce=None, override=True)
    r = _live(conn, clock, override=True, reason="small mailbox, watched closely")
    assert r["stage"] == "live"
    data = json.loads(conn.execute("SELECT data FROM audit WHERE event = 'stage.changed'"
                                   " ORDER BY id DESC").fetchone()[0])  # fmt: skip
    assert data["override"] is True and data["reason"] == "small mailbox, watched closely"
    assert "went live by override" in _posts(conn)[-1]["card"]["text"]
    row = gate.stored(conn, "ap")  # the model is recorded, but the gate wasn't passed
    assert row is not None and row["ollama_digest"] == DIGEST and row["passed_at"] is None


def test_the_synthetic_run_must_be_safe_and_on_the_current_set(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    _eval(conn, clock, root, version="0123456789abcdef")
    assert "older version of the set" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    _eval(conn, clock, root, unsafe=["mid-domain-renewal-notice"])
    assert "1 unsafe case(s): mid-domain-renewal-notice" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    _eval(conn, clock, root)
    assert gate.synthetic(conn, DIGEST).ok
    (root / "labels.jsonl").write_text('{"id": "y"}\n')  # a card changed since
    assert not gate.synthetic(conn, DIGEST).ok


def test_the_synthetic_run_needs_full_fraud_guard_recall(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    """D2 (2026-10-06): a case expecting fraud_guard that ended elsewhere fails the check, even
    with 0 unsafe; a run saved before the figure existed fails closed."""
    make_address(conn, clock, "assist")
    _eval(conn, clock, root, missed=["sales-urgent-po-forwarder"])
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok
    assert "missed the fraud guard on 1 of 66 case(s): sales-urgent-po-forwarder" in check.detail
    clock.advance(60)
    _eval(conn, clock, root)
    with write_tx(conn):  # a run saved before the recall figure
        conn.execute("UPDATE eval_runs SET metrics = json_remove(metrics,"
                     " '$.fraud_guard_missed')")  # fmt: skip
    assert "predates the fraud-guard recall check" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    _eval(conn, clock, root)
    check = gate.synthetic(conn, DIGEST)
    assert check.ok and "fraud-guard recall 100%" in check.detail


def test_only_a_complete_run_with_both_models_passes_the_synthetic_check(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    _eval(conn, clock, root, complete=False)  # `ecf eval stop`, or the runtime cap
    assert "was stopped before its last case" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    _eval(conn, clock, root, actor=False)  # the actor's injection obedience never tested
    assert "ran without the classifier or the actor" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    with write_tx(conn):  # a run saved before V1.3 step 12b: no completeness recorded
        conn.execute("UPDATE eval_runs SET metrics = json_remove(metrics, '$.complete',"
                     " '$.options')")  # fmt: skip
    assert "predates the completeness check" in gate.synthetic(conn, DIGEST).detail
    clock.advance(60)
    _eval(conn, clock, root)
    assert gate.synthetic(conn, DIGEST).ok


def test_fraud_misses_and_unsafe_proposals_count(conn: sqlite3.Connection,
                                                 clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "assist")
    _reviewed(conn, clock, 3, wrong=1)
    with write_tx(conn):  # the first was fixed to a bank change: rule 1 would have caught it
        conn.execute("UPDATE items SET human_correction = ? WHERE stable_id = ?",
                     (json.dumps({"category": "vendor_change_request"}),
                      "r0000x".ljust(64, "0")))  # fmt: skip
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?",
                     (json.dumps({"plan": {"payment_or_fraud": True,
                                           "actor": {"action": "archive"}}}),
                      "r0001x".ljust(64, "0")))  # fmt: skip
    assert gate.fraud_misses(conn, "ap", DIGEST) == 1
    assert gate.unsafe_proposals(conn, "ap", DIGEST) == 1
    assert not gate.compute(conn, "ap").safety_met


def test_going_live_runs_recent_held_emails_and_resolves_older_ones_on_request(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    old = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="old")
    assert decide.apply(conn, clock, old) is Status.HELD  # archive is held in assist
    clock.advance(10 * 86400)
    new = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="new")
    assert decide.apply(conn, clock, new) is Status.HELD
    _reviewed(conn, clock, 100)
    _eval(conn, clock, root)
    assert stages.held_by_age(conn, "ap", clock.now()) == {"recent": 1, "older": 1}
    r = _live(conn, clock, held="resolve_older")
    assert r["held"] == {"ran": 1, "resolved": 1, "still_held": 0, "failed": 0}
    status = {s: conn.execute("SELECT status FROM items WHERE stable_id = ?", (s,)).fetchone()[0]
              for s in (old, new)}  # fmt: skip
    assert status == {old: "resolved_manual", new: "executing"}


def test_a_fixed_held_email_runs_the_corrected_plan(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    make_address(conn, clock, "assist")
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="fix")
    assert decide.apply(conn, clock, sid) is Status.HELD
    with write_tx(conn):  # a Fix on the held email (OD-157): not marketing, a customer asking
        conn.execute("UPDATE items SET human_correction = ? WHERE stable_id = ?",
                     (json.dumps({"category": "customer_request", "requires_reply": True}),
                      sid))  # fmt: skip
    _reviewed(conn, clock, 100)
    _eval(conn, clock, root)
    _live(conn, clock)
    proposal = json.loads(conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                                       (sid,)).fetchone()[0])  # fmt: skip
    assert proposal["plan"]["rule"] == "requires_reply"
    assert "archive" not in [a["name"] for a in proposal["plan"]["actions"]]


def test_the_tick_announces_once_and_drops_live_when_the_model_changes(
    conn: sqlite3.Connection, clock: FakeClock, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_address(conn, clock, "assist")
    _reviewed(conn, clock, 100)
    _eval(conn, clock, root)
    stages.tick(conn, clock)
    stages.tick(conn, clock)
    ready = [p for p in _posts(conn) if p["card"]["title"] == "Ready for live"]
    assert len(ready) == 1 and "ecf stage set ap live" in ready[0]["card"]["text"]
    _live(conn, clock)

    def changed(conn: sqlite3.Connection, aid: str) -> str:
        return "sha256:" + "f" * 64

    monkeypatch.setattr(claude_pins, "address_key", changed)
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"
    assert "a pinned model changed" in _posts(conn)[-1]["card"]["text"]


def test_the_tick_computes_a_gate_only_when_its_inputs_change(
    conn: sqlite3.Connection, clock: FakeClock, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_address(conn, clock, "assist")
    _reviewed(conn, clock, 99)
    _eval(conn, clock, root)
    computed: list[str] = []
    real = gate.compute

    def counting(c: sqlite3.Connection, aid: str) -> gate.Gate:
        computed.append(aid)
        return real(c, aid)

    monkeypatch.setattr(gate, "compute", counting)
    stages.tick(conn, clock)
    stages.tick(conn, clock)
    assert computed == ["ap"]  # nothing changed: not computed again
    _reviewed(conn, clock, 1, prefix="z")  # the 100th review
    with write_tx(conn):  # as review.record does
        conn.execute("UPDATE items SET updated_at = ? WHERE stable_id LIKE 'z%'",
                     (to_ts(clock.now() + timedelta(seconds=1)),))  # fmt: skip
    stages.tick(conn, clock)
    assert computed == ["ap", "ap"]
    assert [p["card"]["title"] for p in _posts(conn)].count("Ready for live") == 1
    clock.advance(3600)
    _reviewed(conn, clock, 1, prefix="y")
    with write_tx(conn):
        conn.execute("UPDATE items SET updated_at = ? WHERE stable_id LIKE 'y%'",
                     (to_ts(clock.now()),))  # fmt: skip
    stages.tick(conn, clock)
    assert computed == ["ap", "ap"]  # announced for this model: never computed again


def test_recording_a_review_marks_the_email_changed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    sid = make_classified(conn, clock, CUSTOMER, KNOWN_BULK)
    before = conn.execute("SELECT updated_at FROM items").fetchone()[0]
    clock.advance(60)
    review._set_review(conn, clock.now(), sid, {"verdict": "correct"})  # pyright: ignore[reportPrivateUsage]
    assert conn.execute("SELECT updated_at FROM items").fetchone()[0] > before


# ---- the CLI's screens ------------------------------------------------------------------------


def _checks(**ok: bool) -> list[dict[str, Any]]:
    rows = [("reviewed", "20/100 reviewed", True), ("accuracy", "50% accurate (need 85%)", True),
            ("fraud_misses", "0 fraud-guard miss(es)", False),
            ("unsafe_proposals", "0 unsafe proposal(s)", False),
            ("synthetic", "no synthetic-set result for this model", False)]  # fmt: skip
    return [{"name": n, "ok": ok.get(n, True), "detail": d, "waivable": w} for n, d, w in rows]


def test_the_cli_shows_each_check_and_what_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    gate_doc = {"met": False, "safety_met": False,
                "checks": _checks(reviewed=False, accuracy=False, synthetic=False)}  # fmt: skip
    cli_admin._show_gate(  # pyright: ignore[reportPrivateUsage]
        {"gate": gate_doc, "held": {"recent": 2, "older": 3}}, "run"
    )
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "go-live gate: NOT met"
    assert out[1:6] == ["  SHORT 20/100 reviewed", "  SHORT 50% accurate (need 85%)",
                        "  ok   0 fraud-guard miss(es)", "  ok   0 unsafe proposal(s)",
                        "  FAIL no synthetic-set result for this model"]  # fmt: skip
    assert out[6] == "  run the synthetic set for this model: ecf eval run --fraud-only"
    assert out[7] == "held emails: 2 up to 7 days old run when the address goes live; 3 older"
    assert out[8].startswith("  older ones stay held: add --held resolve-older")
    cli_admin._show_gate(  # pyright: ignore[reportPrivateUsage]
        {"gate": {"met": True, "safety_met": True, "checks": _checks()},
                "held": {"recent": 0, "older": 3}}, "resolve_older")  # fmt: skip
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "go-live gate: met" and all(line.startswith("  ok") for line in out[1:6])
    assert len(out) == 7  # the held line, and no hint once older ones are being resolved


def test_the_cli_reports_what_happened_to_held_emails(capsys: pytest.CaptureFixture[str]) -> None:
    show = cli_admin._show_stage_result  # pyright: ignore[reportPrivateUsage]
    show({"address_id": "ap", "stage": "live", "changed": True,
          "held": {"ran": 2, "resolved": 3, "still_held": 0, "failed": 0}})  # fmt: skip
    show({"address_id": "ap", "stage": "assist", "changed": False})
    assert capsys.readouterr().out.splitlines() == [
        "ap: live",
        "held emails: 2 ran, 3 marked handled by hand, 0 still held, 0 failed",
        "ap: assist (unchanged)",
    ]
