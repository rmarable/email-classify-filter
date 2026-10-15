"""`ecf reprocess` (OD-507, plan v3.0.0-STEP5 §4): eligibility, the per-item minimum, the guarded
edges, no mailbox action for moved mail, `done` appended, `close_gone`, batches, `--stop`, the
step-up, the summary lines and `ecf item undo`."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.errors import ConflictError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import digests, execute, fetch, items, leases, mailbox_actions, reprocess, scans
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource
from ecf_server.state_machine import Stage, TransitionContext
from tests.helpers import GOOD

SINCE = "2000-01-01"
LIVE = TransitionContext(stage=Stage.LIVE)


def _address(conn: sqlite3.Connection, clock: FakeClock, stage: str = "live") -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', ?, 'C', ?)",
                     (stage, to_ts(clock.now())))  # fmt: skip


def _item(  # noqa: PLR0913 - an item in any of the states a test needs
        conn: sqlite3.Connection, clock: FakeClock, n: int, *, status: Status = Status.OBSERVED,
          pinned: dict[str, Any] | None = None, facts: dict[str, Any] | None = None,
          done: list[dict[str, Any]] | None = None, classified: bool = True,
          review: str | None = None) -> str:  # fmt: skip
    sid = f"{n:02d}".ljust(64, "a")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts or {}), subject="Lunch",
                      sender="pat@friends.example",
                      locator=json.dumps({"uid": n, "uidvalidity": 1}))  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ?, pinned_models = ?, proposal = ?,"
                     " review = ?, decision_source = 'rule' WHERE stable_id = ?",
                     (json.dumps(GOOD) if classified else None, json.dumps(pinned or {}),
                      json.dumps({"done": done or []}), review, sid))  # fmt: skip
    path = {Status.OBSERVED: [Status.OBSERVED],
            Status.EXECUTED: [Status.CLASSIFIED, Status.PROPOSED, Status.EXECUTING,
                              Status.EXECUTED],
            Status.AWAITING_CLAUDE: [Status.AWAITING_CLAUDE]}[status]  # fmt: skip
    for to in path:
        ctx = TransitionContext(backfill=True) if to is Status.OBSERVED else LIVE
        items.transition(conn, clock, StableId(sid), to, ctx, actor="test")
    clock.advance(1)
    return sid


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


def _key(conn: sqlite3.Connection, **change: Any) -> dict[str, Any]:
    return reprocess.current_key(conn, "ap") | change


def test_eligibility_is_an_allow_list_with_d11s_skips(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, 1)  # no key at all: reclassify
    _item(conn, clock, 2, pinned=_key(conn))  # current: unchanged
    _item(conn, clock, 3, pinned=_key(conn, rules="0" * 64))  # rules only: re-plan
    _item(conn, clock, 4, review='{"verdict": "correct"}')
    _item(conn, clock, 5, facts={"triggers": {"fraud_weak": ["x"]}})
    _item(conn, clock, 6, status=Status.AWAITING_CLAUDE)
    n = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    assert (n["in_range"], n["reclassify"], n["replan"], n["unchanged"]) == (6, 1, 1, 1)
    assert n["skipped"] == {"reviewed or corrected": 1, "fraud or regulator trigger": 1,
                            "status awaiting_claude": 1}  # fmt: skip
    r = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    assert (r["reclassify"], r["replan"]) == (0, 1)  # never a model; no key: unchanged (P5)
    f = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=True, force=True)
    assert (f["reclassify"], f["replan"]) == (0, 3)


def test_rules_only_skips_unclassified_items_and_never_calls_a_model(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, 1, classified=False, pinned=_key(conn, rules="0" * 64))
    n = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=True, force=True)
    assert n["skipped"] == {"not classified": 1} and n["replan"] == 0


def test_a_run_moves_items_by_the_guarded_edges_in_batches(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _address(conn, clock, stage="shadow")
    monkeypatch.setattr(reprocess, "BATCH", 2)
    old = [_item(conn, clock, i) for i in range(3)]  # classified again
    replan = _item(conn, clock, 9, pinned=_key(conn, rules="0" * 64))
    r = reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    assert (r["reclassify"], r["replan"]) == (3, 1)
    assert reprocess.tick(conn, clock) == 2  # one batch per tick
    [running] = reprocess.status(conn)
    assert running["outcome"] == "running" and running["reclassify"] == 2
    reprocess.tick(conn, clock)
    reprocess.tick(conn, clock)  # nothing left: finished
    assert [_status(conn, s) for s in old] == ["awaiting_claude"] * 3
    assert _status(conn, replan) == "observed"  # shadow: planned again, recorded
    rows = conn.execute("SELECT data FROM audit WHERE event = 'item.reprocessed'").fetchall()
    assert len(rows) == 4 and all(len(json.loads(x[0])["new"]["rules"]) == 16 for x in rows)
    facts = json.loads(conn.execute("SELECT facts FROM items WHERE stable_id = ?",
                                    (old[0],)).fetchone()[0])  # fmt: skip
    assert facts["reprocess"] == r["run_id"] and "reprocess_from" not in facts  # H1
    pinned = json.loads(conn.execute("SELECT pinned_models FROM items WHERE stable_id = ?",
                                     (replan,)).fetchone()[0])  # fmt: skip
    assert pinned["rules"] == _key(conn)["rules"]  # not counted as changed next time
    [done] = reprocess.status(conn)
    assert done["outcome"] == "done"
    doc = scans.load(conn, r["run_id"])
    assert doc is not None and doc["kind"] == "reprocess" and doc["outcome"] == "done"


def test_stop_ends_the_run_and_running_twice_is_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, 1)
    reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    with pytest.raises(ConflictError, match="already running"):
        reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    assert reprocess.stop(conn, clock, "ap")["reclassify"] == 0
    assert reprocess.status(conn)[0]["outcome"] == "stopped"
    with pytest.raises(ConflictError, match="no reprocess"):
        reprocess.stop(conn, clock, "ap")


def test_over_the_cap_a_step_up_bound_to_the_counts(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _address(conn, clock)
    monkeypatch.setattr(reprocess, "CAP", 1)
    _item(conn, clock, 1)
    _item(conn, clock, 2)
    with pytest.raises(StepupRequiredError) as ei:
        reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    t = ei.value.extra["target"]
    assert ei.value.extra["purpose"] == "reprocess" and (t["reclassify"], t["touch"]) == (2, 2)


def test_an_executed_item_moved_by_ecf_gets_no_mailbox_action_and_keeps_its_undo(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    moved = [{"name": "label", "target": "newsletter"}, {"name": "archive", "folder": "Archive"}]
    sid = _item(conn, clock, 1, status=Status.EXECUTED, pinned=_key(conn, rules="0" * 64),
                done=moved)  # fmt: skip
    reprocess.start(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    reprocess.tick(conn, clock)
    assert _status(conn, sid) == "executing"  # an empty grant: nothing to run
    src = FakeMailSource()  # empty: any IMAP call on the message would fail verification
    run = mailbox_actions.executor_for(src, "t", 1000, lambda: False)
    assert execute.run_once(conn, clock, run, address_id="ap")
    assert _status(conn, sid) == "executed"
    row = conn.execute("SELECT proposal, facts FROM items WHERE stable_id = ?", (sid,)).fetchone()
    assert json.loads(row["proposal"])["done"] == moved  # the move record stays (Undo)
    assert "reprocess_from" not in json.loads(row["facts"])
    differs = conn.execute("SELECT data FROM audit WHERE event = 'item.reprocess_differs'"
                           ).fetchall()  # fmt: skip
    assert [json.loads(d[0])["hides"] for d in differs] == [False]  # GOOD now wouldn't hide
    doc = scans.load(conn, json.loads(row["facts"])["reprocess"])
    assert doc is not None
    assert any("now wouldn't hide: 1" in x and "ecf item undo" in x for x in doc["lines"])


def test_remember_appends_for_a_reprocessed_executed_item_only(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    first = [{"name": "label", "target": "newsletter"}]
    sid = _item(conn, clock, 1, status=Status.EXECUTED, done=first,
                facts={"reprocess": "r1", "reprocess_from": "executed"})  # fmt: skip
    mailbox_actions._remember(conn, clock, sid, [{"name": "flag"}])  # pyright: ignore[reportPrivateUsage]
    doc = json.loads(conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                                  (sid,)).fetchone()[0])  # fmt: skip
    assert doc["done"] == [*first, {"name": "flag"}]
    mailbox_actions._remember(conn, clock, sid, [{"name": "flag"}])  # pyright: ignore[reportPrivateUsage]
    doc = json.loads(conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                                  (sid,)).fetchone()[0])  # fmt: skip
    assert doc["done"] == [{"name": "flag"}]  # an ordinary item again: replaced, as before


def test_close_gone_leaves_only_moved_reprocessed_items_alone_and_only_in_flight(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review H1: only an executed item whose message ecf moved is left alone, and only until it
    is executed again; a reprocessed observed item whose mail you deleted is closed as usual."""
    _address(conn, clock)
    moved = _item(conn, clock, 1, status=Status.AWAITING_CLAUDE,
                  facts={"reprocess": "r1", "reprocess_from": "executed",
                         "reprocess_moved": 1})  # fmt: skip
    observed = _item(conn, clock, 2, status=Status.AWAITING_CLAUDE, facts={"reprocess": "r1"})
    lease = leases.acquire(conn, clock, "ap", "test")
    assert lease is not None
    assert fetch.close_gone(conn, clock, FakeMailSource(), lease, 1) == 1
    assert _status(conn, moved) == "awaiting_claude"
    assert _status(conn, observed) == "resolved_by_mailbox"
    mailbox_actions._remember(conn, clock, moved, [])  # pyright: ignore[reportPrivateUsage]
    facts = json.loads(conn.execute("SELECT facts FROM items WHERE stable_id = ?",
                                    (moved,)).fetchone()[0])  # fmt: skip
    assert "reprocess_moved" not in facts and "reprocess_from" not in facts
    assert fetch.close_gone(conn, clock, FakeMailSource(), lease, 1) == 1  # the exclusion ended
    assert _status(conn, moved) == "resolved_by_mailbox"


def test_only_executed_items_carry_reprocess_from(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    obs = _item(conn, clock, 1)
    ex = _item(conn, clock, 2, status=Status.EXECUTED,
               done=[{"name": "archive", "folder": "Archive"}])  # fmt: skip
    reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    reprocess.tick(conn, clock)
    f = {s: json.loads(conn.execute("SELECT facts FROM items WHERE stable_id = ?",
                                    (s,)).fetchone()[0]) for s in (obs, ex)}  # fmt: skip
    assert "reprocess_from" not in f[obs] and "reprocess_moved" not in f[obs]
    assert f[ex]["reprocess_from"] == "executed" and f[ex]["reprocess_moved"] == 1


def test_an_actor_decided_item_goes_back_through_the_actor(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review H2: planning it by the rules alone would drop the actor's decision."""
    _address(conn, clock)
    sid = _item(conn, clock, 1, pinned=_key(conn, rules="0" * 64))
    with write_tx(conn):
        conn.execute("UPDATE items SET decision_source = 'actor' WHERE stable_id = ?", (sid,))
    n = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    assert (n["reclassify"], n["replan"]) == (1, 0)
    r = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    assert r["skipped"] == {"actor decided; needs a model": 1} and r["replan"] == 0
    with write_tx(conn):  # a plan that continued to the actor counts too
        conn.execute("UPDATE items SET decision_source = 'rule', proposal = ? WHERE"
                     " stable_id = ?", (json.dumps({"plan": {"to_actor": True}}), sid))  # fmt: skip
    r = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    assert r["skipped"] == {"actor decided; needs a model": 1}


def test_a_plan_continuing_to_the_actor_records_no_differs(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import decide  # noqa: PLC0415
    from ecf_server.policy import Planned  # noqa: PLC0415

    _address(conn, clock)
    sid = _item(conn, clock, 1, status=Status.EXECUTED,
                done=[{"name": "archive", "folder": "Archive"}],
                facts={"reprocess": "r1", "reprocess_from": "executed"})  # fmt: skip
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    got = decide._added(conn, clock, row, [Planned("label", "x", "auto")],  # pyright: ignore[reportPrivateUsage]
                        to_actor=True)  # fmt: skip
    assert got == []  # moved: nothing runs
    assert conn.execute("SELECT count(*) FROM audit WHERE event = 'item.reprocess_differs'"
                        ).fetchone()[0] == 0  # fmt: skip


def test_done_and_planned_moves_compare_by_folder(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review M1: `done` records a move's folder, a plan its target."""
    from ecf_server import decide  # noqa: PLC0415
    from ecf_server.policy import Planned  # noqa: PLC0415

    _address(conn, clock)
    sid = _item(conn, clock, 1, status=Status.EXECUTED,
                done=[{"name": "label", "target": "receipts"},
                      {"name": "move", "folder": "Receipts"}],
                facts={"reprocess": "r1", "reprocess_from": "executed"})  # fmt: skip
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    same = [Planned("label", "receipts", "auto"), Planned("move", "Receipts", "approve")]
    assert decide._added(conn, clock, row, same, to_actor=False) == []  # pyright: ignore[reportPrivateUsage]
    assert conn.execute("SELECT count(*) FROM audit WHERE event = 'item.reprocess_differs'"
                        ).fetchone()[0] == 0  # fmt: skip
    other = [Planned("label", "receipts", "auto"), Planned("move", "Bills", "approve")]
    decide._added(conn, clock, row, other, to_actor=False)  # pyright: ignore[reportPrivateUsage]
    [d] = conn.execute("SELECT data FROM audit WHERE event = 'item.reprocess_differs'").fetchall()
    assert json.loads(d[0])["hides"] is True


def test_a_failed_move_unmarks_the_item_and_names_it_in_status(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M6: no rules digest written, no marker left, the id in `--status`."""
    _address(conn, clock)
    sid = _item(conn, clock, 1, pinned=_key(conn, rules="0" * 64))

    def fail(*_a: Any, **_k: Any) -> None:
        raise ConflictError("item changed concurrently")

    monkeypatch.setattr(reprocess.items, "transition", fail)
    reprocess.start(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    reprocess.tick(conn, clock)
    row = conn.execute("SELECT facts, pinned_models FROM items WHERE stable_id = ?",
                       (sid,)).fetchone()  # fmt: skip
    assert "reprocess" not in json.loads(row["facts"])
    assert json.loads(row["pinned_models"])["rules"] == "0" * 64
    [st] = reprocess.status(conn)
    assert st["failed"] == [sid[:8]] and st["errors"] == 1


def test_an_item_with_an_undo_queued_is_skipped(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock)
    sid = _item(conn, clock, 1, status=Status.EXECUTED,
                done=[{"name": "archive", "folder": "Archive"}])  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = json_set(proposal, '$.undo', 'queued')"
                     " WHERE stable_id = ?", (sid,))  # fmt: skip
    n = reprocess.dry_run(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    assert n["skipped"] == {"undo queued": 1}


# Reprocessing live mail (review M7): a hide added on mail still in the INBOX waits for
# `ecf approve --scan`, and the done marker is appended, also after a reclassify.


def _inbox_item(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource,
                pinned: dict[str, Any]) -> str:  # fmt: skip
    """An executed marketing email still in INBOX that ecf only labelled, under old rules."""
    from ecf_server.message import parse  # noqa: PLC0415
    from tests.test_decide import KNOWN_BULK, MARKETING  # noqa: PLC0415
    from tests.test_triggers import mail  # noqa: PLC0415

    raw = mail("Autumn sale", sender="News <news@vendor-a.example>")
    uid = src.deliver(raw)
    parsed = parse(raw)
    sid = "f".ljust(64, "1")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash=parsed.content_hash, hash_version=parsed.hash_version,
                      message_id=parsed.message_id, facts=json.dumps(KNOWN_BULK),
                      subject="Autumn sale", sender="news@vendor-a.example",
                      locator=json.dumps({"uid": uid, "uidvalidity": 1}))  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ?, pinned_models = ?, proposal = ?,"
                     " decision_source = 'rule' WHERE stable_id = ?",
                     (json.dumps(MARKETING), json.dumps(pinned),
                      json.dumps({"done": [{"name": "label", "target": "marketing"}]}),
                      sid))  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED, Status.EXECUTING, Status.EXECUTED):
        items.transition(conn, clock, StableId(sid), to, LIVE, actor="test")
    return sid


def _approve_scan_and_run(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource,
                          sid: str, run_id: str) -> None:  # fmt: skip
    from ecf_server import approvals, stepup  # noqa: PLC0415
    from ecf_server.notify import FakeNotifier  # noqa: PLC0415
    from ecf_server.stepper import FakeStepper  # noqa: PLC0415

    assert [b["id"] for b in approvals.scan_pending(conn, run_id)["batch"]] == [sid]
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve_scan(conn, clock, FakeNotifier(), run_id, [sid], nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "approve_scan", ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    approvals.approve_scan(conn, clock, FakeNotifier(), run_id, [sid], nonce=issued.nonce_id)
    run = mailbox_actions.executor_for(src, "t", 10 * 1024 * 1024, lambda: False)
    assert execute.run_once(conn, clock, run, address_id="ap")
    assert _status(conn, sid) == "executed"
    done = json.loads(conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                                   (sid,)).fetchone()[0])["done"]  # fmt: skip
    assert done[0] == {"name": "label", "target": "marketing"}  # the first record kept
    assert done[-1]["name"] == "archive"  # what the new plan added, appended
    assert src.uids_after(0) == []  # archived


def test_live_an_added_hide_waits_for_approve_scan_and_done_is_appended(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    src = FakeMailSource()
    sid = _inbox_item(conn, clock, src, _key(conn, rules="0" * 64))
    r = reprocess.start(conn, clock, "ap", SINCE, None, rules_only=True, force=False)
    reprocess.tick(conn, clock)
    assert _status(conn, sid) == "awaiting_approval"  # historical: the hide needs you
    posts = conn.execute("SELECT count(*) FROM jobs WHERE queue = 'slack_out'").fetchone()[0]
    assert posts == 0  # card=False: old mail gets no card
    _approve_scan_and_run(conn, clock, src, sid, r["run_id"])


def test_live_the_same_after_a_reclassify(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """(a): classified again by Claude, then planned: only what the new plan adds."""
    from ecf_server import classifier  # noqa: PLC0415
    from tests.test_decide import MARKETING  # noqa: PLC0415

    _address(conn, clock)
    src = FakeMailSource()
    sid = _inbox_item(conn, clock, src, {})  # no key: classified again
    r = reprocess.start(conn, clock, "ap", SINCE, None, rules_only=False, force=False)
    reprocess.tick(conn, clock)
    assert _status(conn, sid) == "awaiting_claude"
    assert classifier.store(conn, clock, sid, MARKETING, {"classifier": "claude-sonnet-5-5"},
                            expected=Status.AWAITING_CLAUDE)  # fmt: skip
    assert _status(conn, sid) == "awaiting_approval"
    _approve_scan_and_run(conn, clock, src, sid, r["run_id"])


def test_the_summary_counts_new_unsafe_proposals_for_the_gate(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    with write_tx(conn):
        run_id = scans.new_run(conn, clock, kind="reprocess", address_id="ap", since=SINCE,
                               until=None, mode="changed items")  # fmt: skip
    sid = _item(conn, clock, 1, facts={"reprocess": run_id})
    plan = {"payment_or_fraud": True, "actor": {"action": "archive"}}
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?",
                     (json.dumps({"plan": plan}), sid))  # fmt: skip
    assert reprocess.summary_lines(conn, run_id) == [
        "1 new unsafe proposal(s): the go-live gate counts them"
    ]


def test_item_undo_is_the_digests_undo_as_a_command(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    sid = _item(conn, clock, 1, status=Status.EXECUTED,
                done=[{"name": "archive", "folder": "Archive"}])  # fmt: skip
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    assert digests.queue_undo(conn, clock, row, "os_user") == "plan"
    doc = json.loads(conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                                  (sid,)).fetchone()[0])  # fmt: skip
    assert doc["undo"] == "queued"
    fraud = _item(conn, clock, 2, status=Status.EXECUTED, facts={"triggers": {"fraud": ["x"]}},
                  done=[{"name": "label", "target": "suspicious"}])  # fmt: skip
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (fraud,)).fetchone()
    with pytest.raises(ConflictError, match="nothing to undo"):
        digests.queue_undo(conn, clock, row, "os_user")
