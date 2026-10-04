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
