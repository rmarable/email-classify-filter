"""Escalations to Slack (SPEC §5.4, §10.1; V1.2 step 6), and the item-card buttons.

The pre-check queues each escalation (`escalations` table, state `pending`); the Slack thread calls
`sweep` on every pass:

- Each pending escalation becomes a card in its address's channel, mentioning you, once the
  channel exists (escalations wait for it, never dropped).
- More than `BURST` (5) in a minute for one address merge into one thread listing them by severity
  (OD-036); later ones in the same minute go into that thread.
- `escalations_per_hour` (OD-035) doesn't apply: every escalation V1.2 makes is fraud, quarantine or
  regulator, which the cap never holds back (OD-212). It applies from V1.3, when classifier
  escalations arrive.
- Escalations V1.1 recorded as pending go into one summary post in the summary channel: counts by
  address and the 5 most severe of the last 7 days (OD-211); no thread each.

Buttons: Show excerpt answers with an ephemeral message only you see (OD-214); Dismiss resolves a
non-payment, non-fraud, non-regulator item (none of V1.2's escalations qualify; the rule is
enforced here as well as by not showing the button). Undo arrives with digests (step 8); Confirm
sender category with the classifier (V1.3, OD-210).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from ecf.errors import NotFoundError, PolicyDeniedError
from ecf.ids import StableId
from ecf.status import OPEN, Status
from ecf_server import cards, items, slack_admin, slack_in, slack_out, slack_routes
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.slack_in import Click
from ecf_server.slack_render import clean
from ecf_server.state_machine import TransitionContext

BURST = 5  # more than this in a minute merge into one thread (OD-036)
BURST_WINDOW = timedelta(minutes=1)
BURST_LIST = 20  # lines listed in a merged thread's card
SUMMARY_DAYS = 7
SUMMARY_TOP = 5
EXCERPT_CHARS = 200
NO_EXCERPT = "No excerpt is kept for this email (for example, one over the size limit)."


def sweep(conn: sqlite3.Connection, clock: Clock) -> int:
    """Post what's waiting; returns how many escalations were handled."""
    ident = slack_admin.identity(conn)
    if ident is None or not ident.member:
        return 0
    handled = _summarize_v11(conn, clock, ident.member)
    aids = [r[0] for r in conn.execute(
        "SELECT DISTINCT address_id FROM escalations WHERE state = 'pending'")]  # fmt: skip
    for aid in aids:
        route = slack_routes.route_for(conn, aid)
        if route is not None:  # no channel yet: they wait
            handled += _post_address(conn, clock, aid, route, ident.member)
    return handled


def _post_address(
    conn: sqlite3.Connection, clock: Clock, aid: str, route: RouteRef, member: str
) -> int:
    rows = _items(conn, "e.state = 'pending' AND e.address_id = ?", (aid,))
    now = clock.now()
    since = to_ts(now - BURST_WINDOW)
    recent = conn.execute(
        "SELECT count(*) FROM escalations WHERE address_id = ? AND posted_at >= ?", (aid, since)
    ).fetchone()[0]
    ident = slack_routes.identity(aid)
    if recent + len(rows) <= BURST:
        for r in rows:
            key = f"item:{r['stable_id']}"
            slack_out.enqueue_post(conn, clock, key=key, route=route, identity=ident,
                                   card=cards.item_card(r, mention=member))  # fmt: skip
            _mark(conn, clock, [r["stable_id"]], "posted", key)
        return len(rows)
    burst = conn.execute(
        "SELECT thread_key FROM escalations WHERE address_id = ? AND state = 'merged'"
        " AND posted_at >= ? ORDER BY posted_at DESC LIMIT 1",
        (aid, since),
    ).fetchone()
    key = f"burst:{aid}:{rows[0]['stable_id']}"
    ordered = sorted(rows, key=lambda r: (cards.severity(_facts(r)), r["created_at"]))
    if burst is None:
        card = _burst_card(f"{len(rows)} fraud or regulatory emails in a minute", ordered, member)
        slack_out.enqueue_post(conn, clock, key=key, route=route, card=card, identity=ident)
        head = key
    else:  # later ones in the same burst go into its thread
        head = str(burst["thread_key"])
        card = _burst_card(f"{len(rows)} more in this burst", ordered, "")
        slack_out.enqueue_post(conn, clock, key=key, route=route, card=card, identity=ident,
                               thread_key=head)  # fmt: skip
    _mark(conn, clock, [r["stable_id"] for r in rows], "merged", head)
    return len(rows)


def _burst_card(title: str, rows: list[sqlite3.Row], member: str) -> Card:
    lines = [_line(r) for r in rows[:BURST_LIST]]
    if len(rows) > BURST_LIST:
        lines.append(f"... and {len(rows) - BURST_LIST} more: ecf inbox")
    note = cards.PAYMENT_NOTE if any(cards.payment_or_fraud(_facts(r)) for r in rows) else ""
    return Card(title, text="\n".join(lines), note=note, mention=member)


def _line(r: sqlite3.Row) -> str:
    facts = _facts(r)
    who = cards.sender_line(r, facts)
    return (f"{cards.TITLES[cards.kind(facts)]}: {who}: {cards.subject_line(r)}"
            f" (ecf item show {r['stable_id'][: cards.SHORT_ID]})")  # fmt: skip


def _summarize_v11(conn: sqlite3.Connection, clock: Clock, member: str) -> int:
    """OD-211: one post for everything V1.1 recorded as pending, once the summary channel exists."""
    route = slack_routes.summary_route(conn)
    if route is None:
        return 0
    rows = _items(conn, "e.state = 'v1.1'", ())
    if not rows:
        return 0
    cutoff = clock.now() - timedelta(days=SUMMARY_DAYS)
    recent = [r for r in rows if from_ts(r["esc_created_at"]) >= cutoff]
    recent_ids = {r["stable_id"] for r in recent}
    counts: dict[str, list[int]] = {}
    for r in rows:
        c = counts.setdefault(r["address_id"], [0, 0])
        c[0] += 1
        c[1] += r["stable_id"] in recent_ids
    top = sorted(recent, key=lambda r: (cards.severity(_facts(r)), r["esc_created_at"]))
    text = ["None in the last 7 days."]
    if top:
        text = ["Most severe of the last 7 days:", *(_line(r) for r in top[:SUMMARY_TOP])]
    per_address = [(aid, f"{n} ({last} in the last 7 days)") for aid, (n, last) in counts.items()]
    card = Card(
        f"{len(rows)} escalation(s) recorded before Slack was connected",
        fields=tuple(sorted(per_address)),
        text="\n".join([*text, "All of them: ecf inbox"]),
        note="Recorded while ecf ran without Slack (V1.1). ecf posts each new one as it happens.",
        mention=member,
    )
    key = "escalations:v1.1-summary"
    slack_out.enqueue_post(conn, clock, key=key, route=route, card=card)
    _mark(conn, clock, [r["stable_id"] for r in rows], "summarized", key)
    return len(rows)


def _items(conn: sqlite3.Connection, where: str, args: tuple[Any, ...]) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT i.*, e.created_at AS esc_created_at FROM escalations e"  # noqa: S608
        f" JOIN items i USING (stable_id) WHERE {where} ORDER BY e.created_at, e.stable_id",
        args,
    ).fetchall()


def _facts(r: sqlite3.Row) -> dict[str, Any]:
    return json.loads(r["facts"] or "{}")


def _mark(conn: sqlite3.Connection, clock: Clock, sids: list[str], state: str, key: str) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.executemany(
            "UPDATE escalations SET state = ?, thread_key = ?, posted_at = ? WHERE stable_id = ?",
            [(state, key, now, sid) for sid in sids],
        )


# ---- buttons --------------------------------------------------------------------------------


@slack_in.handles(cards.SHOW_EXCERPT)
def _show_excerpt(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    """OD-214: an ephemeral message only you see, not kept in the channel's history."""
    item = _item(conn, click.ref)
    row = conn.execute(
        "SELECT classifier_text FROM excerpts WHERE stable_id = ?", (item["stable_id"],)
    ).fetchone()
    text = str(row["classifier_text"] or "") if row else ""
    shown = clean(text, EXCERPT_CHARS) if text else NO_EXCERPT
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=f"Excerpt, only you see this: {shown}")  # fmt: skip
    _audit(conn, clock, item, "item.excerpt_shown", click.user)


@slack_in.handles(cards.DISMISS)
def _dismiss(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    item = _item(conn, click.ref)
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    if not cards.dismissible(facts):
        _tell(conn, clock, click, "Dismiss isn't available for payment, fraud or regulatory "
                                  "email; use `ecf item resolve` at your computer.")  # fmt: skip
        raise PolicyDeniedError("dismiss refused: payment, fraud or regulatory item")
    if Status(item["status"]) not in OPEN:
        _tell(conn, clock, click, "This one is already closed.")
        raise PolicyDeniedError("dismiss refused: item already closed")
    items.transition(conn, clock, StableId(item["stable_id"]), Status.RESOLVED_MANUAL,
                     TransitionContext(), actor=f"slack:{click.user}")  # fmt: skip
    close_card(conn, clock, item["stable_id"], "Dismissed")


def close_card(conn: sqlite3.Connection, clock: Clock, stable_id: str, title: str) -> None:
    """Edit an item's card to `title`, without buttons, if one was posted for it. The edit is
    queued behind the card's post, so it edits that card even if the post hasn't gone out yet."""
    key = f"item:{stable_id}"
    carded = conn.execute(
        "SELECT 1 FROM escalations WHERE stable_id = ? AND thread_key = ?", (stable_id, key)
    ).fetchone()
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (stable_id,)).fetchone()
    route = slack_routes.route_for(conn, item["address_id"]) if item else None
    if carded and route is not None:
        card = cards.item_card(item, title=title)
        slack_out.enqueue_post(conn, clock, key=key, route=route,
                               card=Card(card.title, fields=card.fields),
                               identity=slack_routes.identity(item["address_id"]))  # fmt: skip


def _item(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (ref,)).fetchone()
    if row is None:
        raise NotFoundError("that email is no longer kept")
    return row


def _tell(conn: sqlite3.Connection, clock: Clock, click: Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip


def _audit(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, event: str, member: str
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, ?, ?, 'ok', '{}')",
            (to_ts(clock.now()), item["address_id"], item["stable_id"], event, f"slack:{member}"),
        )
