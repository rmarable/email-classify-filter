"""The draft checker on live proposals (M8 plan §1, P-d; OD-583): a draft that fails is dropped
and its email flagged, with the codes on the item; one with no excerpt to check against is
refused too. The tests go through `actor.decide_one`, as a Claude submission does."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf_server import actor, decide
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource
from tests.test_decide import item_row
from tests.test_drafts import FACTS, REPLY
from tests.test_mailbox_actions import (  # pyright: ignore[reportPrivateUsage]
    _address,  # pyright: ignore[reportPrivateUsage]
    _mail_item,  # pyright: ignore[reportPrivateUsage]
)

CLEAN = "Hello,\n\nThanks for your note. I will look at this and reply soon.\n\n[Your Name]"
PROMISE = "Hello,\n\nI will pay the 2,000 invoice on 2026-11-30.\n\n[Your Name]"


def _propose(conn: sqlite3.Connection, clock: FakeClock, text: str, *,
             excerpt: str | None = "Please confirm.") -> dict[str, Any]:  # fmt: skip
    _address(conn, clock)
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    with write_tx(conn):
        conn.execute("DELETE FROM excerpts WHERE stable_id = ?", (sid,))
        if excerpt is not None:
            conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, ?)",
                         (sid, excerpt))  # fmt: skip
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": "draft_reply", "target": "", "reason": "r", "text": text},
                     frozenset())  # fmt: skip
    return json.loads(item_row(conn, sid)["proposal"])


def _names(doc: dict[str, Any]) -> set[str]:
    return {a["name"] for a in doc["actions"]}


def test_a_clean_draft_goes_on_to_approval(conn: sqlite3.Connection, clock: FakeClock) -> None:
    doc = _propose(conn, clock, CLEAN)
    assert "draft_reply" in _names(doc)
    assert doc["plan"]["dropped"] == []


def test_a_refused_draft_is_dropped_flagged_and_says_why(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    doc = _propose(conn, clock, PROMISE)
    assert "draft_reply" not in _names(doc)
    assert "flag" in _names(doc)
    [gone] = doc["plan"]["dropped"]
    assert gone["name"] == "draft_reply"
    assert gone["why"].startswith("draft_checker: ")
    assert "date" in gone["why"]


def test_the_reason_names_codes_and_no_draft_text(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    doc = _propose(conn, clock, PROMISE)
    assert "2,000" not in json.dumps(doc)
    assert "2026-11-30" not in json.dumps(doc)


def test_a_draft_with_no_excerpt_is_refused_as_unchecked(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    doc = _propose(conn, clock, CLEAN, excerpt=None)
    assert "draft_reply" not in _names(doc)
    [gone] = doc["plan"]["dropped"]
    assert gone["why"] == "draft_checker: unchecked"


def test_the_checker_reads_the_emails_own_links(conn: sqlite3.Connection, clock: FakeClock) -> None:
    text = "Hello,\n\nThe terms are at https://acme.example/terms as you said.\n\n[Your Name]"
    doc = _propose(conn, clock, text, excerpt="See https://acme.example/terms please.")
    assert "draft_reply" in _names(doc)


def test_a_link_the_email_lacks_is_refused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    text = "Hello,\n\nThe terms are at https://acme.example/terms as you said.\n\n[Your Name]"
    doc = _propose(conn, clock, text)
    assert "new_link" in doc["plan"]["dropped"][0]["why"]
