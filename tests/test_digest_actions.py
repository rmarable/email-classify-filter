"""What the hourly digest offers from V1.3 (V1.3 step 4d; SPEC §9.5, OD-210): automatic actions,
"Approve all N reversible" with its exclusions and re-checks, and the sender-category offer."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf_server import decide, digest_actions, digests, slack_in
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.slack_in import Click
from tests.test_decide import KNOWN_BULK, MARKETING, item_row, make_classified
from tests.test_digests_daily import ME, MORNING, slack_setup


@pytest.fixture
def morning(clock: FakeClock) -> FakeClock:
    clock.advance((MORNING - clock.now()).total_seconds())
    return clock


def _stage(conn: sqlite3.Connection, stage: str, sensitivity: str = "standard") -> None:
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = ?, sensitivity = ? WHERE address_id = 'ap'",
                     (stage, sensitivity))  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.action_policy', ?, 't', 't') ON CONFLICT (key) DO NOTHING",
                     (json.dumps({"standard": {"archive": "approve"}}),))  # fmt: skip


def _waiting(conn: sqlite3.Connection, clock: FakeClock, sid: str = "a",
             facts: dict[str, Any] | None = None, **cls: Any) -> str:  # fmt: skip
    s = make_classified(conn, clock, MARKETING | cls, facts or KNOWN_BULK, sid=sid)
    decide.apply(conn, clock, s)
    return s


def _digest(conn: sqlite3.Connection, clock: FakeClock) -> Any:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 't',"
                     " 't') ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (digests.LAST + "ap", json.dumps(to_ts(clock.now()))))  # fmt: skip
    clock.advance(3600)
    return digests.build(conn, "ap", clock.now().replace(hour=0), clock.now())


def _click(conn: sqlite3.Connection, clock: FakeClock, action: str, ref: str) -> None:
    slack_in.HANDLERS[action](conn, clock, Click("button", action, ref, "CAP", ME))


def test_approve_all_lists_eligible_reversible_approvals_and_approves_them(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    _stage(conn, "live")
    sid = _waiting(conn, morning)
    assert item_row(conn, sid)["status"] == "awaiting_approval"
    assert digest_actions.eligible(conn, item_row(conn, sid))
    card = _digest(conn, morning)
    [button] = [b for b in card.buttons if b.action == digest_actions.APPROVE_ALL]
    assert button.label == "Approve all 1 reversible"
    assert "Waiting for your approval, all reversible (1):" in card.text
    _click(conn, morning, digest_actions.APPROVE_ALL, button.ref)
    assert item_row(conn, sid)["status"] == "executing"
    audit = conn.execute("SELECT data FROM audit WHERE event = 'approval.batch'").fetchone()
    assert json.loads(audit[0]) == {"approved": 1, "skipped": 0}


def test_a_decision_made_since_the_digest_is_skipped(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    from ecf_server import approvals  # noqa: PLC0415

    slack_setup(conn, morning)
    _stage(conn, "live")
    sid = _waiting(conn, morning)
    card = _digest(conn, morning)
    [button] = [b for b in card.buttons if b.action == digest_actions.APPROVE_ALL]
    approvals.reject(conn, morning, sid, actor="os_user")
    _click(conn, morning, digest_actions.APPROVE_ALL, button.ref)
    assert item_row(conn, sid)["status"] == "rejected"
    audit = conn.execute("SELECT data FROM audit WHERE event = 'approval.batch'").fetchone()
    assert json.loads(audit[0]) == {"approved": 0, "skipped": 1}


@pytest.mark.parametrize(
    ("facts", "cls", "sensitivity"),
    [
        (KNOWN_BULK | {"sender_seen_before": False}, {}, "standard"),  # high-risk: first-time
        (KNOWN_BULK, {"payment_related": True}, "standard"),  # payment
        (KNOWN_BULK | {"triggers": {"unverified_payment": True}}, {}, "standard"),
        (KNOWN_BULK, {}, "high"),  # high address: nothing to approve (label + leave)
    ],
)
def test_excluded_items_never_join_approve_all(
    conn: sqlite3.Connection, morning: FakeClock, facts: dict[str, Any], cls: dict[str, Any],
    sensitivity: str,
) -> None:  # fmt: skip
    slack_setup(conn, morning)
    _stage(conn, "live", sensitivity)
    sid = _waiting(conn, morning, facts=facts, **cls)
    assert not digest_actions.eligible(conn, item_row(conn, sid))
    card = _digest(conn, morning)
    assert not [b for b in card.buttons if b.action == digest_actions.APPROVE_ALL]


def test_automatic_actions_are_counted(conn: sqlite3.Connection, morning: FakeClock) -> None:
    slack_setup(conn, morning)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live' WHERE address_id = 'ap'")
    _waiting(conn, morning)
    card = _digest(conn, morning)
    assert "Done automatically: 1 archive, 1 label" in card.text


def test_uncorroborated_hides_offer_a_category_confirmation_via_the_computer(
    conn: sqlite3.Connection, morning: FakeClock
) -> None:
    slack_setup(conn, morning)
    sid = _waiting(conn, morning, facts=KNOWN_BULK | {"bulk_corroborates": False})
    before = item_row(conn, sid)["status"]
    card = _digest(conn, morning)
    assert "Not hidden because the sender's category isn't confirmed:" in card.text
    [button] = [b for b in card.buttons if b.action == digest_actions.CONFIRM]
    _click(conn, morning, digest_actions.CONFIRM, button.ref)
    last = "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid DESC LIMIT 1"
    [post] = [json.loads(r[0]) for r in conn.execute(last)]
    assert (
        "ecf sender confirm news@vendor-a.example --category marketing --address ap"
        in (post["text"])
    )
    assert item_row(conn, sid)["status"] == before  # the click changes nothing
