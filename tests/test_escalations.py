"""Escalations and item cards (V1.2 step 6): posting, bursts, the V1.1 summary (OD-211), and the
Show excerpt and Dismiss buttons; against the fake chat surface. Nothing here talks to Slack."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import PolicyDeniedError
from ecf.ids import AddressId, StableId
from ecf_server import cards, db, escalations, slack_admin
from ecf_server._slack import Envelope
from ecf_server.chat import Card, FakeChat
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.items import create_item
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Click, Inbound, SlackReceiver
from ecf_server.slack_out import SlackSender
from ecf_server.slack_render import blocks

ME = "U0ME1"
FRAUD: dict[str, Any] = {
    "triggers": {"fraud": ["bank details from an unconfirmed sender"], "regulator": []},
    "keywords": {"bank": ["iban"], "change": [], "payment": ["invoice"], "regulator": []},
    "payment_keyword": True,
    "auth_result": "none",
    "auth": {"reason": "no DKIM signature"},
    "reply_to_mismatch": True,
    "precheck": {"stage": "shadow", "reasons": ["possible fraud"]},
}
KNOWN_BANK_CHANGE = FRAUD | {"sender_seen_before": True}
REGULATOR: dict[str, Any] = {
    "triggers": {"fraud": [], "regulator": ["names the SEC"]},
    "keywords": {"bank": [], "change": [], "payment": [], "regulator": ["SEC"]},
    "auth_result": "pass",
    "sender_confirmed": True,
    "precheck": {"stage": "shadow"},
}
PLAIN: dict[str, Any] = {"triggers": {}, "keywords": {}, "sender_confirmed": True}


def _slack(conn: sqlite3.Connection, clock: FakeClock, *, route: bool = True) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
            " VALUES ('ap', 'ap@acme.example', 'high', 'A', ?)",
            (now,),
        )
        if route:
            conn.execute(
                "INSERT INTO routes (address_id, surface, route_ref, name)"
                " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')"
            )


def _item(
    conn: sqlite3.Connection,
    clock: FakeClock,
    sid: str,
    facts: dict[str, Any],
    *,
    state: str | None = "pending",
    excerpt: str | None = None,
) -> None:
    def also(c: sqlite3.Connection) -> None:
        if excerpt is not None:
            c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES (?, ?)",
                      (sid, excerpt))  # fmt: skip
        if state:
            c.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                      " VALUES (?, 'ap', ?, ?)", (sid, state, to_ts(clock.now())))  # fmt: skip

    create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                content_hash="h", facts=json.dumps(facts), subject=f"Invoice {sid}",
                sender="billing@vendor-a.example", sender_name="Vendor A", also=also)  # fmt: skip


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY created_at")
    return [json.loads(r["payload"]) for r in rows]


def _card(p: dict[str, Any]) -> dict[str, str]:
    return dict(p["card"]["fields"])


# ---- cards ----------------------------------------------------------------------------------


def test_a_fraud_card_shows_metadata_only_and_never_offers_dismiss(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    _item(conn, clock, "s" * 64, FRAUD, excerpt="Please pay to IBAN ...")
    row = conn.execute("SELECT * FROM items").fetchone()
    card = cards.item_card(row, mention=ME)
    f = dict(card.fields)
    assert card.title == "Possible fraud"
    assert (
        f["From"] == "Vendor A <billing@vendor-a.example>" and f["Subject"] == f"Invoice {'s' * 64}"
    )
    assert f["Sender check"] == "none: no DKIM signature"
    assert f["Flags"] == "first-time sender, Reply-To differs from From"
    assert f["Done"] == "nothing in the mailbox (shadow stage)"
    assert f["Open"] == "ecf item show ssssssss"
    assert [b.action for b in card.buttons] == ["show_excerpt"]  # no Dismiss (OD-213)
    assert card.note == cards.PAYMENT_NOTE
    assert "IBAN" not in json.dumps(card.fields)  # never the body


def test_the_mention_is_the_only_mrkdwn_and_only_for_a_member_id() -> None:
    b = blocks(Card("t <!channel>", text="*x*", mention=ME))
    mrkdwn = [x for x in b if x.get("text", {}).get("type") == "mrkdwn"]
    assert mrkdwn == [{"type": "section", "text": {"type": "mrkdwn", "text": f"<@{ME}>"}}]
    for bad in ("<!channel>", "U1> <!here", "u0me1"):
        assert all(x.get("text", {}).get("type") != "mrkdwn"
                   for x in blocks(Card("t", mention=bad)))  # fmt: skip


def test_items_from_before_v12_say_what_is_missing(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    facts = json.dumps(FRAUD | {"from_domain": "vendor-a.example"})
    create_item(conn, clock, stable_id=StableId("o" * 64), address_id=AddressId("ap"),
                content_hash="h", facts=facts)  # fmt: skip
    f = dict(cards.item_card(conn.execute("SELECT * FROM items").fetchone()).fields)
    assert f["From"] == "someone at vendor-a.example (sender not recorded before V1.2)"
    assert f["Subject"] == "(not recorded before V1.2)"


def test_severity_puts_a_known_sender_with_a_bank_change_first() -> None:
    assert cards.severity(KNOWN_BANK_CHANGE) == 0
    assert cards.severity(FRAUD) == 1
    assert cards.severity(REGULATOR) == 2
    assert cards.severity(FRAUD | {"quarantined": True}) == 1


def test_impersonation_without_a_payment_keyword_is_a_fraud_card() -> None:
    """V1.6: rule 1 escalates it on payment_related alone, with `fraud` empty; the card said
    "Regulatory mail"."""
    why = "display name matches Pat Lee (patlee@gmail.com), one of your org addresses, but ..."
    imp = REGULATOR | {"triggers": {"fraud": [], "fraud_weak": [why], "regulator": [],
                                    "impersonation": [why]}}  # fmt: skip
    assert cards.kind(imp) == "fraud" and cards.severity(imp) == 1
    assert cards.kind(REGULATOR) == "regulator"


# ---- posting --------------------------------------------------------------------------------


def test_each_escalation_is_posted_once_to_its_channel_mentioning_you(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD)
    _item(conn, clock, "b" * 64, REGULATOR)
    assert escalations.sweep(conn, clock) == 2
    assert escalations.sweep(conn, clock) == 0  # nothing twice
    posts = _posts(conn)
    assert [p["channel"] for p in posts] == ["CAP", "CAP"]
    assert [p["card"]["title"] for p in posts] == ["Possible fraud", "Regulatory mail"]
    assert all(p["card"]["mention"] == ME and p["identity"]["name"] == "ecf ap" for p in posts)
    states = {r[0] for r in conn.execute("SELECT state FROM escalations")}
    assert states == {"posted"}


def test_escalations_wait_for_your_member_id_and_the_channel(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock, route=False)
    _item(conn, clock, "a" * 64, FRAUD)
    assert escalations.sweep(conn, clock) == 0 and _posts(conn) == []
    with write_tx(conn):
        conn.execute(
            "INSERT INTO routes (address_id, surface, route_ref, name)"
            " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')"
        )
    assert escalations.sweep(conn, clock) == 1


def test_more_than_five_in_a_minute_merge_into_one_thread(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    for i in range(6):
        _item(conn, clock, f"{i}" * 64, REGULATOR if i == 0 else FRAUD)
    _item(conn, clock, "k" * 64, KNOWN_BANK_CHANGE)
    escalations.sweep(conn, clock)
    [head] = _posts(conn)
    assert head["card"]["title"] == "7 fraud or regulatory emails in a minute"
    lines = head["card"]["text"].splitlines()
    assert lines[0].endswith("(ecf item show kkkkkkkk)")  # the most severe first
    assert lines[-1].startswith("Regulatory mail")
    clock.advance(20)
    _item(conn, clock, "x" * 64, FRAUD)
    escalations.sweep(conn, clock)
    reply = _posts(conn)[-1]
    assert reply["thread_key"] == head["key"] and reply["card"]["title"] == "1 more in this burst"
    clock.advance(120)  # the burst is over
    _item(conn, clock, "y" * 64, FRAUD)
    escalations.sweep(conn, clock)
    assert _posts(conn)[-1]["key"] == f"item:{'y' * 64}"


def test_v11_escalations_get_one_summary_post(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _slack(conn, clock)
    _item(conn, clock, "o" * 64, FRAUD, state="v1.1")  # older than 7 days
    clock.advance(8 * 86400)
    for i, facts in enumerate((REGULATOR, FRAUD, KNOWN_BANK_CHANGE)):
        _item(conn, clock, f"{i}" * 64, facts, state="v1.1")
    assert escalations.sweep(conn, clock) == 4
    assert escalations.sweep(conn, clock) == 0
    [post] = _posts(conn)
    assert post["channel"] == "CSUM"
    assert post["card"]["title"] == "4 escalation(s) recorded before Slack was connected"
    assert _card(post) == {"ap": "4 (3 in the last 7 days)"}
    lines = post["card"]["text"].splitlines()
    assert lines[1].endswith("(ecf item show 22222222)") and len(lines) == 5
    assert {r[0] for r in conn.execute("SELECT state FROM escalations")} == {"summarized"}


# ---- buttons --------------------------------------------------------------------------------


def _click(
    db_path: Path, clock: FakeClock, conn: sqlite3.Connection, action: str, ref: str
) -> None:
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    payload = {"type": "block_actions", "api_app_id": "A1", "team": {"id": "T1"},
               "user": {"id": ME}, "channel": {"id": "CAP"},
               "actions": [{"action_id": f"{action}#0", "value": ref}]}  # fmt: skip
    inbound.on_envelope(Envelope(f"e-{action}-{ref}", "interactive", payload, None, None))
    SlackReceiver(clock).run_once(conn)


def test_show_excerpt_is_ephemeral_short_and_audited(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _slack(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD, excerpt="Dear customer, see https://evil.test " + "x" * 400)
    _click(db_path, clock, conn, "show_excerpt", "a" * 64)
    [p] = _posts(conn)
    assert p["op"] == "ephemeral" and p["user"] == ME and p["channel"] == "CAP"
    assert "https[:]//evil.test" in p["text"] and len(p["text"]) < 260
    events = [r[0] for r in conn.execute("SELECT event FROM audit")]
    assert "item.excerpt_shown" in events


def test_dismiss_is_refused_on_fraud_and_closes_a_plain_item(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _slack(conn, clock)
    _item(conn, clock, "f" * 64, FRAUD)
    _item(conn, clock, "p" * 64, PLAIN)
    escalations.sweep(conn, clock)
    _click(db_path, clock, conn, "dismiss", "f" * 64)
    status = dict(conn.execute("SELECT stable_id, status FROM items").fetchall())
    assert status["f" * 64] == "new"
    assert any(p.get("op") == "ephemeral" and "isn't available" in p["text"] for p in _posts(conn))
    _click(db_path, clock, conn, "dismiss", "p" * 64)
    status = dict(conn.execute("SELECT stable_id, status FROM items").fetchall())
    assert status["p" * 64] == "resolved_manual"
    edited = _posts(conn)[-1]
    assert edited["key"] == f"item:{'p' * 64}" and edited["card"]["title"] == "Dismissed"
    assert edited["card"]["buttons"] == []


def test_dismiss_handler_refuses_directly_too(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _slack(conn, clock)
    _item(conn, clock, "f" * 64, REGULATOR)
    handler = escalations._dismiss  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(PolicyDeniedError):
        handler(conn, clock, Click("button", "dismiss", "f" * 64, "CAP", ME))


def test_cards_render_through_the_real_sender(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _slack(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD)
    escalations.sweep(conn, clock)
    chat = FakeChat()
    sender = SlackSender(chat, clock, FakeNotifier(), sleep=lambda _s: None)
    while sender.run_once(conn):
        pass
    [post] = chat.posts
    assert post["card"]["mention"] == ME and post["card"]["title"] == "Possible fraud"


def test_a_short_sender_never_cuts_the_address() -> None:
    """A cut domain can hide a lookalike (V1.2 shadow run, 2026-09-30)."""
    long = "Mistry Babylon <mistrybabylon@atomicmail.example>"
    assert cards.short_sender(long) == "mistrybabylon@atomicmail.example"
    assert cards.short_sender("Pat <pat@a.example>") == "Pat <pat@a.example>"
    very = "x" * 50 + "@lookalike-bank.example"
    assert cards.short_sender(very) == very  # longer than the limit, still whole
    assert cards.short_sender("someone at gmail.com (sender not recorded before V1.2)") == (
        "someone at gmail.com")  # fmt: skip
