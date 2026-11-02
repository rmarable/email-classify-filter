"""The draft card (M8 plan §6): what a model-written draft looks like in Slack, before and after a
decision. Only builds cards; `approvals` posts them and `draft_actions` handles their buttons.

Every draft line starts with "│ ", so a line the model wrote can't pass as ecf's label or note.
Slack can't colour text, so a coloured circle marks a line (operator decision 2026-11-01). A warning
is ecf's fixed wording, never text from an email, since warnings alone are rendered bold.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf_server import cards, draftcheck
from ecf_server.chat import (
    CUT,
    FIRST_TIME,
    LINKS,
    NOT_ADDRESSED,
    PAYMENT,
    REPLY_TO,
    Button,
    Card,
)
from ecf_server.precheck import item_payment_or_fraud
from ecf_server.slack_render import MARK

LABEL = "🔵 Draft written by ecf's model (it can be wrong; it is saved, never sent):"
NOTE = "Check every name, title and sign-off in this draft is yours"
SAVED, DELETED, REJECTED = "🔵 Draft saved", "🔴 Deleted", "🔴 Rejected"
ASK = "How was this draft?"
RATED = "You rated this draft {}. Tap to change it."
UP, DOWN = "👍", "👎"
ELLIPSIS = "…"  # `message.cut` ends a cut excerpt with it

DELETE_WAITING, DELETE_SAVED = "draft_delete", "draft_delete_saved"
RATE_UP, RATE_DOWN, RATE_REASON = "draft_up", "draft_down", "draft_reason"
SHOW_DRAFT = "draft_show"
STILL_SAVED = frozenset({"saved", "still_waiting"})  # still in Drafts, as far as ecf knows


def whole_text(text: str) -> str:
    """The label, the note and every draft line marked: what the card shows, what `fits` measures
    and what a Slack approval relies on, from this one function (AH8). Links aren't clickable."""
    text = cards.defang(text).translate(draftcheck.LINE_BREAKS)  # the checker's idea of a line
    lines = [f"{MARK}{ln}" if ln else MARK.rstrip() for ln in text.split("\n")]
    return "\n".join([LABEL, NOTE, *lines])


def headline(item: sqlite3.Row, title: str = "") -> str:
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    line = f"{cards.sender_line(item, facts)} · {cards.subject_line(item)}"
    return f"{title}: {line}" if title else line


def context_line(item: sqlite3.Row, row: sqlite3.Row | None) -> str:
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    cls: dict[str, Any] = json.loads(item["classification"] or "{}")
    model = row["drafter_model"] if row is not None else None
    parts = [
        str(cls["category"]) if cls.get("category") else "",
        f"sender check {facts.get('auth_result') or 'none'}",
        f"fraud risk {cls['fraud_risk']}" if cls.get("fraud_risk") else "",
        f"written by {cards.model_name(model)}" if model else "written by ecf's model",
    ]
    return " · ".join(p for p in parts if p)


def warnings_for(conn: sqlite3.Connection, item: sqlite3.Row,
                 row: sqlite3.Row | None) -> tuple[str, ...]:  # fmt: skip
    """Only what applies. A reply-to mismatch and a first-time sender don't withhold a draft, so
    they stay on the card (operator decision 2026-11-01); a draft goes to From, never Reply-To."""
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    excerpt = conn.execute("SELECT substr(actor_text, -1) AS last FROM excerpts"
                           " WHERE stable_id = ?", (item["stable_id"],)).fetchone()  # fmt: skip
    found = (
        (FIRST_TIME, not (facts.get("sender_seen_before") or facts.get("sender_confirmed"))),
        (REPLY_TO, facts.get("reply_to_mismatch")),
        (NOT_ADDRESSED, facts.get("recipient_mismatch")),
        (PAYMENT, item_payment_or_fraud(item)),
        (LINKS, row is not None and row["links_copied"]),
        (CUT, excerpt is not None and excerpt["last"] == ELLIPSIS),
    )
    return tuple(wording for wording, applies in found if applies)


def waiting(conn: sqlite3.Connection, item: sqlite3.Row, row: sqlite3.Row | None, text: str, *,  # noqa: PLR0913
            buttons: tuple[Button, ...], note: str = "", status: str = "", title: str = "",
            member: str = "") -> Card:  # fmt: skip
    return Card(headline(item, title), context=context_line(item, row),
                warnings=warnings_for(conn, item, row), text=text, status=status, buttons=buttons,
                note=note, mention=member)  # fmt: skip


def status_only(item: sqlite3.Row, status: str, buttons: tuple[Button, ...] = ()) -> Card:
    """Running, failed or expired: a line, not the draft."""
    return Card(headline(item), status=status, buttons=buttons)


def refused_codes(row: sqlite3.Row) -> list[str]:
    return [str(c) for c in json.loads(row["refuse_codes"] or "[]")]


def decided(item: sqlite3.Row, row: sqlite3.Row, *, also: str = "") -> Card:
    """After a decision. A refused draft was dropped, so there is nothing to rate or show (and a
    refusal isn't a decision, `draft_versions.DECIDED`)."""
    sid, outcome = str(item["stable_id"]), row["outcome"]
    if outcome == "refused":
        codes, short = ", ".join(refused_codes(row)), sid[: cards.SHORT_ID]
        return Card(headline(item), text=(
            f"🔴 The draft check refused this draft ({codes}). Nothing was saved, and ecf flags the"
            f" email at its next check. Details: ecf item show {short}"))  # fmt: skip
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    gone = outcome == "deleted" or doc.get("delete_draft") == "done"  # tracking can't tell (§12.2)
    first = DELETED if gone else REJECTED if outcome == "rejected" else SAVED
    rating = {"up": UP, "down": DOWN}.get(str(row["rating"]))
    lines = [f"{first} · {also}" if also else first, RATED.format(rating) if rating else ASK]
    delete = (Button(DELETE_SAVED, "Delete from Drafts", sid),) if outcome in STILL_SAVED else ()
    return Card(headline(item), text="\n".join(lines), buttons=(
        Button(RATE_UP, UP, sid), Button(RATE_DOWN, DOWN, sid), *delete,
        Button(SHOW_DRAFT, "Show draft", sid)))  # fmt: skip
