"""`ecf backfill <address> --since <date> [--act]` (SPEC §5.1, §10.2; V1.2 step 11b).

ecf's first check starts from now (`start_from: now`). A backfill reads the mail that arrived in
INBOX since a date, up to where that first position was, through the same path as a check: the
same page and time limits, sender authentication, facts, triggers and pre-check decision, and
sender history (a DMARC-passing message counts toward a sender being known).

Records only by default (OD-216): each backfilled item is decided and recorded, then closed as
`observed` through the backfill-only edge `new → observed` (OD-221): nothing is done to the
mailbox, nothing is escalated, and the item never reaches the classifier or "Needs you". With
`--act` the pre-check acts as it would on new mail, within the address's stage (shadow still does
nothing), and escalations post like any others (bursts merge, §10.1).

The backfill runs inside the address's checks, after the page of new mail, while the lease is
held; progress is kept in the settings table (`backfill.<address_id>`), so it survives restarts.
While mail remains, the scheduler treats it like a backlog (catch-up rules: 30 s pauses on AC
power, the time cap and cooldown). A mailbox reset (UIDVALIDITY change) ends it; start it again
afterwards.

v3.0.0 step 5 (OD-507 §3; D5, D6, D8, P1, P7, P10-P13):

- **Ranges:** `--since` and `--until` take the range parser's forms (`ecf.timerange`); the service
  stores UTC times. A bare date (`2026-09-01`, what a v2.2.x client sends) is UTC midnight, as
  before. The range is bounded once, at the first page (`fetch.scan_range`): mail ecf holds is
  skipped and counted (`held`); mail retention deleted is read again.
- **Each scan is a run** (`scans.py`): its items are marked (`facts.backfill`, `facts.scan_run`),
  so none gets a post or card of its own and none counts toward sender history; one Slack summary
  per run.
- **`--act` count, estimate and cap:** the CLI asks for a count first (`request_count`); the
  next check counts the range under the lease (`run_count`): N in range, M already held, K to
  classify, with an estimate (`estimate`). `start` with `act` takes K from that count and refuses
  K over `ACT_CAP` without a step-up bound to {address, since, until, act, K}. Splitting a range
  evades the cap; accepted (cost only).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from ecf import timerange
from ecf.errors import ConflictError, InvalidInputError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import addresses, jobs, leases, precheck, scans, stepup
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.fetch import Backfill, known_uids, load_cursor, scan_range, utc_moment
from ecf_server.items import transition
from ecf_server.mail import MailSource
from ecf_server.state_machine import TransitionContext

KEY = "backfill.{}"
LAST_KEY = "backfill.last.{}"  # the last outcome, for `ecf backfill` to show
COUNT_KEY = "backfill.count.{}"  # an `--act` count asked for, then its answer (P11)
CHECK_TIMEOUT_S = 900  # as schedule.py's check jobs
ACT_CAP = 500  # emails to classify per `--act` scan without a step-up (D6, P11; SPEC §14.3)
ESTIMATE_DAYS = 30
ESTIMATE_MIN_ITEMS = 50  # fewer reviewed here: SPEC §21.2's whole-run figures instead (P1)
# SPEC §21.2, step 6: whole C runs with Opus classifying on schema v3, $12.87 at standard (Sonnet
# actor, about $0.24 less than an Opus one) and $15.14 at high for 304 cards (OD-520, OD-521)
FALLBACK_PER_ITEM = (0.043, 0.050)
FALLBACK_SOURCE = ("SPEC §21.2's whole eval runs with Opus classifying (step 6: $12.87 at standard,"
                   " $15.14 at high, for 304 emails)")  # fmt: skip
# preset D (v3.0.0 step 4, hand-off 4, P20): TypeSafe bills input tokens only (/models, read
# 2026-10-14); without a Jev run here, the live check's 13-question request (SPEC §21.2)
JEV_USD_PER_M = 0.042
JEV_TOKENS_FALLBACK = 2374
JEV_FALLBACK_SOURCE = "the live check's 2,374 input tokens per email (SPEC §21.2)"
CLAUDE_ACTOR_NOTE = ("the Claude actor's share, an estimate: this install's review sessions'"
                     " cost per email, classifier and actor together (not separable)")  # fmt: skip


def _key(address_id: str) -> str:
    return KEY.format(address_id)


def load(conn: sqlite3.Connection, address_id: str) -> Backfill | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (_key(address_id),)).fetchone()
    return Backfill(**json.loads(row[0])) if row else None


def save(conn: sqlite3.Connection, clock: Clock, address_id: str, bf: Backfill) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (_key(address_id), json.dumps(bf.to_json()), now),
        )


def finish(
    conn: sqlite3.Connection, clock: Clock, address_id: str, bf: Backfill, outcome: str
) -> None:
    last = {"since": bf.since, "until": bf.until, "run_id": bf.run_id or None, "act": bf.act,
            "outcome": outcome, "items": _count(conn, address_id),
            "fraud": _count(conn, address_id, fraud=True), "at": to_ts(clock.now())}  # fmt: skip
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (_key(address_id),))
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (LAST_KEY.format(address_id), json.dumps(last), last["at"]),
        )
        _audit(conn, clock, address_id, "backfill.finished", _public(bf) | {"outcome": outcome})
    if bf.run_id:
        scans.finish(conn, clock, bf.run_id, outcome)


def stop(conn: sqlite3.Connection, clock: Clock, ref: str) -> dict[str, Any]:
    """`ecf backfill <address> --stop`: end it now; what was read stays (V1.2 review)."""
    aid = addresses.get_address(conn, ref)["address_id"]
    bf = load(conn, aid)
    if bf is None:
        raise ConflictError(f"no backfill of {aid} is running")
    finish(conn, clock, aid, bf, "stopped")
    return {"address_id": aid, "items": _count(conn, aid)}


def _count(conn: sqlite3.Connection, address_id: str, *, fraud: bool = False) -> int:
    """Backfilled items; with `fraud`, those a fraud trigger fired on, so a records-only backfill
    can't quietly bury a hit (V1.2 review, 2026-09-30)."""
    return int(conn.execute(
        "SELECT count(*) FROM items WHERE address_id = ? AND json_extract(facts, '$.backfill') = 1"
        " AND (NOT ? OR coalesce(json_array_length(facts, '$.triggers.fraud'), 0) > 0)",
        (address_id, int(fraud))).fetchone()[0])  # fmt: skip


def _audit(
    conn: sqlite3.Connection, clock: Clock, address_id: str, event: str, data: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, 'service', 'ok', ?)",
        (to_ts(clock.now()), address_id, event, json.dumps(data)),
    )


def _public(bf: Backfill) -> dict[str, Any]:
    """A backfill's audit data: without its packed UID list."""
    return {k: v for k, v in bf.to_json().items() if k != "uids"}


def resolve_range(clock: Clock, since: str, until: str | None) -> tuple[str, str | None]:
    """The range as stored UTC times. A bare date is UTC midnight (a v2.2.x client's form, P13);
    anything else goes through the range parser, in this computer's zone."""
    now = clock.now()
    if len(since.strip()) == 10 and until is None:
        try:
            day = date.fromisoformat(since.strip())
        except ValueError:
            raise InvalidInputError(f"--since {since!r}: {timerange.HINT}") from None
        if day > now.date():
            raise InvalidInputError("--since is in the future")
        return day.isoformat(), None
    start, end = timerange.resolve(since, until, now=now)
    return timerange.stamp(start), timerange.stamp(end) if end else None


def start(
    conn: sqlite3.Connection,
    clock: Clock,
    ref: str,
    since: str,
    *,
    act: bool,
    until: str | None = None,
    nonce: str | None = None,
    typed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a scan. With `act`, the address's count for this range must be done (`run_count`);
    over `ACT_CAP` emails to classify, a step-up bound to the range and the count."""
    at_since, at_until = resolve_range(clock, since, until)
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    cur = load_cursor(conn, aid)
    if cur is None or cur.uidvalidity is None:
        raise ConflictError(f"{aid} hasn't been checked yet: run `ecf check {aid}` first")
    running = load(conn, aid)
    if running is not None:
        raise ConflictError(f"a backfill of {aid} since {running.since} is already running")
    k = None
    if act:
        k = _counted(conn, aid, at_since, at_until)
        if k > ACT_CAP:
            stepup.consume(conn, clock, "backfill_act", _target(aid, at_since, at_until, k), nonce)
    mode = "acting" if act else "records only"
    with write_tx(conn):
        run_id = scans.new_run(conn, clock, kind="scan", address_id=aid, since=at_since,
                               until=at_until, mode=mode)  # fmt: skip
        bf = Backfill(at_since, act, cur.uidvalidity, cur.last_uid, until=at_until, run_id=run_id)
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')",
            (_key(aid), json.dumps(bf.to_json()), to_ts(clock.now())),
        )
        conn.execute("DELETE FROM settings WHERE key = ?", (COUNT_KEY.format(aid),))  # used (L2)
        _audit(conn, clock, aid, "backfill.started",
               _public(bf) | {"typed": typed or {"since": since, "until": until},
                              "to_classify": k})  # fmt: skip
    queued = conn.execute(
        "SELECT 1 FROM jobs WHERE address_id = ? AND queue = 'fetch'"
        " AND state IN ('queued', 'claimed')",
        (aid,),
    ).fetchone()
    if queued is None:  # start now rather than at the next scheduled check
        jobs.enqueue(conn, clock, jobs.Queue.FETCH, AddressId(aid), {"reason": "backfill"},
                     timeout_s=CHECK_TIMEOUT_S)  # fmt: skip
    return {"address_id": aid, "since": bf.since, "until": at_until, "act": act,
            "run_id": run_id}  # fmt: skip


def _target(aid: str, since: str, until: str | None, k: int) -> dict[str, Any]:
    return {"address_id": aid, "since": since, "until": until, "act": True, "count": k}


@stepup.purpose("backfill_act")
def _describe_act(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to the address, the range, `act` and the service's own count (P11)."""
    aid = str(target.get("address_id", ""))
    since, until = str(target.get("since", "")), target.get("until")
    k = _counted(conn, aid, since, None if until is None else str(until))
    if target.get("count") != k or target.get("act") is not True:
        raise InvalidInputError("the count changed: run ecf backfill --act again")
    email = addresses.get_address(conn, aid)["email"]
    prompt = (f"ecf: classify {k} old emails of {email} with Claude"
              f" ({since[:16]} to {str(until or 'now')[:16]})")  # fmt: skip
    return stepup.Bound(stepup.digest("backfill_act", aid, since, until, True, k), prompt)


# Counting old mail for `ecf backfill --act`: the request, the count run under the address lease
# (in range, minus mail already held), and the number the CLI shows before any classifying.


def request_count(conn: sqlite3.Connection, clock: Clock, ref: str, since: str,
                  until: str | None) -> dict[str, Any]:  # fmt: skip
    """Ask the next check to count the range (P11); the CLI polls `count`."""
    at_since, at_until = resolve_range(clock, since, until)
    aid = addresses.get_address(conn, ref)["address_id"]
    cur = load_cursor(conn, aid)
    if cur is None or cur.uidvalidity is None:
        raise ConflictError(f"{aid} hasn't been checked yet: run `ecf check {aid}` first")
    doc = {"since": at_since, "until": at_until, "state": "queued", "asked_at": to_ts(clock.now())}
    with write_tx(conn):
        _set(conn, clock, COUNT_KEY.format(aid), doc)
    if conn.execute("SELECT 1 FROM jobs WHERE address_id = ? AND queue = 'fetch'"
                    " AND state = 'queued'", (aid,)).fetchone() is None:  # fmt: skip
        jobs.enqueue(conn, clock, jobs.Queue.FETCH, AddressId(aid), {"reason": "backfill count"},
                     timeout_s=CHECK_TIMEOUT_S)  # fmt: skip
    return {"address_id": aid} | doc


def count(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    aid = addresses.get_address(conn, ref)["address_id"]
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (COUNT_KEY.format(aid),)).fetchone()  # fmt: skip
    if row is None:
        raise ConflictError(f"no count asked for {aid}: run ecf backfill --act")
    return {"address_id": aid} | json.loads(row[0])


def run_count(conn: sqlite3.Connection, clock: Clock, src: MailSource, address_id: str,
              lease: leases.Lease) -> bool:  # fmt: skip
    """In a check, under the lease: answer a queued count (P11). True if one ran."""
    key = COUNT_KEY.format(address_id)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    doc: dict[str, Any] = json.loads(row[0]) if row else {}
    cur = load_cursor(conn, address_id)
    if doc.get("state") != "queued" or cur is None or cur.uidvalidity is None:
        return False
    if not leases.held(conn, clock, lease):
        return False
    until = utc_moment(doc["until"]) if doc.get("until") else None
    inrange = scan_range(src, utc_moment(doc["since"]), until, cur.last_uid)
    known = known_uids(conn, address_id, cur.uidvalidity)
    held = sum(1 for u in inrange if u in known)
    k = len(inrange) - held
    doc |= {"state": "done", "in_range": len(inrange), "held": held, "to_classify": k,
            "done_at": to_ts(clock.now()),
            "estimate": estimate(conn, clock.now(), k, addresses.get_address(
                conn, address_id)["preset"])}  # fmt: skip
    with write_tx(conn):
        _set(conn, clock, key, doc)
    return True


def _counted(conn: sqlite3.Connection, aid: str, since: str, until: str | None) -> int:
    """K from the address's done count for exactly this range; refused without one."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (COUNT_KEY.format(aid),)).fetchone()  # fmt: skip
    doc: dict[str, Any] = json.loads(row[0]) if row else {}
    if doc.get("state") != "done" or (doc.get("since"), doc.get("until")) != (since, until):
        raise ConflictError("--act needs the count of this range first: ecf backfill --act"
                            " counts, estimates and asks (an older ecf client can't; update"
                            " it, or run without --act)")  # fmt: skip
    return int(doc["to_classify"])


def estimate(conn: sqlite3.Connection, now: datetime, k: int,
             preset: str = "C") -> dict[str, Any]:  # fmt: skip
    """What classifying `k` emails with Claude may cost (P1): API-equivalent dollars per email
    from this install's `ecf claude` sessions in the last 30 days (every role and the main
    session, cache reads included: the sessions' cost over the emails they reviewed), else
    SPEC §21.2's whole-run figures, named as such; and the session time at this install's median
    pace, else "not measured yet". Not a charge on a plan. Preset D (step 4, P20) adds `jev`:
    TypeSafe's dollars from the latest Jev eval run's billed input tokens per case (else the live
    check's, labelled) and Jev's measured time; the Claude part is then labelled an estimate of
    the actor's share."""
    since = to_ts(now - timedelta(days=ESTIMATE_DAYS))
    # review sessions only: an `/ecf-eval` session reviews no item (`items` counts the items its
    # submissions settled, telemetry.submitted), so its cost isn't spread over them (review L2)
    cost = conn.execute("SELECT sum(c.cost_usd), count(c.cost_usd) FROM claude_calls c JOIN"
                        " claude_sessions s USING (session_id) WHERE c.ts >= ? AND s.items > 0",
                        (since,)).fetchone()  # fmt: skip
    sessions = conn.execute(
        "SELECT started_at, ended_at, items FROM claude_sessions WHERE started_at >= ?",
        (since,)).fetchall()  # fmt: skip
    items = sum(int(r["items"]) for r in sessions)
    out: dict[str, Any] = {"emails": k}
    if items >= ESTIMATE_MIN_ITEMS and cost[1]:
        per = float(cost[0]) / items
        out |= {"usd": round(per * k, 2), "per_email_usd": round(per, 4),
                "source": f"this install's /ecf-review sessions, last {ESTIMATE_DAYS} days"
                          f" ({items} emails)"}  # fmt: skip
    else:
        lo, hi = FALLBACK_PER_ITEM
        out |= {"usd_low": round(lo * k, 2), "usd_high": round(hi * k, 2),
                "source": FALLBACK_SOURCE}  # fmt: skip
    paces = [(from_ts(r["ended_at"]) - from_ts(r["started_at"])).total_seconds() / r["items"]
             for r in sessions if r["ended_at"] and r["items"]]  # fmt: skip
    out["minutes"] = round(statistics.median(paces) * k / 60) if paces else None
    if preset == "D":
        out["claude_note"] = CLAUDE_ACTOR_NOTE
        out["jev"] = jev_estimate(conn, k)
    return out


def jev_estimate(conn: sqlite3.Connection, k: int) -> dict[str, Any]:
    """TypeSafe's part of preset D's estimate (P20): billed input tokens per email from the
    latest Jev eval run here, else the live check's; Jev's median call time from `model_calls`."""
    per, source = float(JEV_TOKENS_FALLBACK), JEV_FALLBACK_SOURCE
    for (raw,) in conn.execute("SELECT metrics FROM eval_runs WHERE pair = 'jev/hosted'"
                               " ORDER BY created_at DESC LIMIT 5"):  # fmt: skip
        m: dict[str, Any] = json.loads(raw)
        figures: dict[str, Any] = m.get("jev") or {}
        billed = figures.get("input_tokens_billed")
        n = m.get("cases")
        if isinstance(billed, int) and isinstance(n, int) and n > 0 and billed > 0:
            per, source = billed / n, f"the latest Jev eval run here ({n} cases)"
            break
    ms = sorted(int(r[0]) for r in conn.execute(
        "SELECT ms FROM model_calls WHERE backend = 'jev' AND role = 'classifier' AND"
        " outcome = 'ok' AND ms IS NOT NULL ORDER BY id DESC LIMIT 500"))  # fmt: skip
    median_s = ms[len(ms) // 2] / 1000 if ms else None
    return {"usd": round(per * k * JEV_USD_PER_M / 1e6, 4), "tokens_per_email": round(per),
            "source": source,
            "minutes": round(median_s * k / 60, 1) if median_s is not None else None}  # fmt: skip


def _set(conn: sqlite3.Connection, clock: Clock, key: str, doc: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(doc), to_ts(clock.now())),
    )


def status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Each address's backfill: running, or the last one's outcome."""
    out: list[dict[str, Any]] = []
    for a in addresses.list_addresses(conn):
        aid = a["address_id"]
        bf = load(conn, aid)
        if bf is not None:
            out.append({"address_id": aid, "since": bf.since, "act": bf.act,
                        "outcome": "running", "items": _count(conn, aid),
                        "fraud": _count(conn, aid, fraud=True)}
                       | ({"until": bf.until, "run_id": bf.run_id, "held": bf.held}
                          if bf.run_id else {}))  # fmt: skip
            continue
        row = conn.execute("SELECT value FROM settings WHERE key = ?",
                           (LAST_KEY.format(aid),)).fetchone()  # fmt: skip
        if row is not None:
            out.append({"address_id": aid} | json.loads(row[0]))
    return out


def undecided(
    conn: sqlite3.Connection, address_id: str, bf: Backfill, skip: list[str]
) -> list[str]:
    """Backfilled mail a failed pass left behind in the backfill's range: created but never
    pre-checked, or pre-checked records-only but not yet closed (V1.2 review, 2026-09-30). New mail
    is above `end_uid`; `--act` items stay `new` after the pre-check by design, and deciding them
    again does nothing (the pre-check skips what it has seen)."""
    rows = conn.execute(
        "SELECT stable_id FROM items WHERE address_id = ? AND status = 'new'"
        " AND (prechecked = 0 OR (? AND json_extract(facts, '$.backfill') = 1))"
        " AND json_extract(locator, '$.uidvalidity') = ?"
        " AND json_extract(locator, '$.uid') <= ?",
        (address_id, int(not bf.act), bf.uidvalidity, bf.end_uid),
    ).fetchall()
    return [r[0] for r in rows if r[0] not in skip]


def decide(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    address_id: str,
    stable_ids: list[str],
    bf: Backfill,
    *,
    install: str,
    max_scan_bytes: int,
) -> list[precheck.Outcome]:
    """The pre-check for backfilled items: as for new mail with --act; otherwise decided,
    recorded and closed as `observed` without acting or escalating."""
    with write_tx(conn):
        for sid in stable_ids:
            conn.execute(
                "UPDATE items SET facts = json_set(facts, '$.backfill', 1) WHERE stable_id = ?",
                (sid,),
            )
            if bf.run_id:  # the run's marker (OD-507 §2); set at creation too
                conn.execute("UPDATE items SET facts = json_set(facts, '$.scan_run', ?)"
                             " WHERE stable_id = ?", (bf.run_id, sid))  # fmt: skip
    if bf.act:
        return precheck.run(conn, clock, src, address_id, stable_ids, install=install,
                            max_scan_bytes=max_scan_bytes)  # fmt: skip
    out = precheck.record_only(conn, clock, stable_ids, "backfill: recorded only (use --act)")
    for sid in stable_ids:
        transition(conn, clock, StableId(sid), Status.OBSERVED,
                   TransitionContext(backfill=True), actor="service")  # fmt: skip
    return out
