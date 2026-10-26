"""The Slack output queue (SPEC §10.1, §11.3; V1.2 step 3): posts are durable `slack_out` jobs,
ordered per channel and sent one at a time, at most one per second per channel.

- Each post has a key (an item's card, "Needs you", a digest). The message it made is recorded in
  `slack_messages`, so a card is later edited by its stored channel and ts, and a retried post
  edits instead of posting twice.
- Network errors hold the post (never dead-lettered; the attempt isn't counted). After 15 minutes
  of failures while the network is up (the same rule as mail, §13.3) `Slack Delivery Failed` opens.
- A revoked or invalid token holds everything and opens `Slack Delivery Failed` at once. It goes to
  a desktop notification, since Slack can't carry it.
- A channel that is gone (archived, deleted, or ecf removed from it) isn't retried: ecf forgets it,
  so the next channel pass (30 s) creates it again, re-queues an escalation card that was lost, and
  opens `Slack Delivery Failed` (V1.2 review, 2026-09-30).
- Rate limits hold the post, like the network (not counted as a failure).
- Other Slack errors retry with the queue's backoff; a post that gives up opens `Slack Delivery
  Failed` naming what was lost (`ecf doctor` lists them too).
- Each job that completes (posted, given up, or its channel gone) leaves one `slack_calls` row:
  queued and done times, tries, seconds in Slack calls, rate-limit holds and pacing, the key's
  kind only (OD-507). `ecf stats` reads it; pruned with the logs, not in the bundle.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from ecf.ids import AddressId
from ecf.status import OPEN
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
CHANNEL_GONE = frozenset({"channel_not_found", "is_archived", "not_in_channel"})
RATE_LIMITED = frozenset({"ratelimited", "rate_limited"})
RATE_HOLD_S = 60
PINNED = frozenset({"needs-you"})  # re-pinned when re-posted after a deletion in Slack
ALERT = "slack_delivery_failed"
FIX = "Fix: ecf slack set-tokens (ecf slack reauthorize only for missing_scope)"


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
        self._tally: dict[str, _Tally] = {}  # per job until it completes (`slack_calls`)
        self.failing_since: datetime | None = None

    def run_once(self, conn: sqlite3.Connection) -> bool:
        job = jobs.claim(conn, self._clock, jobs.Queue.SLACK_OUT, WORKER)
        if job is None:
            return False
        p = job.payload
        tally = self._tally.setdefault(job.job_id, _Tally())
        tally.tries += 1
        tally.pace_s += self._pace(str(p["channel"]))
        began = self._mono()
        try:
            self._do(conn, p)
        except SlackNetworkError:
            tally.call_s += self._mono() - began
            jobs.hold(conn, self._clock, job.job_id, WORKER, NETWORK_HOLD_S, "network")
            self._network_failed(conn)
            return True
        except SlackError as exc:
            tally.call_s += self._mono() - began
            self._slack_failed(conn, job, p, exc)
            return True
        tally.call_s += self._mono() - began
        jobs.complete(conn, job.job_id, WORKER)
        self._record(conn, job, p, "posted")
        self.failing_since = None
        health.resolve_alert(conn, self._clock, self._notifier, ALERT, None)
        return True

    def _record(self, conn: sqlite3.Connection, job: jobs.Job, p: dict[str, Any],
                outcome: str) -> None:  # fmt: skip
        """One `slack_calls` row for a job that completed (OD-507, §5 e): times and counts, the
        key's kind only, never its text. A restart loses the tallies of jobs then in flight."""
        t = self._tally.pop(job.job_id, _Tally())
        kind = str(p.get("key") or p.get("op", "post")).partition(":")[0][:40]
        now = to_ts(self._clock.now())
        with write_tx(conn):
            row = conn.execute("SELECT created_at FROM jobs WHERE job_id = ?",
                               (job.job_id,)).fetchone()  # fmt: skip
            conn.execute(
                "INSERT INTO slack_calls (ts, queued_at, op, kind, outcome, attempts, call_ms,"
                " rate_hold_s, pace_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, row["created_at"] if row else now, str(p.get("op", "post")), kind, outcome,
                 max(t.tries, 1), round(t.call_s * 1000), t.rate_hold_s,
                 round(t.pace_s * 1000)),
            )  # fmt: skip

    def _slack_failed(
        self, conn: sqlite3.Connection, job: jobs.Job, p: dict[str, Any], exc: SlackError
    ) -> None:
        """Slack answered with an error: hold, re-route, retry or give up (see the module notes)."""
        if exc.code in FATAL:
            jobs.hold(conn, self._clock, job.job_id, WORKER, AUTH_HOLD_S, exc.code)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"Slack refused ecf ({exc.code}). {FIX}")  # fmt: skip
        elif exc.code in RATE_LIMITED:
            self._tally.setdefault(job.job_id, _Tally()).rate_hold_s += RATE_HOLD_S
            jobs.hold(conn, self._clock, job.job_id, WORKER, RATE_HOLD_S, exc.code)
        elif exc.code in CHANNEL_GONE and p.get("op") == "post":
            jobs.complete(conn, job.job_id, WORKER)
            self._record(conn, job, p, "channel_gone")
            _channel_gone(conn, self._clock, str(p["channel"]), str(p["key"]), exc.code)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"A Slack channel ecf posts to is gone ({exc.code}); ecf creates it"
                              " again and re-posts escalations there")  # fmt: skip
        else:
            state = jobs.fail(conn, self._clock, job.job_id, WORKER, f"slack:{exc.code}")
            log.warning("slack.post_failed", code=exc.code, method=exc.method, state=state)
            if state == "dead":
                self._record(conn, job, p, "dead")
                health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                                  f"A Slack post gave up ({exc.code}): {_what(p)}. The email is"
                                  " still in ecf inbox")  # fmt: skip

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
        pin = bool(p.get("pin"))
        if existing is not None:  # a retry or a later edit: never a second post
            try:
                self._chat.update(existing, card)
                ref = existing
            except SlackError as exc:
                if exc.code not in GONE:
                    raise
                _forget(conn, key)  # deleted in Slack: post it again below
                pin = pin or key in PINNED  # and pin it again (V1.2 review, 2026-09-30)
        if ref is None:
            thread = message_ref(conn, str(p["thread_key"])) if p.get("thread_key") else None
            ident = p.get("identity")
            identity = Identity(str(ident["name"]), str(ident["icon_emoji"])) if ident else None
            ref = self._chat.post(route, card, thread=thread, identity=identity)
        _remember(conn, self._clock, key, ref, message_ref(conn, str(p.get("thread_key") or "")),
                  {k: v for k, v in p.items() if k != "op"})  # fmt: skip
        if pin:
            self._chat.pin(ref)

    def _pace(self, channel: str) -> float:
        """Wait out the channel's 1 post per second; returns the seconds slept."""
        last = self._last_post.get(channel)
        slept = 0.0
        if last is not None:
            wait = PACE_S - (self._mono() - last)
            if wait > 0:
                self._sleep(wait)
                slept = wait
        self._last_post[channel] = self._mono()
        return slept

    def _network_failed(self, conn: sqlite3.Connection) -> None:
        now = self._clock.now()
        self.failing_since = self.failing_since or now
        if now - self.failing_since >= UNREACHABLE_AFTER and self._resolve("slack.com"):
            minutes = int((now - self.failing_since).total_seconds() // 60)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"Slack unreachable for {minutes} minutes while the network is up;"
                              " posts are held and will be sent")  # fmt: skip


@dataclass
class _Tally:
    """One job's Slack work so far: tries, seconds in calls, rate-limit holds, pacing."""

    tries: int = 0
    call_s: float = 0.0
    rate_hold_s: int = 0
    pace_s: float = 0.0


def _what(p: dict[str, Any]) -> str:
    """What a lost post was, without its text: the kind of key and a short ID."""
    kind, _, rest = str(p.get("key") or p.get("op", "post")).partition(":")
    return f"{kind} {rest[:8]}".strip()


def _channel_gone(
    conn: sqlite3.Connection, clock: Clock, channel: str, key: str, code: str
) -> None:
    """Forget a channel Slack says is gone, so the next channel pass creates it again, and put an
    escalation whose card was lost back in the queue."""
    now = to_ts(clock.now())
    with write_tx(conn):
        for (aid,) in conn.execute("SELECT address_id FROM routes WHERE route_ref = ?",
                                   (channel,)).fetchall():  # fmt: skip
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, 'slack.channel_gone', 'service', 'ok', ?)",
                (now, aid, json.dumps({"channel": channel, "code": code})),
            )
        conn.execute("DELETE FROM routes WHERE route_ref = ?", (channel,))
        conn.execute("DELETE FROM settings WHERE key = 'slack_summary_channel' AND value = ?",
                     (json.dumps(channel),))  # fmt: skip
        conn.execute("DELETE FROM slack_messages WHERE channel = ?", (channel,))
        if key.startswith("item:"):
            conn.execute("UPDATE escalations SET state = 'pending' WHERE stable_id = ?",
                         (key.removeprefix("item:"),))  # fmt: skip


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


def repost_all(
    conn: sqlite3.Connection, clock: Clock, exclude: frozenset[str] = frozenset()
) -> int:
    """Queue the posts that still matter again, as last sent: "Needs you" and the cards of open
    items, except the keys in `exclude` (cards `approvals.repost_cards` renders afresh). Each
    edits its card, or re-posts it if the message is gone. Old alerts, digests and closed items
    are left alone: re-posting them brought back what you'd deleted (V1.2 review, 2026-09-30).
    A card recorded in a channel ecf no longer uses is skipped, so the edit can't fail there and
    forget the channel in use (OD-557); plain SQL, since `slack_routes` imports this module."""
    rows = conn.execute(
        "SELECT m.post FROM slack_messages m LEFT JOIN items i ON m.key = 'item:' || i.stable_id"
        " WHERE m.post != '{}' AND m.key NOT IN (SELECT value FROM json_each(?))"
        " AND ((m.key = 'needs-you' AND m.channel = json_extract((SELECT value FROM settings"
        "  WHERE key = 'slack_summary_channel'), '$'))"
        " OR (i.stable_id IS NOT NULL AND i.status IN (SELECT value FROM json_each(?))"
        "  AND EXISTS (SELECT 1 FROM routes r WHERE r.address_id = i.address_id"
        "   AND r.surface = 'slack' AND r.route_ref = m.channel)))"
        " ORDER BY m.created_at, m.key",
        (json.dumps(sorted(exclude)), json.dumps(sorted(OPEN))),
    ).fetchall()
    for r in rows:
        p: dict[str, Any] = json.loads(r["post"])
        jobs.enqueue(conn, clock, jobs.Queue.SLACK_OUT, AddressId(str(p["channel"])),
                     {**p, "op": "post", "pin": False}, timeout_s=TIMEOUT_S)  # fmt: skip
    return len(rows)


# Dead posts no later post of the same card has superseded (OD-557). A job row doesn't record which
# call it made, so any finished post job counts as the card's latest success: a post, an edit, a
# completion on a gone channel, or a dead job `ecf slack repost` marked done.
_LIVE_DEAD = (
    "d.queue = 'slack_out' AND d.state = 'dead' AND json_extract(d.payload, '$.key') IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM jobs o WHERE o.queue = 'slack_out' AND o.state = 'done'"
    "  AND json_extract(o.payload, '$.op') = 'post'"
    "  AND json_extract(o.payload, '$.key') = json_extract(d.payload, '$.key')"
    "  AND (o.created_at > d.created_at OR (o.created_at = d.created_at AND o.rowid > d.rowid)))"
)


def lost_keys(conn: sqlite3.Connection) -> set[str]:
    """The keys with a dead post that no later post of the same card has superseded."""
    rows = conn.execute(
        f"SELECT DISTINCT json_extract(d.payload, '$.key') FROM jobs d WHERE {_LIVE_DEAD}"  # noqa: S608
    ).fetchall()
    return {str(r[0]) for r in rows}


def dead_posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Posts that gave up (for `ecf doctor` and the daily summary); keys only, no card text."""
    rows = conn.execute(
        "SELECT job_id, payload, last_error FROM jobs WHERE queue = 'slack_out' AND state = 'dead'"
    ).fetchall()
    return [{"job_id": r["job_id"], "key": json.loads(r["payload"]).get("key"),
             "error": r["last_error"]} for r in rows]  # fmt: skip
