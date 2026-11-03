"""The draft card's buttons after a decision (M8 plan §6): 👍/👎, Show draft and Delete from Drafts,
and, inside a check, the mailbox work a click only queues: deleting a saved draft and flagging an
email whose draft was deleted or refused. A click never touches IMAP."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import ConflictError, EcfError
from ecf_server import actions as mail_actions
from ecf_server import (
    approvals,
    checks,
    digests,
    draft_cards,
    draft_data,
    draft_versions,
    inbox,
    jobs,
    mailbox_actions,
    pause,
    slack_in,
    slack_out,
    slack_render,
)
from ecf_server.actions import Planned
from ecf_server.chat import RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.slack_in import Click

REASON_BLOCK = "reason"
REASON_LABELS = {  # one list: Slack allows 10 options of 75 characters (fetched 2026-11-01)
    draft_data.PROMISED: "It promised something",
    "unsupported": "It said something the email doesn't support",
    "followed_email": "It did what the email told it to",
    "shared_details": "It shared details it shouldn't",
    "unanswered": "It didn't answer the question",
    "generic": "Too generic",
    "tone": "Wrong tone",
    "length": "Wrong length",
    "writing": "Writing mistakes",
    "other": "Something else",
}
PER_CHECK = 10
TRIES = 5  # checks in a row a queued delete or flag may fail before it is given up on
BUDGET_S = 120.0  # a hung mailbox waits 60-75 s a row: the rest wait for the next check


def _tell(conn: sqlite3.Connection, clock: Clock, click: Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip


def _rate(conn: sqlite3.Connection, clock: Clock, click: Click, rating: str,
          reason: str | None = None) -> dict[str, Any]:  # fmt: skip
    sid = inbox.find(conn, click.ref)["stable_id"]
    draft_data.rate(conn, clock, approvals.desktop, sid, rating, reason=reason,
                    actor=f"slack:{click.user}")  # fmt: skip
    approvals.show_decided(conn, clock, sid)
    return {}


@slack_in.handles(draft_cards.RATE_UP)
def _rate_up(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    approvals.answering(conn, clock, click, lambda: _rate(conn, clock, click, "up"))


@slack_in.opens_form(draft_cards.RATE_DOWN)
def reason_form(conn: sqlite3.Connection, click: Click) -> dict[str, Any]:
    """One reason, no note: ecf keeps no words written here (operator decision 2026-11-01)."""
    options = [{"text": {"type": "plain_text", "text": label}, "value": value}
               for value, label in REASON_LABELS.items()]  # fmt: skip
    return {
        "type": "modal",
        "callback_id": draft_cards.RATE_REASON,
        "private_metadata": inbox.find(conn, click.ref)["stable_id"],
        "title": {"type": "plain_text", "text": "What was wrong?"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{
            "type": "input",
            "block_id": REASON_BLOCK,
            "label": {"type": "plain_text", "text": "Pick the main reason"},
            "element": {"type": "radio_buttons", "action_id": REASON_BLOCK, "options": options},
        }],
    }  # fmt: skip


@slack_in.handles(draft_cards.RATE_REASON)
def _rate_down(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    reason = click.values.get(REASON_BLOCK)
    try:
        _rate(conn, clock, click, "down", reason)
    except EcfError as exc:  # a form has no channel to answer in: say why in the item's
        item = inbox.find(conn, click.ref)
        digests.tell_member(conn, clock, item["address_id"], f"Not done: {exc.detail}")
        raise


def _draft_shown(conn: sqlite3.Connection, item: sqlite3.Row) -> str:
    """The marked draft, or why there is none: a refused draft was dropped."""
    row = draft_versions.latest(conn, item["stable_id"])
    if row is not None and row["outcome"] == "refused":
        codes = ", ".join(draft_cards.refused_codes(row))
        return f"No draft to show: the draft check refused it ({codes})."
    return approvals.item_draft_text(item) or "No draft to show."


@slack_in.shows_window(draft_cards.SHOW_DRAFT)
def draft_window(conn: sqlite3.Connection, click: Click) -> dict[str, Any]:
    """A window only you see (OD-214), each line marked as on the card (AG4): the longest draft
    ecf saves fits, so a long one no longer sends you to your computer."""
    text = _draft_shown(conn, inbox.find(conn, click.ref))
    return slack_render.modal(draft_cards.SHOW_TITLE, text, draft_cards.CLOSE)


@slack_in.handles(draft_cards.SHOW_DRAFT)
def _show_draft(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    """After the window opened: the audit, as for an excerpt."""
    item = inbox.find(conn, click.ref)
    text = approvals.item_draft_text(item)
    if text and _draft_shown(conn, item) == text:
        approvals.audit_item(conn, clock, item, "draft.shown", click.user)


def _queue_delete(conn: sqlite3.Connection, clock: Clock, click: Click) -> dict[str, Any]:
    item = inbox.find(conn, click.ref)
    row = draft_versions.latest(conn, item["stable_id"])
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    if (row is None or row["outcome"] not in draft_cards.STILL_SAVED
            or not any(r["name"] == "draft_reply" for r in doc.get("done", []))):  # fmt: skip
        raise ConflictError("this draft isn't waiting in Drafts any more")
    doc |= {"delete_draft": "queued", "queued_tries": 0}
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps(doc, sort_keys=True), to_ts(clock.now()),
                      item["stable_id"]))  # fmt: skip
    jobs.make_due(conn, clock, item["address_id"])
    _tell(conn, clock, click, "Deleting it at the next check, within a minute while your computer"
                              " is awake.")  # fmt: skip
    return {}


@slack_in.handles(draft_cards.DELETE_SAVED)
def _delete_saved(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    approvals.answering(conn, clock, click, lambda: _queue_delete(conn, clock, click))


def _set_doc(conn: sqlite3.Connection, sid: str, change: dict[str, Any]) -> None:
    row = conn.execute("SELECT proposal FROM items WHERE stable_id = ?", (sid,)).fetchone()
    doc: dict[str, Any] = json.loads(row["proposal"] or "{}") | change
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?",
                     (json.dumps(doc, sort_keys=True), sid))  # fmt: skip


def _delete_one(conn: sqlite3.Connection, clock: Clock, src: MailSource,
                item: sqlite3.Row) -> None:  # fmt: skip
    """Only ecf's draft, only while it is exactly what ecf saved; the email's labels and flag stay.
    The record goes with the draft, so a later Undo doesn't look for it."""
    sid = item["stable_id"]
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    done: list[dict[str, Any]] = doc.get("done", [])
    record = next((r for r in done if r["name"] == "draft_reply"), None)
    if record is None:  # taken back by Undo meanwhile
        _set_doc(conn, sid, {"delete_draft": "failed"})
        return
    result = mailbox_actions.delete_draft(src, record)
    if result == mailbox_actions.DRAFT_DELETED:
        _set_doc(conn, sid, {"delete_draft": "done",
                             "done": [r for r in done if r is not record]})  # fmt: skip
        draft_versions.set_outcome(conn, clock, sid, "deleted_unsent")  # it was saved: counted so
        approvals.show_decided(conn, clock, sid)
    else:
        _set_doc(conn, sid, {"delete_draft": "failed"})
    digests.tell_member(conn, clock, item["address_id"], f"{result[:1].upper()}{result[1:]}.")


def _flag_one(conn: sqlite3.Connection, clock: Clock, src: MailSource, item: sqlite3.Row,
              max_scan_bytes: int) -> None:  # fmt: skip
    """The email stays in view once its draft is gone (the plan's "queued flag"), under the same
    rules as any flag: nothing in shadow, and a changed message isn't touched."""
    sid = item["stable_id"]
    try:
        if mailbox_actions.refusal(conn, item, [Planned("flag")], {}) is not None:
            raise mail_actions.MessageChangedError("not allowed here")
        uid = mail_actions.verify(src, item, max_scan_bytes)
    except mail_actions.MessageChangedError:
        _set_doc(conn, sid, {"flag_email": "failed"})
        digests.tell_member(conn, clock, item["address_id"],
                            f"Couldn't flag the email for item {sid[:8]}: look at it"
                            " yourself.")  # fmt: skip
        return
    src.set_flagged(uid, True)
    _set_doc(conn, sid, {"flag_email": "done"})


def _tried(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row) -> None:
    """Read again: a delete may have finished before its flag failed."""
    row = conn.execute("SELECT proposal FROM items WHERE stable_id = ?",
                       (item["stable_id"],)).fetchone()  # fmt: skip
    doc: dict[str, Any] = json.loads(row["proposal"] or "{}")
    tries = int(doc.get("queued_tries") or 0) + 1
    if tries < TRIES:
        _set_doc(conn, item["stable_id"], {"queued_tries": tries})
        return
    change = {k: "failed" for k in ("delete_draft", "flag_email") if doc.get(k) == "queued"}
    _set_doc(conn, item["stable_id"], change | {"queued_tries": tries})
    digests.tell_member(conn, clock, item["address_id"],
                        f"Couldn't finish in your mailbox for item {item['stable_id'][:8]}: do it"
                        " by hand.")  # fmt: skip


def run_in_check(conn: sqlite3.Connection, clock: Clock, src: MailSource, address_id: str,
                 _install: str, max_scan_bytes: int, lost: Callable[[], bool]) -> int:  # fmt: skip
    """This address's queued work, bounded. A mailbox error never fails the check: the item stays
    queued for the next one, until TRIES, so it can't block the rest for good. A paused address
    waits."""
    if pause.is_paused(conn, address_id):
        return 0
    rows = conn.execute(
        "SELECT * FROM items WHERE address_id = ? AND (json_extract(proposal, '$.delete_draft') ="
        " 'queued' OR json_extract(proposal, '$.flag_email') = 'queued') LIMIT ?",
        (address_id, PER_CHECK)).fetchall()  # fmt: skip
    n = 0
    start = clock.monotonic()
    for item in rows:
        if lost() or clock.monotonic() - start > BUDGET_S:
            break
        doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
        try:
            if doc.get("delete_draft") == "queued":
                _delete_one(conn, clock, src, item)
            if doc.get("flag_email") == "queued":
                _flag_one(conn, clock, src, item, max_scan_bytes)
        except Exception as exc:  # e.g. the connection dropped, or the folder was renamed
            log.warning("drafts.queued_failed", error_type=type(exc).__name__)
            _tried(conn, clock, item)
            continue
        n += 1
    return n


checks.IN_LEASE.append(run_in_check)
