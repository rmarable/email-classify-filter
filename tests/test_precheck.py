"""The pre-check's model-free rules and mailbox actions (V1.1 step 10)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from ecf.errors import GrantInvalidError
from ecf_server import actions, leases, precheck
from ecf_server.actions import Planned
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import FakeClock
from ecf_server.dnscache import DnsCache
from ecf_server.fetch import address_config, fetch_page
from ecf_server.mail import FLAGGED, MailSource
from ecf_server.mail.fake import FakeMailSource
from tests.test_senderauth import FakeDns
from tests.test_triggers import mail

INSTALL = "default"
KW = "$ecf_default_suspicious"


def facts(**triggers: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fraud": [],
        "fraud_weak": [],
        "regulator": [],
        "unverified_payment": False,
    }
    return {"triggers": base | triggers}


# ---- decisions ------------------------------------------------------------------------------


def test_decisions_follow_the_spec_table() -> None:
    d = precheck.decide(facts(fraud=["x"]))
    assert [a.to_json() for a in d.actions] == [
        {"name": "label", "target": "suspicious"},
        {"name": "flag", "target": None},
        {"name": "escalate", "target": None},
    ]
    assert d.escalate and not d.digest
    weak = precheck.decide(facts(fraud_weak=["y"]))
    assert not weak.escalate and weak.digest and len(weak.actions) == 2
    unverified = precheck.decide(facts(unverified_payment=True))
    assert unverified.digest and Planned("label", "unverified_sender") in unverified.actions
    assert precheck.decide(facts()).actions == []


def test_several_triggers_add_up_without_repeats() -> None:
    d = precheck.decide(facts(fraud=["x"], fraud_weak=["y"], regulator=["IRS"]))
    labels = [a.target for a in d.actions if a.name == "label"]
    assert labels == ["suspicious", "regulatory"]
    assert [a.name for a in d.actions].count("flag") == 1 and d.escalate and not d.digest
    assert d.reasons == ["possible fraud", "weak fraud signal", "regulatory mail"]


def test_quarantined_items_are_escalated() -> None:
    d = precheck.decide({"quarantined": True, "content_unscanned": True})
    assert d.escalate and d.reasons == ["possible fraud"]


# ---- running it -----------------------------------------------------------------------------


def _setup(conn: sqlite3.Connection, stage: str) -> None:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, stage, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', ?, 'now')",
        (stage,),
    )
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('org_domains', '[\"acme.example\"]', 'now', 'test')"
    )


def _check(conn: sqlite3.Connection, clock: FakeClock, src: MailSource) -> list[precheck.Outcome]:
    """One pre-check round: fetch with the analyzer, then the model-free rules."""
    lease = leases.acquire(conn, clock, "ap", "w")
    assert lease is not None
    cfg = address_config(conn, "ap")
    analyzer = MessageAnalyzer.for_address(
        conn, clock, "ap", DnsCache(conn, clock, lookup=FakeDns())
    )
    page = fetch_page(conn, clock, src, cfg, lease, analyzer=analyzer)
    return precheck.run(
        conn, clock, src, "ap", page.created, install=INSTALL, max_scan_bytes=cfg.max_scan_bytes
    )


BEC = mail(
    "Please pay invoice 42 to our updated bank details below.",
    sender="Vendor A <billing@vendor-a.example>",
)


def test_shadow_decides_but_leaves_the_mailbox_alone(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, "shadow")
    src = FakeMailSource()
    assert _check(conn, clock, src) == []  # first run
    src.deliver(BEC)
    (o,) = _check(conn, clock, src)
    assert o.decision.escalate and o.executed == [] and o.skipped == "shadow: decided, not done"
    row = conn.execute("SELECT status, prechecked, facts FROM items").fetchone()
    assert row["status"] == "new" and row["prechecked"] == 1
    assert json.loads(row["facts"])["precheck"]["escalation"] == "queued"
    esc = conn.execute("SELECT stable_id, state FROM escalations").fetchone()
    assert (esc["stable_id"], esc["state"]) == (o.stable_id, "pending")  # for the Slack thread
    assert src.flags([1])[1] == frozenset()
    assert conn.execute("SELECT count(*) FROM grants").fetchone()[0] == 0
    assert (
        precheck.run(conn, clock, src, "ap", [o.stable_id], install=INSTALL, max_scan_bytes=1 << 20)
        == []
    )  # already prechecked


def test_live_labels_and_flags_under_a_grant_and_can_undo(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, "live")
    src = FakeMailSource()
    _check(conn, clock, src)
    src.deliver(BEC)
    (o,) = _check(conn, clock, src)
    # unsigned and about payment: an unverified payment sender as well as possible fraud
    assert o.executed == [
        "label suspicious",
        "flag",
        "escalate",
        "label unverified_sender",
    ]
    assert {KW, FLAGGED} <= src.flags([1])[1]
    assert conn.execute("SELECT status FROM grants").fetchone()["status"] == "consumed"
    events = [r["event"] for r in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert events[-3:] == ["action.granted", "action.executed", "precheck.decided"]
    assert conn.execute("SELECT status FROM items").fetchone()["status"] == "new"
    item = conn.execute("SELECT * FROM items").fetchone()
    actions.undo(
        conn, clock, src, item, o.decision.actions, install=INSTALL, max_scan_bytes=1 << 20
    )
    assert not {KW, FLAGGED} & src.flags([1])[1]


def test_a_changed_message_is_left_alone(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, "live")
    src = FakeMailSource()
    _check(conn, clock, src)
    src.deliver(BEC)
    lease = leases.acquire(conn, clock, "ap", "w")
    assert lease is not None
    cfg = address_config(conn, "ap")
    analyzer = MessageAnalyzer.for_address(
        conn, clock, "ap", DnsCache(conn, clock, lookup=FakeDns())
    )
    page = fetch_page(conn, clock, src, cfg, lease, analyzer=analyzer)
    src.expunge(1)  # the person deleted it before the actions ran
    (o,) = precheck.run(
        conn, clock, src, "ap", page.created, install=INSTALL, max_scan_bytes=cfg.max_scan_bytes
    )
    assert o.executed == [] and o.skipped == "message no longer in INBOX"


def test_grants_are_single_use(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, "live")
    src = FakeMailSource()
    _check(conn, clock, src)
    src.deliver(BEC)
    _check(conn, clock, src)
    grant = conn.execute("SELECT grant_id FROM grants").fetchone()["grant_id"]
    with pytest.raises(GrantInvalidError):
        actions._claim(conn, clock, grant)  # pyright: ignore[reportPrivateUsage]


def test_harmless_mail_gets_nothing(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, "live")
    src = FakeMailSource()
    _check(conn, clock, src)
    src.deliver(mail("Lunch on Friday?"))
    (o,) = _check(conn, clock, src)
    assert o.decision.actions == [] and o.skipped == "nothing fired"
    assert src.flags([1])[1] == frozenset()


@pytest.mark.imap
def test_live_actions_against_dovecot(
    conn: sqlite3.Connection, clock: FakeClock, dovecot_server: Any
) -> None:
    from ecf_server.mail.imap import ImapSource  # noqa: PLC0415
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server
    user = f"ecf-t-{uuid.uuid4().hex[:12]}"
    src = ImapSource(
        dv.host, user, lambda: dovecot.PASSWORD, port=dv.port, ssl_context=dv.context()
    )
    admin = dv.admin(user)
    try:
        _setup(conn, "live")
        _check(conn, clock, src)
        dovecot.append(admin, BEC, datetime.now(UTC))
        (o,) = _check(conn, clock, src)
        (uid,) = src.uids_after(0)
        assert o.executed[:2] == ["label suspicious", "flag"]
        assert {KW, FLAGGED} <= src.flags([uid])[uid] and "\\Seen" not in src.flags([uid])[uid]
        item = conn.execute("SELECT * FROM items").fetchone()
        actions.undo(
            conn, clock, src, item, o.decision.actions, install=INSTALL, max_scan_bytes=1 << 20
        )
        assert not {KW, FLAGGED} & src.flags([uid])[uid]
    finally:
        src.close()
        admin.logout()
