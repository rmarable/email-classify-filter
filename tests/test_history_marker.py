"""The scan and reprocess marker (OD-507, plan v3.0.0-STEP5 §2), row by row, the run's Slack
summary (§3.4) and `ecf approve --scan` (§3.5)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

import pytest

from ecf.errors import StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import (
    approvals,
    claude_queue,
    digest_actions,
    digests,
    escalations,
    inbox,
    marked,
    needs_you,
    scan_summary,
    scans,
    slack_admin,
)
from ecf_server.actions import Planned
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.items import create_item, transition
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper
from tests.test_claude_review import add, queue, waiting_item

ME = "U0ME1"
FRAUD: dict[str, Any] = {"triggers": {"fraud": ["bank details from an unconfirmed sender"]}}


def _setup(conn: sqlite3.Connection, clock: FakeClock, stage: str = "live") -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', ?, 'C', ?)",
                     (stage, now))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip


def _run(conn: sqlite3.Connection, clock: FakeClock, mode: str = "acting") -> str:
    with write_tx(conn):
        return scans.new_run(conn, clock, kind="scan", address_id="ap", until=None, mode=mode,
                             since="2026-09-01T00:00:00.000000Z")  # fmt: skip


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any], *,
          escalation: bool = False) -> str:  # fmt: skip
    sid = sid.ljust(64, "0")

    def also(c: sqlite3.Connection) -> None:
        if escalation:
            c.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                      " VALUES (?, 'ap', 'pending', ?)", (sid, to_ts(clock.now())))  # fmt: skip

    create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                content_hash="h", facts=json.dumps(facts), subject=f"Invoice {sid[:4]}",
                sender="billing@vendor-a.example", also=also)  # fmt: skip
    return sid


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _approval(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any],
              *, card: bool = True) -> str:  # fmt: skip
    _item(conn, clock, sid, facts)
    sid = sid.ljust(64, "0")
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    approvals.request(conn, clock, sid, [Planned("archive")], card=card)
    return sid


# ---- §2 row by row --------------------------------------------------------------------------


def test_new_mail_during_a_scan_escalates_at_once_and_old_mail_goes_to_the_summary(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    run = _run(conn, clock)
    old = _item(conn, clock, "a1", FRAUD | {"backfill": 1, "scan_run": run}, escalation=True)
    new = _item(conn, clock, "b1", FRAUD, escalation=True)
    assert escalations.sweep(conn, clock) == 1  # only the new one is posted
    keys = [p["key"] for p in _posts(conn)]
    assert keys == [f"item:{new}"]
    state = conn.execute("SELECT state, thread_key FROM escalations WHERE stable_id = ?",
                         (old,)).fetchone()  # fmt: skip
    assert tuple(state) == ("merged", f"scan:{run}")  # never posted twice (PF8)


def test_old_mails_approvals_get_no_card_no_reoffer_card_and_stay_out_of_approve_all(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    run = _run(conn, clock)
    old = _approval(conn, clock, "a2", {"backfill": 1, "scan_run": run}, card=False)
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (old,)).fetchone()
    assert not digest_actions.eligible(conn, row)
    assert approvals.post_held_cards(conn, clock) == 0  # no card, even when the backlog is low
    with write_tx(conn):
        conn.execute("UPDATE grants SET expires_at = ? WHERE stable_id = ?",
                     (to_ts(clock.now() - timedelta(seconds=1)), old))  # fmt: skip
    approvals.expire(conn, clock)
    assert not [p for p in _posts(conn) if p.get("key") == f"item:{old}"]  # no "decide again"
    from ecf_server.state_machine import Origin  # noqa: PLC0415

    transition(conn, clock, StableId(old), Status.AWAITING_STEPUP,
               TransitionContext(origin=Origin.APPROVAL), actor="test")  # fmt: skip
    assert approvals.pending(conn)["batch"] == []  # approve --scan instead


def test_digests_list_no_old_mail(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    since = clock.now() - timedelta(minutes=1)
    weak = {"triggers": {"fraud_weak": ["first-time sender asks for payment"]}}
    _item(conn, clock, "a3", weak | {"backfill": 1})
    assert digests.build(conn, "ap", since, clock.now()) is None
    _item(conn, clock, "b3", weak)
    card = digests.build(conn, "ap", since, clock.now())
    assert card is not None and "1 new message(s)" in card.text


def test_the_claude_queue_counts_and_review_order_leave_old_mail_last(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "ap", "C")
    old = waiting_item(conn, clock, "ap", 1, facts={"backfill": 1, "scan_run": "r1"})
    new = waiting_item(conn, clock, "ap", 2)
    assert claude_queue.backlog(conn) == 1  # the card switch never counts old mail
    assert claude_queue.waiting(conn) == {"ap": 1}
    clock.advance(3 * 86400)
    assert [o.count for o in claude_queue.overdue(conn, clock.now(), 2)] == [1]
    assert scans.waiting_line(conn) is not None and "1 scan/reprocess" in str(
        scans.waiting_line(conn))  # fmt: skip
    q = queue(conn, clock, limit=1)  # oldest is the old one; new mail first
    assert [i["id"] for i in q["items"]] == [new]
    assert old != new


def test_needs_you_and_inbox_show_one_count_line_per_run(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    run = _run(conn, clock)
    for i in range(3):
        _approval(conn, clock, f"a4{i}", {"backfill": 1, "scan_run": run}, card=False)
    new = _approval(conn, clock, "b4", {})
    assert [i["id"] for i in inbox.inbox(conn)] == [new]
    lines = scans.inbox_lines(conn, inbox.inbox(conn, old=True))
    assert lines == [f"Scan of ap@acme.example: 3 waiting, 3 approval(s): ecf approve --scan {run}"]
    card = needs_you.card(conn, computer="mac", last_connected=clock.now())
    assert lines[0] in card.text and card.title == "Needs you: 1 waiting"


def test_scanned_mail_never_counts_toward_sender_history(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server.analysis import MessageAnalyzer  # noqa: PLC0415
    from ecf_server.dnscache import DnsCache  # noqa: PLC0415
    from ecf_server.message import parse  # noqa: PLC0415
    from tests.test_triggers import mail  # noqa: PLC0415

    _setup(conn, clock)
    a = MessageAnalyzer.for_address(conn, clock, "ap", DnsCache(conn, clock))
    parsed = parse(mail("Hello", sender="Pat <pat@friends.example>"))
    with write_tx(conn):
        a.record(conn, parsed, {"auth_result": "pass", "backfill": 1})
    assert conn.execute("SELECT count(*) FROM senders").fetchone()[0] == 0
    with write_tx(conn):
        a.record(conn, parsed, {"auth_result": "pass"})
    assert conn.execute("SELECT count(*) FROM senders").fetchone()[0] == 1


def test_the_marker() -> None:
    assert marked.is_marked({"backfill": 1}) and marked.is_marked({"reprocess": "r"})
    assert not marked.is_marked({}) and marked.run_of({"backfill": 1}) is None
    assert marked.run_of({"backfill": 1, "scan_run": "s"}) == "s"
    assert marked.head_key(None) == "scan:-"


# ---- the run's summary (§3.4) ---------------------------------------------------------------


def test_the_summary_head_at_start_replies_as_found_and_edits_until_settled(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    run = _run(conn, clock, mode="records only")
    scan_summary.tick(conn, clock)
    [head] = _posts(conn)
    assert head["key"] == f"scan:{run}" and head["channel"] == "CSUM"
    assert dict(head["card"]["fields"])["State"] == "running"
    for i in range(scan_summary.REPLY_LINES + 1):
        _item(conn, clock, f"c{i:02d}", FRAUD | {"backfill": 1, "scan_run": run})
    scan_summary.tick(conn, clock)
    replies = [p for p in _posts(conn) if p.get("thread_key") == f"scan:{run}"]
    assert [p["key"] for p in replies] == [f"scan:{run}:r1", f"scan:{run}:r2"]
    first = replies[0]["card"]
    assert first["title"] == "Found in old mail, no action taken" and first["mention"] == ME
    assert len(first["text"].splitlines()) == scan_summary.REPLY_LINES
    assert "billing@vendor-a.example" in first["text"]
    edited = [p for p in _posts(conn) if p["key"] == f"scan:{run}"]
    assert dict(edited[-1]["card"]["fields"])["Fraud"] == str(scan_summary.REPLY_LINES + 1)
    n = len(_posts(conn))
    scan_summary.tick(conn, clock)
    assert len(_posts(conn)) == n  # nothing changed: no edit
    waiting = _item(conn, clock, "d1", {"backfill": 1, "scan_run": run})
    transition(conn, clock, StableId(waiting), Status.AWAITING_CLAUDE, TransitionContext(),
               actor="test")  # fmt: skip
    scans.finish(conn, clock, run, "stopped")
    scan_summary.tick(conn, clock)
    doc = scans.load(conn, run)
    assert doc is not None and not doc["settled"]  # an item still waits for /ecf-review
    assert "still waiting" in dict(_posts(conn)[-1]["card"]["fields"])["State"]
    transition(conn, clock, StableId(waiting), Status.CLASSIFIED, TransitionContext(),
               actor="test")  # fmt: skip
    scan_summary.tick(conn, clock)
    doc = scans.load(conn, run)
    assert doc is not None and doc["settled"]
    assert dict(_posts(conn)[-1]["card"]["fields"])["State"] == "stopped"


# ---- ecf approve --scan (§3.5) --------------------------------------------------------------


def test_approve_scan_shows_the_set_and_binds_one_step_up_to_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    run = _run(conn, clock)
    ids = [_approval(conn, clock, f"e{i}", {"backfill": 1, "scan_run": run}, card=False)
           for i in range(2)]  # fmt: skip
    other = _approval(conn, clock, "f1", {})
    p = approvals.scan_pending(conn, run[:6])
    assert [b["id"] for b in p["batch"]] == ids and p["address"] == "ap@acme.example"
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve_scan(conn, clock, FakeNotifier(), run, ids, nonce=None)
    target = ei.value.extra["target"]
    assert ei.value.extra["purpose"] == "approve_scan" and len(target["grant_ids"]) == 2
    from ecf_server import stepup  # noqa: PLC0415

    issued = stepup.issue(conn, clock, FakeStepper(), "approve_scan", target)
    assert "approve 2 action(s) on old mail" in issued.prompt
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):  # bound to exactly that set
        approvals.approve_scan(conn, clock, FakeNotifier(), run, ids[:1], nonce=issued.nonce_id)
    got = approvals.approve_scan(conn, clock, FakeNotifier(), run, ids, nonce=issued.nonce_id)
    assert [g["id"] for g in got] == ids
    status = conn.execute("SELECT status FROM items WHERE stable_id = ?", (other,)).fetchone()
    assert status[0] == "awaiting_approval"  # another run's or new mail's: untouched


def test_the_head_is_queued_before_its_first_replies(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review M2: a reply queued before its head would post top-level."""
    _setup(conn, clock)
    run = _run(conn, clock)
    _item(conn, clock, "g1", FRAUD | {"backfill": 1, "scan_run": run})
    scan_summary.tick(conn, clock)
    assert [p["key"] for p in _posts(conn)] == [f"scan:{run}", f"scan:{run}:r1"]


def test_the_tick_keeps_lines_another_thread_wrote_meanwhile(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M3: each writer changes its own fields inside one write transaction."""
    _setup(conn, clock)
    run = _run(conn, clock)
    real = scans.counts

    def counts_while_the_timer_writes(c: sqlite3.Connection, run_id: str) -> dict[str, int]:
        scans.note(c, clock, run_id, ["plan now differs: 1"])  # the timer, mid-tick
        return real(c, run_id)

    monkeypatch.setattr(scans, "counts", counts_while_the_timer_writes)
    scan_summary.tick(conn, clock)
    doc = scans.load(conn, run)
    assert doc is not None and doc["lines"] == ["plan now differs: 1"] and doc["head"]


def test_a_run_settles_without_slack(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """Review L1."""
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'C', ?)", (now,))  # fmt: skip
    run = _run(conn, clock)
    scans.finish(conn, clock, run, "done")
    scan_summary.tick(conn, clock)
    doc = scans.load(conn, run)
    assert doc is not None and doc["settled"]


def test_expired_approvals_of_a_run_are_counted_and_re_offered(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review M4."""
    from ecf_server import stepup  # noqa: PLC0415
    from ecf_server.state_machine import Origin  # noqa: PLC0415

    _setup(conn, clock)
    run = _run(conn, clock)
    sid = _approval(conn, clock, "a5", {"backfill": 1, "scan_run": run}, card=False)
    transition(conn, clock, StableId(sid), Status.EXPIRED,
               TransitionContext(origin=Origin.APPROVAL), actor="test")  # fmt: skip
    assert scans.counts(conn, run)["approvals"] == 1
    assert [b["id"] for b in approvals.scan_pending(conn, run)["batch"]] == [sid]
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve_scan(conn, clock, FakeNotifier(), run, [sid], nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "approve_scan", ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    [got] = approvals.approve_scan(conn, clock, FakeNotifier(), run, [sid], nonce=issued.nonce_id)
    assert got["id"] == sid and got["status"] != "expired"


def test_old_mail_raises_no_alert_email(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M5 (operator decision 2026-10-14): the run's summary mentions you instead."""
    from ecf_server import alert_items, alert_mail  # noqa: PLC0415

    _setup(conn, clock)
    sent: list[str] = []

    def queue(_c: sqlite3.Connection, _k: Any, _title: str, text: str, **_kw: Any) -> None:
        sent.append(text)

    def emailed(_c: sqlite3.Connection) -> bool:
        return True

    monkeypatch.setattr(alert_items, "emailed", emailed)
    monkeypatch.setattr(alert_mail, "queue", queue)
    old = _item(conn, clock, "i1", FRAUD | {"backfill": 1, "scan_run": "r"}, escalation=True)
    new = _item(conn, clock, "i2", FRAUD, escalation=True)
    assert alert_items.sweep(conn, clock) == 2
    assert len(sent) == 1 and new[:8] in sent[0] and old[:8] not in sent[0]
