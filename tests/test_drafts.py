"""Draft replies carried out (V1.5 step 2c; OD-317): saved to Drafts from the approved payload,
refused when it no longer matches, shown on the card, and undone only while unchanged."""

from __future__ import annotations

import json
import sqlite3
from email import message_from_bytes, policy
from typing import Any

import pytest

from ecf_server import actor, approvals, decide, digests, mailbox_actions
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.mail import Folder
from ecf_server.mail.fake import DEFAULT_FOLDERS, FakeMailSource
from ecf_server.notify import FakeNotifier
from tests.test_decide import item_row
from tests.test_mailbox_actions import (  # pyright: ignore[reportPrivateUsage]
    INSTALL,
    MB,
    _address,  # pyright: ignore[reportPrivateUsage]
    _mail_item,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)

REPLY: dict[str, Any] = {"category": "invoice", "priority": "normal", "requires_action": True,
         "requires_reply": True, "payment_related": False, "deadline_mentioned": False,
         "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
FACTS: dict[str, Any] = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True,
         "from_count": 1}  # fmt: skip
TEXT = "Hello,\n\nThanks for your note; see https://acme.example/terms or write to ar@acme.example."


def _approved_draft(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, *,
                    address: bool = True) -> str:  # fmt: skip
    if address:
        _address(conn, clock)
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    assert item_row(conn, sid)["status"] == "awaiting_approval"
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")  # reversible: no step-up
    return sid


def _drafts(src: FakeMailSource) -> dict[int, Any]:
    return src.elsewhere.get("Drafts", {})


def test_an_approved_draft_is_saved_to_drafts(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _approved_draft(conn, clock, src)
    assert _run(conn, clock, src) == 1
    [stored] = _drafts(src).values()
    assert "\\Draft" in stored.flags
    m = message_from_bytes(stored.raw, policy=policy.default)
    assert (str(m["To"]), str(m["From"])) == ("news@vendor-a.example", "ap@acme.example")
    assert str(m["In-Reply-To"]) == "<contract-0@synthetic.acme.example>"
    assert "X-ECF-Install" not in m and "Auto-Submitted" not in m
    assert m.get_content().replace("\r\n", "\n").startswith("Hello,\n\nThanks for your note")
    row = item_row(conn, sid)
    assert row["status"] == "executed"
    [done] = [d for d in json.loads(row["proposal"])["done"] if d["name"] == "draft_reply"]
    assert done["folder"] == "Drafts" and done["message_id"] == str(m["Message-ID"])
    assert src.uids_after(0) != []  # the email itself stays in INBOX


def test_the_card_shows_the_draft_in_full_without_live_links(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import slack_admin  # noqa: PLC0415

    _address(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-t-ap')")  # fmt: skip
    slack_admin.put_setting(conn, "slack_member_id", "U1", "t", actor="test")
    _approved_draft(conn, clock, FakeMailSource(), address=False)
    cards = [json.loads(r[0])["card"] for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")]  # fmt: skip
    [card] = [c for c in cards if c["title"].startswith("Approve?")]
    assert "save a draft reply to news@vendor-a.example" in card["title"]
    assert card["text"].startswith("Draft written by ecf's model")
    assert "https[:]//acme[.]example/terms" in card["text"]
    assert "ar[at]acme[.]example" in card["text"] and "https://" not in card["text"]


def test_a_draft_at_the_limit_is_shown_whole_on_the_card(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """One paragraph of 4,000 characters with links: more than one section, all of it shown."""
    from ecf_server import cards, outbound_plan, slack_admin, slack_render  # noqa: PLC0415
    from ecf_server.chat import Card  # noqa: PLC0415

    _address(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-t-ap')")  # fmt: skip
    slack_admin.put_setting(conn, "slack_member_id", "U1", "t", actor="test")
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    unit = "see https://acme.example/a.b.c or ar@acme.example; "
    text = (unit * 200)[: outbound_plan.DRAFT_MAX].strip()
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": text},
                     frozenset())  # fmt: skip
    [card] = [c for c in (json.loads(r[0])["card"] for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid"))
        if c["title"].startswith("Approve?")]  # fmt: skip
    shown = "".join(s["text"]["text"] for s in slack_render.blocks(Card("t", text=card["text"]))
                    if s["type"] == "section")  # fmt: skip
    assert "…" not in shown
    assert shown.replace(" ", "").endswith(cards.defang(text).replace(" ", ""))
    assert any(b["action"] == approvals.APPROVE for b in card["buttons"])


def test_a_card_that_cant_show_the_whole_draft_has_no_approve(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth (P2): approve such a draft at the computer, where it is shown whole."""
    from ecf_server import slack_admin, slack_render  # noqa: PLC0415

    monkeypatch.setattr(slack_render, "TEXT_BLOCKS_MAX", 1)
    _address(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-t-ap')")  # fmt: skip
    slack_admin.put_setting(conn, "slack_member_id", "U1", "t", actor="test")
    _approved_draft(conn, clock, FakeMailSource(), address=False)
    [card] = [c for c in (json.loads(r[0])["card"] for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid"))
        if c["title"].startswith("Approve?")]  # fmt: skip
    assert not any(b["action"] == approvals.APPROVE for b in card["buttons"])
    assert "Approve it at your computer: ecf approve" in card["note"]


def test_a_slack_approval_of_a_draft_its_card_cant_show_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old cards and buttons too (X1): the check runs at approval, not only on the card."""
    from ecf.errors import InvalidInputError  # noqa: PLC0415
    from ecf_server import slack_render  # noqa: PLC0415

    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    with pytest.raises(InvalidInputError, match="being updated"):  # before the one-off refresh
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1")
    approvals.refresh_draft_cards(conn, clock)
    monkeypatch.setattr(slack_render, "TEXT_BLOCKS_MAX", 1)
    with pytest.raises(InvalidInputError, match="may not show the whole draft"):
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1")
    assert item_row(conn, sid)["status"] == "awaiting_approval"
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")  # shown in full there


def test_waiting_draft_cards_are_posted_again_once(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    """A card an earlier version cut gets a new grant, so its Approve stops working, and is
    replaced by one that shows the whole draft (W1)."""
    from ecf.errors import GrantInvalidError  # noqa: PLC0415
    from ecf_server import slack_admin  # noqa: PLC0415

    _address(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-t-ap')")  # fmt: skip
    slack_admin.put_setting(conn, "slack_member_id", "U1", "t", actor="test")
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    [old] = [r[0] for r in conn.execute(
        "SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'",
        (sid,))]  # fmt: skip
    assert approvals.refresh_draft_cards(conn, clock) == 1
    assert approvals.refresh_draft_cards(conn, clock) == 0  # once
    posts = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")]  # fmt: skip
    mine = [q["card"] for q in posts if q["key"] == f"item:{sid}"]
    assert len(mine) == 2
    [approve] = [b for b in mine[-1]["buttons"] if b["action"] == approvals.APPROVE]
    assert approve["ref"] != old and "Thanks for your note" in mine[-1]["text"]
    with pytest.raises(GrantInvalidError):  # the earlier card's button
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1", grant_id=old)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1",
                      grant_id=approve["ref"])  # fmt: skip
    assert item_row(conn, sid)["status"] == "executing"


def test_one_failing_draft_doesnt_hold_up_the_refresh(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A draft whose new grant fails keeps its old grant; the next is still refreshed; the marker
    is written (T1, K1, K5)."""
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    _address(conn, clock)
    src = FakeMailSource()
    sids = [_mail_item(conn, clock, src, n, REPLY, FACTS, src.deliver) for n in range(2)]
    for sid in sids:
        item = item_row(conn, sid)
        ctx, p = decide.plan_for(conn, item)
        actor.decide_one(conn, clock, item, ctx, p,
                         {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                         frozenset())  # fmt: skip

    def issued(sid: str) -> set[str]:
        return {r[0] for r in conn.execute(
            "SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'",
            (sid,))}  # fmt: skip

    before = {sid: issued(sid) for sid in sids}
    real = approvals._insert_grant  # pyright: ignore[reportPrivateUsage]
    first = sorted(sids)[0]

    def failing(c: sqlite3.Connection, k: FakeClock, item: sqlite3.Row, a: Any) -> str:
        if item["stable_id"] == first:
            raise InvalidInputError("x")
        return real(c, k, item, a)

    monkeypatch.setattr(approvals, "_insert_grant", failing)
    approvals.refresh_draft_cards(conn, clock)
    other = next(s for s in sids if s != first)
    assert issued(first) == before[first]  # nothing voided: its old grant still works
    assert len(issued(other)) == 1 and issued(other) != before[other]
    assert approvals._drafts_refreshed(conn)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(InvalidInputError, match="ecf approve"):  # skipped: computer only (L2)
        approvals.approve(conn, clock, FakeNotifier(), first, actor="slack:U1")
    approvals.approve(conn, clock, FakeNotifier(), first, actor="os_user")


def test_an_unreadable_marker_refuses_slack_draft_approvals(conn: sqlite3.Connection,
                                                            clock: FakeClock) -> None:  # fmt: skip
    """Fails closed (M5, N1): unreadable or not a list, approve at the computer."""
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    for bad in ("{not json", "true"):
        with write_tx(conn):
            conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                         " VALUES (?, ?, 't', 'test') ON CONFLICT (key) DO UPDATE"
                         " SET value = excluded.value",
                         (approvals.DRAFT_CARDS_KEY, bad))  # fmt: skip
        with pytest.raises(InvalidInputError, match="may not show the whole draft"):
            approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1")
    with write_tx(conn):  # the control: a readable list without this item
        conn.execute("UPDATE settings SET value = '[]' WHERE key = ?",
                     (approvals.DRAFT_CARDS_KEY,))  # fmt: skip
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="slack:U1")
    assert item_row(conn, sid)["status"] == "executing"  # approved, not queued for the computer


def test_a_failure_after_the_void_rolls_back(conn: sqlite3.Connection, clock: FakeClock,
                                             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """The new grant and the void are one transaction (K1, L4)."""
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    before = conn.execute("SELECT grant_id, status FROM grants").fetchall()
    real = approvals._audit  # pyright: ignore[reportPrivateUsage]

    def audit(c: sqlite3.Connection, k: FakeClock, row: sqlite3.Row, event: str,
              *a: Any, **kw: Any) -> None:  # fmt: skip
        if event == "approval.reoffered":  # after the void
            raise InvalidInputError("x")
        real(c, k, row, event, *a, **kw)

    monkeypatch.setattr(approvals, "_audit", audit)
    approvals.refresh_draft_cards(conn, clock)
    assert conn.execute("SELECT grant_id, status FROM grants").fetchall() == before


def test_a_decision_inside_the_refresh_window_is_respected(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status is read again after the refresh listed the item (K2, L4): decided meanwhile, it gets
    no new grant."""
    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    real = approvals._refresh_one  # pyright: ignore[reportPrivateUsage]

    def decided(c: sqlite3.Connection, k: FakeClock, s: str) -> int:
        approvals.reject(c, k, s, actor="os_user")  # after the refresh listed it
        return real(c, k, s)

    monkeypatch.setattr(approvals, "_refresh_one", decided)
    before = conn.execute("SELECT grant_id, status FROM grants").fetchall()
    assert approvals.refresh_draft_cards(conn, clock) == 0
    after = conn.execute("SELECT grant_id, status FROM grants").fetchall()
    assert [g[0] for g in after] == [g[0] for g in before]  # no new grant
    assert {g[1] for g in after} == {"voided"}  # voided by the reject, nothing issued


def test_the_refresh_leaves_a_decided_draft_alone(conn: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    """Approved at the computer first: its grant isn't voided and no new one is issued (K2)."""
    sid = _approved_draft(conn, clock, FakeMailSource())
    assert approvals.refresh_draft_cards(conn, clock) == 0
    rows = conn.execute("SELECT status FROM grants WHERE stable_id = ?", (sid,)).fetchall()
    assert [r[0] for r in rows] == ["approved"]


def test_a_draft_with_no_card_gets_a_new_grant_and_no_card(conn: sqlite3.Connection,
                                                            clock: FakeClock) -> None:  # fmt: skip
    """No Slack route: still a new grant, so no earlier button can approve it (W1)."""
    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": TEXT},
                     frozenset())  # fmt: skip
    before = {r[0] for r in conn.execute("SELECT grant_id FROM grants WHERE status = 'issued'")}
    assert approvals.refresh_draft_cards(conn, clock) == 0
    after = {r[0] for r in conn.execute("SELECT grant_id FROM grants WHERE status = 'issued'")}
    assert len(after) == 1 and not before & after


def test_a_draft_whose_payload_changed_is_refused(conn: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    src = FakeMailSource()
    sid = _approved_draft(conn, clock, src)
    with write_tx(conn):  # the reply would now go elsewhere
        conn.execute("UPDATE items SET sender = 'someone@else.example' WHERE stable_id = ?",
                     (sid,))  # fmt: skip
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed" and _drafts(src) == {}


def test_no_drafts_folder_refuses(conn: sqlite3.Connection, clock: FakeClock) -> None:
    kept = tuple(f for f in DEFAULT_FOLDERS if f.name != "Drafts")
    src = FakeMailSource(folders=(*kept, Folder("Brouillons", frozenset())))
    sid = _approved_draft(conn, clock, src)
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed"


def _undo(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, sid: str) -> list[str]:
    return mailbox_actions.undo_item(conn, clock, src, item_row(conn, sid), install=INSTALL,
                                     lost=lambda: False)  # fmt: skip


def test_undo_deletes_the_unchanged_draft(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _approved_draft(conn, clock, src)
    _run(conn, clock, src)
    assert mailbox_actions.undoable(item_row(conn, sid))
    assert "draft deleted" in _undo(conn, clock, src, sid)
    assert _drafts(src) == {} and item_row(conn, sid)["status"] == "undone"


@pytest.mark.parametrize("change", ["edited", "sent"])
def test_undo_leaves_a_draft_you_touched(conn: sqlite3.Connection, clock: FakeClock,
                                         change: str) -> None:  # fmt: skip
    src = FakeMailSource()
    sid = _approved_draft(conn, clock, src)
    _run(conn, clock, src)
    [(uid, stored)] = _drafts(src).items()
    if change == "edited":
        stored.raw = stored.raw.replace(b"Thanks for your note", b"Thanks, I will pay today")
    else:
        del _drafts(src)[uid]
    notes = _undo(conn, clock, src, sid)
    assert any(n.startswith("draft not deleted") for n in notes)
    assert len(_drafts(src)) == (1 if change == "edited" else 0)


def test_digest_undo_route_still_works_for_drafts() -> None:
    assert mailbox_actions.undo_item is digests.mailbox_actions.undo_item
    assert MB > 0
