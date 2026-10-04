"""Carrying out approved and automatic actions in the mailbox (SPEC §6.4, §8.3, §9.5; V1.3 step 5b).

The action runner (`execute.run_once`) now runs inside a check of the item's address, which holds
the address lease and has the mailbox open (V1.2 ran it on the timer with no mailbox). A job for an
address makes the address due at once, so it runs within a minute while the computer is awake.

At execution, before anything is written, everything is checked again (security review of the V1.3
plan, 2026-09-30):
- the address isn't paused, and its stage still allows the actions (hide actions only in `live`;
  labels and flags in `assist` or `live`): an approval given in live doesn't run after a drop to
  assist;
- a move target is still in `move_folders`; archive and junk go only to the folders marked
  `\\Archive` and `\\Junk` (RFC 6154 LIST flags; ecf never guesses a folder name);
- a draft's approved recipient and text still match (outbound_plan.check) and the mailbox has a
  folder marked `\\Drafts` (V1.5 step 2c); a send's own checks (send_actions.refusal: live, outbound
  on, payload, guardrails, recipient, one reply per thread and per sender a week; step 3a);
- the message is still the item's (same UIDVALIDITY, UID, Message-ID and content hash, §6.4);
- the check still holds its lease, before every write.
A refusal fails the item at once with its reason; an IMAP error is retried (3 attempts).

Order: labels and the flag, then mark read, then the copy to `label_folder` (for `suspicious` and
`regulatory` items, §8.3), then at most one move (archive, move or junk). Where the message went is
recorded on the item (`proposal.done`), for Undo. A draft (V1.5, OD-317) is built from the approved
payload (To the email's From address only; In-Reply-To the email; no ecf headers) and appended to
the Drafts folder with `\\Draft`; its Message-ID and content hash are recorded.

**Undo** (from the digest's Undo button, run in the next check): finds the moved message in the
folder it went to by its Message-ID, refuses unless exactly one message there matches and its
content hash is the item's (a sender controls Message-IDs), moves it back to INBOX, then removes the
labels (never `suspicious` or `regulatory`, OD-213), the flag and the read mark ecf added. The copy
to `label_folder` stays. A draft is deleted from the Drafts folder only when exactly one message
there has its Message-ID and it is unchanged (same content hash); a draft you edited or sent is
left alone and Undo says so. The item goes `executed → undoing → undone`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import ConflictError, PolicyDeniedError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import actions as mail_actions
from ecf_server import checks, items, outbound_msg, outbound_plan, pause, send_actions
from ecf_server.actions import Planned, keyword
from ecf_server.clock import Clock, to_ts
from ecf_server.config import current
from ecf_server.db import write_tx
from ecf_server.mail import MailSource
from ecf_server.message import parse
from ecf_server.state_machine import TransitionContext

HIDES = frozenset({"mark_read", "archive", "move", "junk"})
MOVES = frozenset({"archive", "move", "junk"})
ROLE = {"archive": "\\Archive", "junk": "\\Junk"}
DRAFTS = "\\Drafts"
SENDS = send_actions.SENDS
NEVER_UNDONE = frozenset({"suspicious", "regulatory"})
LABEL_FOLDER_FOR = frozenset({"suspicious", "regulatory"})


class RefusedError(PolicyDeniedError):
    """A check at execution time refused the action: the item fails at once, no retry."""


class LeaseLostError(ConflictError):
    """The check lost its lease before a write: retried later, never written without it."""


def _stage(conn: sqlite3.Connection, address_id: str) -> str:
    return str(conn.execute("SELECT stage FROM addresses WHERE address_id = ?",
                            (address_id,)).fetchone()["stage"])  # fmt: skip


def _label_folder(conn: sqlite3.Connection, address_id: str) -> str | None:
    row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    overrides: dict[str, Any] = json.loads(row["overrides"]) if row else {}
    value = overrides.get("label_folder")
    return str(value) if value else None


def refusal(conn: sqlite3.Connection, item: sqlite3.Row, actions: list[Planned],
            folders: dict[str, frozenset[str]]) -> str | None:  # fmt: skip
    """Why these actions may not run now, or None."""
    names = {a.name for a in actions}
    stage = _stage(conn, item["address_id"])
    allowed = set(current(conn)["move_folders"] or [])
    reasons = [
        ("the address is paused", pause.is_paused(conn, item["address_id"])),
        (f"the address is in {stage}: hiding mail needs live", bool(names & HIDES)
         and stage != "live"),
        ("the address is in shadow", bool(names - {"escalate", "leave"}) and stage == "shadow"),
        ("more than one move for one email", len(names & MOVES) > 1),
    ]  # fmt: skip
    for a in actions:
        reasons.append(
            (
                f"{str(a.target)[:60]!r} isn't an allowed folder here",
                a.name == "move" and (a.target not in allowed or a.target not in folders),
            )
        )
        if a.name in ROLE:
            reasons.append((f"the mailbox has no folder marked {ROLE[a.name]}",
                            _role_folder(folders, ROLE[a.name]) is None))  # fmt: skip
        if a.name == "draft_reply":
            reasons.append(("the mailbox has no folder marked \\Drafts",
                            _role_folder(folders, DRAFTS) is None))  # fmt: skip
            why = outbound_plan.check(conn, item, a.name, a.target, a.payload)
            reasons.append((why or "", why is not None))
    return next((why for why, applies in reasons if applies), None)


def _role_folder(folders: dict[str, frozenset[str]], role: str) -> str | None:
    return next((name for name, roles in folders.items() if role in roles), None)


def executor_for(  # noqa: PLR0915 - the writes in their fixed order
    src: MailSource, install: str, max_scan_bytes: int, lost: Callable[[], bool]
) -> Callable[..., list[str]]:
    """An `execute.Executor` bound to this check's open mailbox and lease."""

    def run(  # noqa: PLR0912 - one branch per kind of write
        conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, planned: list[Planned]
    ) -> list[str]:
        folders = {f.name: f.roles for f in src.folders()}
        why = refusal(conn, item, planned, folders) or next(
            (w for a in planned if a.name in SENDS
             if (w := send_actions.refusal(conn, clock, item, a))), None)  # fmt: skip
        if why:
            raise RefusedError(why)

        def guard() -> None:
            if lost():
                raise LeaseLostError("the check lost its lease")

        guard()
        uid = mail_actions.verify(src, item, max_scan_bytes)
        done: list[str] = []
        record: list[dict[str, Any]] = []
        labels = [a.target for a in planned if a.name == "label" and a.target]
        for a in planned:
            if a.name == "label" and a.target:
                guard()
                src.add_keyword(uid, keyword(install, a.target))
                done.append(f"label {a.target}")
                record.append({"name": "label", "target": a.target})
            elif a.name == "flag":
                guard()
                src.set_flagged(uid, True)
                done.append("flag")
                record.append({"name": "flag"})
        if any(a.name == "mark_read" for a in planned):
            guard()
            src.set_seen(uid, True)
            done.append("mark_read")
            record.append({"name": "mark_read"})
        folder = _label_folder(conn, item["address_id"])
        if folder and LABEL_FOLDER_FOR & set(labels) and folder in folders:
            guard()
            src.copy(uid, folder)
            done.append(f"copy to {folder}")
            record.append({"name": "copy", "folder": folder})
        for a in planned:
            if a.name in SENDS:
                guard()  # never hand a message over without the lease (OD-322)
                record.append(send_actions.run(conn, clock, src, item, a, uid))
                done.append(f"sent {a.name.replace('_', ' ')} to {record[-1]['to']}")
        for a in planned:
            if a.name == "draft_reply" and a.payload is not None:
                guard()
                record.append(_save_draft(conn, clock, src, item, a.payload,
                                          str(_role_folder(folders, DRAFTS))))  # fmt: skip
                done.append(f"draft saved to {record[-1]['folder']}")
        for a in planned:
            if a.name in MOVES:
                target = a.target if a.name == "move" else _role_folder(folders, ROLE[a.name])
                if target is None:  # refusal() checked it; never move to a guessed folder
                    raise RefusedError(f"no folder for {a.name}")
                guard()
                src.move(uid, target)
                done.append(f"{a.name} to {target}")
                record.append({"name": a.name, "folder": target})
        _remember(conn, clock, item["stable_id"], record)
        return done

    return run


def _save_draft(conn: sqlite3.Connection, clock: Clock, src: MailSource, item: sqlite3.Row,
                payload: dict[str, Any], folder: str) -> dict[str, Any]:  # fmt: skip
    """Build the approved draft and append it to the Drafts folder; what Undo needs to find it."""
    frm = conn.execute("SELECT email FROM addresses WHERE address_id = ?",
                       (item["address_id"],)).fetchone()["email"]  # fmt: skip
    built = outbound_msg.build_draft(from_addr=str(frm), to_addr=str(payload["to"]),
                                     subject=str(item["subject"] or ""),
                                     body=str(payload["text"]), in_reply_to=item["message_id"],
                                     references=None, date=clock.now())  # fmt: skip
    src.append(folder, built.raw, ["\\Draft"])
    return {"name": "draft_reply", "folder": folder, "message_id": built.message_id,
            "content_hash": built.content_hash}  # fmt: skip


def _remember(conn: sqlite3.Connection, clock: Clock, sid: str,
              record: list[dict[str, Any]]) -> None:  # fmt: skip
    row = conn.execute("SELECT proposal FROM items WHERE stable_id = ?", (sid,)).fetchone()
    doc: dict[str, Any] = json.loads(row["proposal"] or "{}")
    doc["done"] = record
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps(doc, sort_keys=True), to_ts(clock.now()), sid))  # fmt: skip


# ---------------------------------------------------------------------------- Undo


def undoable(item: sqlite3.Row) -> bool:
    """An executed model-driven item with something ecf may take back (never fraud or regulator
    labels, OD-213; an item with any fraud or regulator signal isn't offered at all)."""
    if item["status"] != Status.EXECUTED or not item["decision_source"]:
        return False
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    t: dict[str, Any] = facts.get("triggers") or {}
    if t.get("fraud") or t.get("regulator") or facts.get("quarantined"):
        return False
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    return any(r["name"] != "copy" and r.get("target") not in NEVER_UNDONE
               for r in doc.get("done", []))  # fmt: skip


def undo_item(conn: sqlite3.Connection, clock: Clock, src: MailSource, item: sqlite3.Row, *,
              install: str, lost: Callable[[], bool]) -> list[str]:  # fmt: skip
    """Take back what ecf did to one email (see the module docstring). Raises
    `actions.MessageChangedError` when the message can't be found safely."""
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    record: list[dict[str, Any]] = doc.get("done", [])
    sid = StableId(item["stable_id"])
    items.transition(conn, clock, sid, Status.UNDOING, TransitionContext(reversible=True),
                     actor="service", expected=Status.EXECUTED)  # fmt: skip
    undone: list[str] = []
    try:
        if lost():
            raise LeaseLostError("the check lost its lease")
        for r in record:
            if r["name"] == "draft_reply":
                undone.append(_delete_draft(src, r))
        moved = next((r for r in record if r["name"] in MOVES), None)
        if moved is not None:
            _move_back(src, item, str(moved["folder"]))
            undone.append(f"{moved['name']} back to INBOX")
        if not any(r["name"] in ("label", "flag", "mark_read") for r in record):
            return _undone(conn, clock, sid, undone)  # a draft alone: the email wasn't touched
        uid = _inbox_uid(src, item, moved=moved is not None)
        for r in record:
            if lost():
                raise LeaseLostError("the check lost its lease")
            if r["name"] == "label" and r.get("target") not in NEVER_UNDONE:
                src.remove_keyword(uid, keyword(install, str(r["target"])))
                undone.append(f"label {r['target']}")
            elif r["name"] == "flag":
                src.set_flagged(uid, False)
                undone.append("flag")
            elif r["name"] == "mark_read":
                src.set_seen(uid, False)
                undone.append("mark_read")
    except Exception:
        items.transition(conn, clock, sid, Status.UNDO_FAILED, TransitionContext(),
                         actor="service", expected=Status.UNDOING)  # fmt: skip
        raise
    return _undone(conn, clock, sid, undone)


def _undone(conn: sqlite3.Connection, clock: Clock, sid: StableId, undone: list[str]) -> list[str]:
    items.transition(conn, clock, sid, Status.UNDONE, TransitionContext(), actor="service",
                     expected=Status.UNDOING)  # fmt: skip
    return undone


def _delete_draft(src: MailSource, r: dict[str, Any]) -> str:
    """Delete ecf's draft only if it is still exactly what ecf saved (see the module docstring)."""
    folder = str(r["folder"])
    found = src.find_in(folder, str(r["message_id"]))
    if len(found) != 1:
        return "draft not deleted (not found, or found more than once: edited or sent?)"
    raw = src.fetch_in(folder, found[0])
    if raw is None or parse(raw).content_hash != r["content_hash"]:
        return "draft not deleted (it was edited)"
    src.delete_in(folder, found[0])
    return "draft deleted"


def _move_back(src: MailSource, item: sqlite3.Row, folder: str) -> None:
    mid = item["message_id"]
    if not mid:
        raise mail_actions.MessageChangedError("no Message-ID: move it back by hand")
    found = src.find_in(folder, mid)
    if len(found) != 1:
        raise mail_actions.MessageChangedError(
            f"{len(found)} messages with this Message-ID in {folder}: move it back by hand"
        )
    raw = src.fetch_in(folder, found[0])
    if raw is None or parse(raw, max_scan_bytes=10 * 1024 * 1024).content_hash != item[
            "content_hash"]:  # fmt: skip
        raise mail_actions.MessageChangedError(f"the message in {folder} isn't this email's")
    src.move_back(folder, found[0])


def _inbox_uid(src: MailSource, item: sqlite3.Row, *, moved: bool) -> int:
    """The email's UID in INBOX, checked by content hash: by Message-ID (a sender can reuse one,
    so every match is checked and exactly one must be this email), else, when it never moved,
    its stored UID. Raises MessageChangedError otherwise."""
    candidates = src.find_message_id(item["message_id"]) if item["message_id"] else []
    if not candidates and not moved:  # the stored UID is still valid only if it never moved
        candidates = [int(json.loads(item["locator"])["uid"])]
    mine = [uid for uid in candidates if _is_this_email(src.fetch(uid), item)]
    if len(mine) != 1:
        raise mail_actions.MessageChangedError(
            f"{len(mine)} messages in INBOX are this email: undo the rest by hand"
        )
    return mine[0]


def _is_this_email(raw: bytes | None, item: sqlite3.Row) -> bool:
    return raw is not None and parse(raw, max_scan_bytes=10 * 1024 * 1024).content_hash == item[
        "content_hash"]  # fmt: skip


# ---------------------------------------------------------------------------- in the check

ACTIONS_PER_CHECK = 20


def run_in_check(conn: sqlite3.Connection, clock: Clock, src: MailSource, address_id: str,
                 install: str, max_scan_bytes: int, lost: Callable[[], bool]) -> int:  # fmt: skip
    """Run this address's waiting action jobs with the check's mailbox and lease."""
    from ecf_server import execute  # noqa: PLC0415 - execute imports approvals, which is heavier

    run = executor_for(src, install, max_scan_bytes, lost)
    n = 0
    while (
        n < ACTIONS_PER_CHECK
        and not lost()
        and execute.run_once(conn, clock, run, address_id=address_id)
    ):
        n += 1
    return n


checks.IN_LEASE.append(run_in_check)
