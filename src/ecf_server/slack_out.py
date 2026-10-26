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
- A post Slack refuses (the same payload would fail again) gives up at once. Slack's own outage
  codes hold the post: given up after about 15 minutes if other posts get through meanwhile, after
  about 2 hours whatever happens. Other errors retry after 30 s, 1, 2 and 5 minutes, then give up
  (OD-557).
- A post that gives up is one notice, `Slack Delivery Failed: a post wasn't sent`, on the desktop
  and by email if routed, never in Slack; worded by what was lost and what to do; at most 5 an
  hour (20 for cards of emails waiting for you), then one summary. An email's card also gets an
  audit row. A refused card isn't posted again by itself (`refused_keys`); `ecf slack repost`
  brings it back; `ecf doctor` counts them (OD-557).
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

# What Slack's error codes mean for a post (OD-557; docs.slack.dev, chat.postMessage and
# chat.update, Errors, fetched 2026-10-25). Refused: the same payload fails again, so the post
# gives up at once. Outage: Slack's own trouble, so the post is held, and given up only when other
# posts get through (this one's problem) or after 2 hours. Anything else: `POST_BACKOFF_S`.
REFUSED = frozenset(
    {"invalid_blocks", "invalid_blocks_format", "msg_blocks_too_long", "msg_too_long", "no_text",
     "invalid_arguments", "invalid_arg_name", "invalid_array_arg", "invalid_attachments",
     "too_many_attachments", "attachment_payload_limit_exceeded", "markdown_text_conflict",
     "invalid_metadata_format", "invalid_metadata_schema", "metadata_too_large", "block_mismatch"}
)  # fmt: skip
OUTAGE = frozenset({"internal_error", "service_unavailable", "fatal_error", "request_timeout"})
OUTAGE_HOLD_S = 60
OUTAGE_CAP_HOLDS = 15  # about 15 minutes, when other posts get through meanwhile
OUTAGE_MAX_HOLDS = 120  # about 2 hours, whatever else happens
POST_BACKOFF_S = (30, 60, 120, 300)  # unknown codes: about 8.5 minutes, then dead

# A post that gives up is one notice (desktop, and email if routed; never Slack, so a refused
# notice can't loop), at most this many an hour, then one summary.
LOST = "a post wasn't sent"
NOTICES_PER_WINDOW = 5
ITEM_NOTICES_PER_WINDOW = 20  # cards of emails waiting for you
NOTICE_WINDOW = timedelta(hours=1)
WAITING_KINDS = frozenset({"question", "approval", "approval_stepup", "delayed", "escalation"})

OnLost = Callable[[sqlite3.Connection, str], None]
LostKind = Callable[[sqlite3.Connection, str], tuple[str, str]]


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
    """Runs `slack_out` jobs, one per call to `run_once`. `on_lost` sends the one-shot notice for
    a post that gave up and `lost_kind` says what it was, both from `slack_runtime` (this module
    may import neither `alerts` nor `approvals`, OD-557)."""

    def __init__(
        self,
        chat: ChatSurface,
        clock: Clock,
        notifier: Notifier,
        *,
        resolve: Callable[[str], bool] = health.resolves,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        on_lost: OnLost | None = None,
        lost_kind: LostKind | None = None,
    ) -> None:
        self._chat, self._clock, self._notifier = chat, clock, notifier
        self._resolve, self._sleep, self._mono = resolve, sleep, monotonic
        self._on_lost = on_lost or self._notify_desktop
        self._lost_kind = lost_kind or _no_kind
        self._last_post: dict[str, float] = {}
        self._tally: dict[str, _Tally] = {}  # per job until it completes (`slack_calls`)
        self.failing_since: datetime | None = None
        self._successes = 0  # posts that went through, so an outage hold can tell whose fault
        self._window = _Window()

    def run_once(self, conn: sqlite3.Connection) -> bool:
        self._flush_window(conn)
        for dead in jobs.expire_claims(conn, self._clock, jobs.Queue.SLACK_OUT):
            try:
                self._expired(conn, dead)
            except Exception as e:  # the job is dead already: never stop the pass or the others
                log.error("slack.notice_failed", error_type=type(e).__name__)
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
        self._successes += p.get("op") == "post"  # a short reply or an archive proves no post
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
        elif p.get("op") == "ephemeral" or (p.get("op") == "archive" and exc.code in CHANNEL_GONE):
            # a short reply is best effort, and a gone channel's closing is done already: neither
            # holds up its channel, nor sends a notice, nor counts as Slack working (OD-557)
            jobs.complete(conn, job.job_id, WORKER)
            self._record(conn, job, p, "channel_gone" if exc.code in CHANNEL_GONE else "dead")
            log.info("slack.dropped", op=str(p.get("op")), code=exc.code)
        elif exc.code in OUTAGE:
            self._outage(conn, job, p, exc)
        elif exc.code in CHANNEL_GONE and p.get("op") == "post":
            jobs.complete(conn, job.job_id, WORKER)
            self._record(conn, job, p, "channel_gone")
            _channel_gone(conn, self._clock, str(p["channel"]), str(p["key"]), exc.code)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"A Slack channel ecf posts to is gone ({exc.code}); ecf creates it"
                              " again and re-posts escalations there")  # fmt: skip
        else:
            refused = exc.code in REFUSED
            state = jobs.fail(conn, self._clock, job.job_id, WORKER, _error(exc), final=refused,
                              backoff_s=POST_BACKOFF_S)  # fmt: skip
            log.warning("slack.post_failed", code=exc.code, method=exc.method, state=state)
            if state == "dead":
                self._lost(conn, job, p, exc, "refused" if refused else "unknown")

    def _outage(
        self, conn: sqlite3.Connection, job: jobs.Job, p: dict[str, Any], exc: SlackError
    ) -> None:
        """Held, uncounted, until Slack takes it; given up after `OUTAGE_MAX_HOLDS` whatever else
        happens, or after `OUTAGE_CAP_HOLDS` when other posts got through meanwhile (the trouble
        is this post's). The longer limit is checked first, so a post held the full 2 hours is
        worded as Slack's silence even if a post got through at the last moment. The counts are
        in memory: a restart or a Slack reload starts them again, which only lengthens a hold.
        A give-up's notice goes before the alert write, so a failing alert can't lose it."""
        t = self._tally.setdefault(job.job_id, _Tally())
        if t.outage_holds >= OUTAGE_MAX_HOLDS:
            flavour = "quiet"
        elif t.outage_holds >= OUTAGE_CAP_HOLDS and self._successes > t.outage_mark:
            flavour = "cap"
        else:
            if t.outage_holds == 0:
                t.outage_mark = self._successes
            t.outage_holds += 1
            jobs.hold(conn, self._clock, job.job_id, WORKER, OUTAGE_HOLD_S, exc.code)
            self._outage_failed(conn, exc.code)
            return
        jobs.fail(conn, self._clock, job.job_id, WORKER, _error(exc), final=True)
        log.warning("slack.post_failed", code=exc.code, method=exc.method, state="dead")
        self._lost(conn, job, p, exc, "outage", flavour)
        self._outage_failed(conn, exc.code)

    def _lost(
        self, conn: sqlite3.Connection, job: jobs.Job, p: dict[str, Any], exc: SlackError,
        reason: str, flavour: str = "",
    ) -> None:  # fmt: skip
        """A post gave up: its `slack_calls` row, an audit row for an email's card, then the
        notice. A failing write or notice is logged and the pass goes on: the job is dead
        already, and nothing comes back to it."""
        try:
            self._record(conn, job, p, "dead")
        except Exception as e:  # a busy database: the audit row and notice still go
            log.error("slack.record_failed", error_type=type(e).__name__)
        key = str(p.get("key") or p.get("op", "post"))
        try:
            _audit_lost(conn, self._clock, key, exc, reason)
        except Exception as e:  # the job is dead and recorded; the notice still goes out
            log.error("slack.audit_failed", error_type=type(e).__name__)
        try:
            kind, short = self._lost_kind(conn, key)
            deaths = post_deaths(conn, key) if exc.method == "chat.postMessage" else 0
            self._notify(conn, kind, notice_text(kind, short, exc.method, reason, flavour, deaths))
        except Exception as e:  # never stops the pass: the dead row and audit are written
            log.error("slack.notice_failed", error_type=type(e).__name__)

    def _expired(self, conn: sqlite3.Connection, job: jobs.Job) -> None:
        """A job whose claim kept expiring (each pass that claimed it failed before finishing it,
        say on a busy database) until it dead-lettered: a lost post like any other. Its method
        isn't known: an update when the card is recorded in Slack, else a post (also when that
        read fails). A short reply or an archive sends nothing (OD-557)."""
        p = job.payload
        if p.get("op") != "post":
            log.info("slack.dropped", op=str(p.get("op")), code="claim_expired")
            return
        try:
            recorded = message_ref(conn, str(p["key"])) is not None
        except sqlite3.Error:
            recorded = False
        method = "chat.update" if recorded else "chat.postMessage"
        log.warning("slack.post_failed", code="claim_expired", method=method, state="dead")
        self._lost(conn, job, p, SlackError(method, "claim_expired"), "unknown")

    def _notify(self, conn: sqlite3.Connection, kind: str, detail: str) -> None:
        """Send the notice unless this window has had its share; then count it for the summary."""
        now = self._clock.now()
        self._flush_window(conn)
        w = self._window  # after the flush, which may have replaced it
        if w.start is None:
            w.start = now
        if kind in WAITING_KINDS:
            allowed = w.item_sent < ITEM_NOTICES_PER_WINDOW
            w.item_sent += allowed
        else:
            allowed = w.sent < NOTICES_PER_WINDOW
            w.sent += allowed
        if allowed:
            self._on_lost(conn, detail)
            return
        w.suppressed += 1
        w.suppressed_waiting += kind in WAITING_KINDS
        log.info("slack.notice_suppressed", kind=kind)

    def flush_notices(self, conn: sqlite3.Connection) -> None:
        """The sender is being dropped (a Slack reload or `ecf slack remove`): send the summary
        this window owes now, since the next sender starts a fresh window. A restart still loses
        it (OD-557)."""
        self._flush_window(conn, ended=True)

    def _flush_window(self, conn: sqlite3.Connection, *, ended: bool = False) -> None:
        """Once the window has ended: close it, then send the summary it owes, if any."""
        w = self._window
        if w.start is None or (not ended and self._clock.now() - w.start < NOTICE_WINDOW):
            return
        n, k = w.suppressed, w.suppressed_waiting
        self._window = _Window()
        if not n:
            return
        try:
            self._on_lost(conn, f"{n} more posts didn't reach Slack, {k} of them cards of emails"
                          " waiting for you; ecf doctor lists them; ecf slack repost puts approval"
                          " cards back")  # fmt: skip
        except Exception as e:  # the counts are cleared already: never blocks the pass
            log.error("slack.notice_failed", error_type=type(e).__name__)

    def _notify_desktop(self, _conn: sqlite3.Connection, detail: str) -> None:
        self._notifier.notify(f"[ecf-alert] {health.TITLES[ALERT]}: {LOST}", detail)

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
        if existing is not None and _moved(conn, existing.route.channel, route.channel):
            # recorded in a channel ecf has since replaced: post in the job's channel instead
            # (editing the old one fails, and the failure would forget the job's channel)
            _forget(conn, key)
            existing = None
            pin = pin or key in PINNED
        if existing is not None:  # a retry or a later edit: never a second post
            try:
                self._chat.update(existing, card)
                ref = existing
            except SlackError as exc:
                if exc.code not in GONE:
                    raise
                _forget(conn, key)  # deleted in Slack: post it again below
                pin = pin or key in PINNED  # and pin it again (V1.2 review, 2026-09-30)
        thread = message_ref(conn, str(p["thread_key"])) if p.get("thread_key") else None
        if thread is not None and _moved(conn, thread.route.channel, route.channel):
            thread = None  # its parent is in a channel ecf replaced: post top-level
        if ref is None:
            ident = p.get("identity")
            identity = Identity(str(ident["name"]), str(ident["icon_emoji"])) if ident else None
            ref = self._chat.post(route, card, thread=thread, identity=identity)
        _remember(conn, self._clock, key, ref, thread,
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

    def _outage_failed(self, conn: sqlite3.Connection, code: str) -> None:
        """An outage answer: the same 15 minutes as the network, and the latest kind's text
        (Slack answered, so no reachability check)."""
        now = self._clock.now()
        self.failing_since = self.failing_since or now
        if now - self.failing_since >= UNREACHABLE_AFTER:
            minutes = int((now - self.failing_since).total_seconds() // 60)
            health.open_alert(conn, self._clock, self._notifier, ALERT, None,
                              f"Slack has answered {code} to every post for {minutes} minutes;"
                              " posts are held. A post that keeps failing is given up with a"
                              " notice (at most 2 hours)")  # fmt: skip


@dataclass
class _Tally:
    """One job's Slack work so far: tries, seconds in calls, rate-limit holds, pacing; and its
    outage holds, with the success count at the first (OD-557)."""

    tries: int = 0
    call_s: float = 0.0
    rate_hold_s: int = 0
    pace_s: float = 0.0
    outage_holds: int = 0
    outage_mark: int = 0


@dataclass
class _Window:
    """This hour's notices for posts that gave up: sent, and held back for the summary."""

    start: datetime | None = None
    sent: int = 0
    item_sent: int = 0
    suppressed: int = 0
    suppressed_waiting: int = 0


def _no_kind(_conn: sqlite3.Connection, _key: str) -> tuple[str, str]:
    return ("none", "")


def _error(exc: SlackError) -> str:
    """A dead job's `last_error`: the Slack method and code, never the payload (OD-557)."""
    return f"slack:{exc.method}:{exc.code}"


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


def _moved(conn: sqlite3.Connection, recorded: str, channel: str) -> bool:
    """A card recorded in a channel ecf no longer posts to, while the job's channel is one it
    does. A DM is recorded under its D channel, never a route, and posted to the person, so it
    never counts as moved."""
    return recorded != channel and _in_use(conn, channel) and not _in_use(conn, recorded)


def _in_use(conn: sqlite3.Connection, channel: str) -> bool:
    """A channel ecf posts to now: an address's or the summary channel (plain SQL, since
    `slack_routes` imports this module)."""
    return bool(conn.execute(
        "SELECT 1 FROM routes WHERE surface = 'slack' AND route_ref = ? UNION ALL"
        " SELECT 1 FROM settings WHERE key = 'slack_summary_channel' AND value = ?",
        (channel, json.dumps(channel))).fetchone())  # fmt: skip


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
        "SELECT m.post FROM slack_messages m LEFT JOIN items i"
        " ON m.key LIKE 'item:%' AND i.stable_id = substr(m.key, 6)"
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
# completion on a gone channel, or a dead job `ecf slack repost` marked done. The `visible_at` bound
# only lets the `jobs_ready` index narrow the scan: a job's `visible_at` is never below its own
# `created_at`, so it follows from the next line (a clock that went backwards could hold a dead
# post back, never loop it).
_LIVE_DEAD = (
    "d.queue = 'slack_out' AND d.state = 'dead' AND json_extract(d.payload, '$.key') IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM jobs o WHERE o.queue = 'slack_out' AND o.state = 'done'"
    "  AND json_extract(o.payload, '$.op') = 'post'"
    "  AND o.visible_at >= d.created_at"
    "  AND json_extract(o.payload, '$.key') = json_extract(d.payload, '$.key')"
    "  AND (o.created_at > d.created_at OR (o.created_at = d.created_at AND o.rowid > d.rowid)))"
)


def lost_keys(conn: sqlite3.Connection) -> set[str]:
    """The keys with a dead post that no later post of the same card has superseded."""
    rows = conn.execute(
        f"SELECT DISTINCT json_extract(d.payload, '$.key') FROM jobs d WHERE {_LIVE_DEAD}"  # noqa: S608
    ).fetchall()
    return {str(r[0]) for r in rows}


def _dead_posts(conn: sqlite3.Connection, key: str | None = None) -> dict[str, tuple[int, bool]]:
    """Per key, its unsuperseded dead posts as (how many, refused). Only `chat.postMessage`
    deaths count (a failed edit doesn't stop the card being posted later); refused means Slack
    refused the post or an outage gave it up. A death that names no method (a claim that
    expired, or written before OD-557) counts as a post when the key has no message (it can
    only have been a post), and as refused when its older `slack:<code>` is one of those."""
    sql = (f"SELECT json_extract(d.payload, '$.key') AS k, d.last_error,"  # noqa: S608
           " EXISTS (SELECT 1 FROM slack_messages m WHERE m.key = json_extract(d.payload, '$.key'))"
           f" AS has_message FROM jobs d WHERE {_LIVE_DEAD}")  # fmt: skip
    args: tuple[str, ...] = ()
    if key is not None:
        sql += " AND json_extract(d.payload, '$.key') = ?"
        args = (key,)
    out: dict[str, tuple[int, bool]] = {}
    for r in conn.execute(sql, args):
        method, code = _parse_error(r["last_error"])
        if method != "chat.postMessage" and (method is not None or r["has_message"]):
            continue
        n, was = out.get(str(r["k"]), (0, False))
        out[str(r["k"])] = (n + 1, was or code in REFUSED | OUTAGE)
    return out


def _parse_error(error: str | None) -> tuple[str | None, str | None]:
    """`slack:<method>:<code>` (OD-557), or the older `slack:<code>`, or anything else."""
    if not error or not error.startswith("slack:"):
        return None, None
    method, _, code = error.removeprefix("slack:").rpartition(":")
    return (method or None), code


def refused_keys(conn: sqlite3.Connection) -> set[str]:
    """The cards `post_held_cards` leaves alone: refused (or given up in an outage), or failed
    twice; one automatic re-post after any other give-up (OD-557)."""
    return {k for k, (n, refused) in _dead_posts(conn).items() if refused or n >= 2}


def failed_post_keys(conn: sqlite3.Connection) -> set[str]:
    """The keys whose card failed to post and wasn't posted since (a failed edit leaves the card
    in Slack, so it isn't one)."""
    return set(_dead_posts(conn))


def post_deaths(conn: sqlite3.Connection, key: str) -> int:
    """How many times the key's card failed to post, counting the post that just died."""
    return _dead_posts(conn, key).get(key, (0, False))[0]


def _audit_lost(
    conn: sqlite3.Connection, clock: Clock, key: str, exc: SlackError, reason: str
) -> None:
    """An audit row for an email's or an escalation's card that gave up: the code, the method and
    why, never the text; on the item's history for an email (OD-557)."""
    kind, _, rest = key.partition(":")
    if kind not in ("item", "burst", "rollup"):
        return
    sid = rest if kind == "item" else None
    if sid is not None:
        row = conn.execute("SELECT address_id FROM items WHERE stable_id = ?", (sid,)).fetchone()
        aid = row["address_id"] if row else None
    else:
        aid = rest.split(":", 1)[0] or None
    data = {"code": exc.code, "method": exc.method, "reason": reason}
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                     " VALUES (?, ?, ?, 'slack.post_lost', 'service', 'error', ?)",
                     (to_ts(clock.now()), aid, sid, json.dumps(data)))  # fmt: skip


_NOTICES = {  # the other kinds' notices; {e} is "Email <id>", {s} the id (a digest's address)
    "approval_stepup": "{e} is waiting for your computer: ecf approve {s} or ecf reject {s}. Its"
    " card in Slack wasn't updated.",
    "delayed": "{e} is sending in a few minutes: ecf cancel {s} stops it. Its card in Slack"
    " wasn't updated or posted.",
    "escalation": "An escalation card didn't reach Slack; its emails are in ecf inbox.",
    "digest": "A digest for {s} didn't reach Slack: ecf digest {s} posts the next one; ecf inbox"
    " lists the emails.",
    "needs_you": "The Needs you list didn't reach Slack; ecf inbox shows the same.",
    "other_item": "{e}: its card in Slack is out of date; ecf inbox shows where it stands.",
}
_NO_NOTICE = "A post didn't reach Slack; nothing to do unless it repeats."
_CAUSES = {"refused": "Slack refused its card",
           "cap": "Slack couldn't take its card while other posts went through",
           "quiet": "Slack didn't take its card for 2 hours"}  # fmt: skip


def notice_text(kind: str, short: str, method: str, reason: str, flavour: str,
                deaths: int) -> str:  # fmt: skip
    """The notice for a post that gave up (OD-557): what was lost and what to do first, so a
    desktop notification shows the command; never the email's text."""
    email = f"Email {short}"
    if kind == "approval":
        return _approval_notice(email, short, method, reason, flavour, deaths)
    if kind == "question":
        lost = ("Its card in Slack wasn't updated." if method == "chat.update"
                else "Its question didn't reach Slack.")  # fmt: skip
        return f"{email}: answer it with ecf answer {short}. {lost}"
    return _NOTICES.get(kind, _NO_NOTICE).format(e=email, s=short)


def _approval_notice(email: str, short: str, method: str, reason: str, flavour: str,
                     deaths: int) -> str:  # fmt: skip
    """In this order: an edit; refused, then an outage give-up; a second failure; else once more."""
    decide = f"decide it with ecf approve {short} or ecf reject {short}"
    if method == "chat.update":
        return (f"{email}: {decide}. Its card in Slack wasn't updated and its buttons may not"
                " work; ecf slack repost refreshes it.")  # fmt: skip
    cause = _CAUSES.get("refused" if reason == "refused" else flavour)
    if cause:
        return (f"{email}: {decide}. {cause}; ecf won't post it again by itself; ecf slack repost"
                " tries again.")  # fmt: skip
    if deaths >= 2:
        return (
            f"{email}: {decide}. Its card didn't reach Slack twice; ecf slack repost tries again."
        )
    return (f"{email}: its card didn't reach Slack; ecf posts it once more when it can. If that"
            f" fails too, {decide}.")  # fmt: skip


def dead_posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Posts that gave up (for `ecf doctor`); keys only, no card text."""
    rows = conn.execute(
        "SELECT job_id, payload, last_error FROM jobs WHERE queue = 'slack_out' AND state = 'dead'"
    ).fetchall()
    return [{"job_id": r["job_id"], "key": json.loads(r["payload"]).get("key"),
             "error": r["last_error"]} for r in rows]  # fmt: skip
