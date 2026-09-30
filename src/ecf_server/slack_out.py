"""The Slack output queue (SPEC §10.1, §11.3; V1.2 step 3): posts are durable `slack_out` jobs,
ordered per channel and sent one at a time, at most one per second per channel.

- Each post has a key (an item's card, "Needs you", a digest). The message it made is recorded in
  `slack_messages`, so a card is later edited by its stored channel and ts, and a retried post
  edits instead of posting twice.
- Network errors hold the post (never dead-lettered; the attempt isn't counted). After 15 minutes
  of failures while the network is up (the same rule as mail, §13.3) `Slack Delivery Failed` opens.
- A revoked or invalid token holds everything and opens `Slack Delivery Failed` at once. It goes to
  a desktop notification, since Slack can't carry it.
- Other Slack errors retry with the queue's backoff, then the post is dead-lettered and logged.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from ecf.ids import AddressId
from ecf_server import health, jobs
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.chat import Button, Card, ChatSurface, Identity, RouteRef, ThreadRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier

WORKER = "slack-out"
TIMEOUT_S = 30
PACE_S = 1.0
NETWORK_HOLD_S = 60
AUTH_HOLD_S = 300
UNREACHABLE_AFTER = timedelta(minutes=15)
FATAL = frozenset(
    {"invalid_auth", "account_inactive", "token_revoked", "token_expired", "not_authed",
     "missing_scope", "no_permission"}
)  # fmt: skip
GONE = frozenset({"message_not_found"})  # the card was deleted in Slack: post it again
ALERT = "slack_delivery_failed"
FIX = "Fix: ecf slack set-tokens, then ecf slack reauthorize"


def enqueue_post(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    key: str,
    route: RouteRef,
    card: Card,
    thread_key: str | None = None,
    identity: Identity | None = None,
    pin: bool = False,
) -> None:
    payload: dict[str, Any] = {
        "op": "post",
        "key": key,
        "channel": route.channel,
        "card": asdict(card),
        "thread_key": thread_key,
        "identity": asdict(identity) if identity else None,
        "pin": pin,
    }
    jobs.enqueue(conn, clock, jobs.Queue.SLACK_OUT, AddressId(route.channel), payload,
                 timeout_s=TIMEOUT_S)  # fmt: skip


def enqueue_ephemeral(
    conn: sqlite3.Connection, clock: Clock, *, route: RouteRef, user: str, text: str
) -> None:
    payload = {"op": "ephemeral", "channel": route.channel, "user": user, "text": text}
    jobs.enqueue(conn, clock, jobs.Queue.SLACK_OUT, AddressId(route.channel), payload,
                 timeout_s=TIMEOUT_S)  # fmt: skip


def message_ref(conn: sqlite3.Connection, key: str) -> ThreadRef | None:
    row = conn.execute("SELECT channel, ts FROM slack_messages WHERE key = ?", (key,)).fetchone()
    return None if row is None else ThreadRef(RouteRef(row["channel"]), row["ts"])


class SlackSender:
    """Runs `slack_out` jobs, one per call to `run_once`."""

    def __init__(
        self,
        chat: ChatSurface,
        clock: Clock,
        notifier: Notifier,
        *,
        resolve: Callable[[str], bool] = health.resolves,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat, self._clock, self._notifier = chat, clock, notifier
        self._resolve, self._sleep, self._mono = resolve, sleep, monotonic
        self._last_post: dict[str, float] = {}
        self.failing_since: datetime | None = None

    def run_once(self, conn: sqlite3.Connection) -> bool:
        job = jobs.claim(conn, self._clock, jobs.Queue.SLACK_OUT, WORKER)
        if job is None:
            return False
        p = job.payload
        self._pace(str(p["channel"]))
        try:
            self._do(conn, p)
        except SlackNetworkError:
            jobs.hold(conn, self._clock, job.job_id, WORKER, NETWORK_HOLD_S, "network")
            self._network_failed(conn)
            return True
        except SlackError as exc:
            if exc.code in FATAL:
                jobs.hold(conn, self._clock, job.job_id, WORKER, AUTH_HOLD_S, exc.code)
                health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                                  f"Slack refused ecf ({exc.code}). {FIX}")  # fmt: skip
                return True
            state = jobs.fail(conn, self._clock, job.job_id, WORKER, f"slack:{exc.code}")
            log.warning("slack.post_failed", code=exc.code, method=exc.method, state=state)
            return True
        jobs.complete(conn, job.job_id, WORKER)
        self.failing_since = None
        health.resolve_alert(conn, self._clock, self._notifier, ALERT, None)
        return True

    def _do(self, conn: sqlite3.Connection, p: dict[str, Any]) -> None:
        route = RouteRef(str(p["channel"]))
        if p["op"] == "archive":  # a removed address's channel (only channels ecf recorded)
            self._chat.archive(route)
            return
        if p["op"] == "ephemeral":
            self._chat.ephemeral(route, str(p["user"]), str(p["text"]))
            return
        card = _card(p["card"])
        key = str(p["key"])
        existing = message_ref(conn, key)
        ref: ThreadRef | None = None
        if existing is not None:  # a retry or a later edit: never a second post
            try:
                self._chat.update(existing, card)
                ref = existing
            except SlackError as exc:
                if exc.code not in GONE:
                    raise
                _forget(conn, key)  # deleted in Slack: post it again below
        if ref is None:
            thread = message_ref(conn, str(p["thread_key"])) if p.get("thread_key") else None
            ident = p.get("identity")
            identity = Identity(str(ident["name"]), str(ident["icon_emoji"])) if ident else None
            ref = self._chat.post(route, card, thread=thread, identity=identity)
        _remember(conn, self._clock, key, ref, message_ref(conn, str(p.get("thread_key") or "")),
                  {k: v for k, v in p.items() if k != "op"})  # fmt: skip
        if p.get("pin"):
            self._chat.pin(ref)

    def _pace(self, channel: str) -> None:
        last = self._last_post.get(channel)
        if last is not None:
            wait = PACE_S - (self._mono() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_post[channel] = self._mono()

    def _network_failed(self, conn: sqlite3.Connection) -> None:
        now = self._clock.now()
        self.failing_since = self.failing_since or now
        if now - self.failing_since >= UNREACHABLE_AFTER and self._resolve("slack.com"):
            minutes = int((now - self.failing_since).total_seconds() // 60)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"Slack unreachable for {minutes} minutes while the network is up;"
                              " posts are held and will be sent")  # fmt: skip


def _card(d: dict[str, Any]) -> Card:
    return Card(
        title=str(d["title"]),
        fields=tuple((str(a), str(b)) for a, b in d.get("fields", ())),
        text=str(d.get("text", "")),
        buttons=tuple(Button(**b) for b in d.get("buttons", ())),
        note=str(d.get("note", "")),
        mention=str(d.get("mention", "")),
    )


def _remember(
    conn: sqlite3.Connection,
    clock: Clock,
    key: str,
    ref: ThreadRef,
    thread: ThreadRef | None,
    post: dict[str, Any],
) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO slack_messages (key, channel, ts, thread_ts, created_at, updated_at, post)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
            " channel = excluded.channel, ts = excluded.ts, updated_at = excluded.updated_at,"
            " post = excluded.post",
            (key, ref.route.channel, ref.ts, thread.ts if thread else None, now, now,
             json.dumps(post)),
        )  # fmt: skip


def _forget(conn: sqlite3.Connection, key: str) -> None:
    with write_tx(conn):
        conn.execute("DELETE FROM slack_messages WHERE key = ?", (key,))


def repost_all(conn: sqlite3.Connection, clock: Clock) -> int:
    """Queue every recorded post again: each edits its card, or re-posts it if the message is
    gone (`ecf slack reauthorize`). Thread replies follow their top post in the channel's order."""
    rows = conn.execute(
        "SELECT post FROM slack_messages WHERE post != '{}' ORDER BY created_at, key"
    ).fetchall()
    for r in rows:
        p: dict[str, Any] = json.loads(r["post"])
        jobs.enqueue(conn, clock, jobs.Queue.SLACK_OUT, AddressId(str(p["channel"])),
                     {**p, "op": "post", "pin": False}, timeout_s=TIMEOUT_S)  # fmt: skip
    return len(rows)


def dead_posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Posts that gave up (for `ecf doctor` and the daily summary); keys only, no card text."""
    rows = conn.execute(
        "SELECT job_id, payload, last_error FROM jobs WHERE queue = 'slack_out' AND state = 'dead'"
    ).fetchall()
    return [{"job_id": r["job_id"], "key": json.loads(r["payload"]).get("key"),
             "error": r["last_error"]} for r in rows]  # fmt: skip
