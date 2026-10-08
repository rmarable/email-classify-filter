"""The local fallback for the Claude queue (SPEC §4.3; OD-019, OD-020, OD-227; V1.4 step 8).

- **Setting** (`ecf settings set claude_queue_timeout <1-168|off> --address X`; B and C only):
  turning it on, or changing the hours, needs step-up and sends a Security Notice, since it lets
  the local model act on mail meant for Claude; turning it off needs neither (operator decision
  2026-10-02).
- **Shadow runs** (OD-020): while it is on and its own gate hasn't passed, the local model also
  runs on each email of the address from when it was turned on: for C the classifier, then the
  actor when the rule continues to it; for B the actor on the emails Claude acts on. They run in
  the global model queue only when no other local work waits (operator decision 2026-10-02), and
  change nothing: the result is kept in `fallback_shadow` (the model's fields, never the email or
  its reason). A failed shadow run is kept as failed and not tried again.
- **Its own gate** (`gate` row `fallback/B` or `fallback/C`, bound to the Ollama digest; §9.3's
  thresholds): your reviews of the address's emails (of Claude's output) give the truth that the
  shadow classification is scored against, so there is no separate review (operator decision
  2026-10-02): reviewed count, category accuracy and fraud-guard misses; unsafe proposals from the
  shadow actor; the synthetic set from the latest `ecf eval run` on the digest. Nothing in it can
  be waived (operator decision 2026-10-02). Shadow runs stop once it passes, and start again
  when the digest changes.
- **Hand-off:** each tick, an item that has waited longer than the timeout at `awaiting_claude`
  (or answered, at `clarified`) on an address whose fallback gate has passed, and that no Claude
  session holds, is marked `fallback_at`: `/ecf-review` no longer offers it and the local model
  takes it (modelq.WAITING). C: the local model classifies it, then its actor decides; B: the
  local actor decides. Both under `local_high_risk` (actor.decide_one with `local=True`). Its
  classification is recorded under the digest, and the actor's proposal is marked `fallback`, so
  neither counts toward the address's Claude gate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.schema import load_schema
from ecf_server import (
    actor,
    addresses,
    classifier,
    claude_pins,
    config,
    decide,
    evalrun,
    gate,
    ollama,
    policy,
    review,
    slack_admin,
    stages,
    stepup,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.modelq import ItemResult
from ecf_server.notify import Notifier
from ecf_server.ollama import Client, OllamaError
from ecf_server.state_machine import Status

PRESETS = frozenset({"B", "C"})
MIN_H, MAX_H = 1, 168  # §14
PAIR = {"B": "fallback/B", "C": "fallback/C"}  # gate.pair_key of the fallback's own gate


# ---------------------------------------------------------------------------- the setting


def hours(conn: sqlite3.Connection, address_id: str) -> int | None:
    """The address's timeout in hours, or None when the fallback is off."""
    row = conn.execute("SELECT fallback_enabled, claude_queue_timeout_h FROM addresses"
                       " WHERE address_id = ?", (address_id,)).fetchone()  # fmt: skip
    if row is None or not row["fallback_enabled"]:
        return None
    return int(row["claude_queue_timeout_h"])


def parse(value: str) -> int | None:
    v = value.strip().lower()
    if v == "off":
        return None
    try:
        n = int(v)
    except ValueError:
        n = 0
    if not MIN_H <= n <= MAX_H:
        raise InvalidInputError(f"claude_queue_timeout: hours from {MIN_H} to {MAX_H}, or off")
    return n


def _address(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    a = addresses.get_address(conn, ref)
    if a["preset"] not in PRESETS:
        raise InvalidInputError(f"claude_queue_timeout is for preset B and C addresses;"
                                f" {a['address_id']} uses preset {a['preset']}")  # fmt: skip
    return a


@stepup.purpose("claude_queue_timeout")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = _address(conn, str(target.get("address", "")))
    n = parse(str(target.get("value", "")))
    return stepup.Bound(stepup.digest("claude_queue_timeout", a["address_id"], n),
                        f"ecf: let the local model act on {a['email']}'s mail after it has"
                        f" waited {n} h for Claude")  # fmt: skip


def set_timeout(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, ref: str, value: str,
                *, nonce: str | None, actor: str = "os_user") -> dict[str, Any]:  # fmt: skip
    a = _address(conn, ref)
    aid = str(a["address_id"])
    new, old = parse(value), hours(conn, aid)
    if new == old:
        raise ConflictError(f"claude_queue_timeout is {old if old else 'off'} for {aid} already")
    if new is not None:  # on, or a different wait: the local model acts on more mail
        stepup.consume(conn, clock, "claude_queue_timeout",
                       {"address": aid, "value": value.strip().lower()}, nonce)  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        if new is None:
            conn.execute("UPDATE addresses SET fallback_enabled = 0, claude_queue_timeout_h ="
                         " NULL, fallback_since = NULL WHERE address_id = ?", (aid,))  # fmt: skip
            # items handed over but not yet taken go back to Claude
            conn.execute("UPDATE items SET fallback_at = NULL WHERE address_id = ? AND"
                         " status IN ('awaiting_claude', 'clarified')", (aid,))  # fmt: skip
        else:
            conn.execute("UPDATE addresses SET fallback_enabled = 1, claude_queue_timeout_h = ?,"
                         " fallback_since = coalesce(fallback_since, ?) WHERE address_id = ?",
                         (new, now, aid))  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'fallback.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps({"from": old, "to": new})),
        )
    if new is not None:
        ident = slack_admin.identity(conn)
        slack_admin.notice(
            conn, clock, notifier,
            f"Local fallback on for {a['email']}: mail that waits more than {new} h for"
            " /ecf-review goes to the local model (local_high_risk), once the fallback's own gate"
            " passes. Until then the local model runs in shadow on each email.",
            dms=[ident.member] if ident and ident.member else [],
        )  # fmt: skip
    return {"key": "claude_queue_timeout", "value": new if new is not None else "off",
            "address_id": aid, "restart": False}  # fmt: skip


def reminders(conn: sqlite3.Connection) -> list[str]:
    """B and C addresses with the fallback off (`ecf init`, `ecf watch`, `ecf doctor`)."""
    return [str(r[0]) for r in conn.execute(
        "SELECT address_id FROM addresses WHERE removed_at IS NULL AND preset IN ('B', 'C')"
        " AND fallback_enabled = 0 ORDER BY address_id")]  # fmt: skip


def needs_ollama(conn: sqlite3.Connection) -> bool:
    """Any address on C with the fallback on (A and B always use the local model)."""
    return conn.execute("SELECT 1 FROM addresses WHERE removed_at IS NULL AND preset = 'C'"
                        " AND fallback_enabled = 1").fetchone() is not None  # fmt: skip


# ---------------------------------------------------------------------------- its own gate


def digest() -> str:
    """The fallback's gate key: the local model's digest and the schema (OD-475)."""
    return claude_pins.local_key()


def stored(conn: sqlite3.Connection, address_id: str, preset: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM gate WHERE address_id = ? AND pair_key = ?",
                        (address_id, PAIR[preset])).fetchone()  # fmt: skip


def passed(conn: sqlite3.Connection, address_id: str, preset: str, d: str | None = None) -> bool:
    row = stored(conn, address_id, preset)
    return bool(row and row["passed_at"] and row["ollama_digest"] == (d or digest()))


def compute(conn: sqlite3.Connection, address_id: str) -> gate.Gate:
    a = conn.execute("SELECT sensitivity, preset FROM addresses WHERE address_id = ?",
                     (address_id,)).fetchone()  # fmt: skip
    sens, preset, d = str(a["sensitivity"]), str(a["preset"]), digest()
    rows = conn.execute(
        "SELECT i.*, s.classification AS s_cls, s.plan AS s_plan FROM items i"
        " JOIN fallback_shadow s USING (stable_id) WHERE i.address_id = ? AND s.digest = ?"
        " AND s.outcome = 'ok'", (address_id, d)).fetchall()  # fmt: skip
    labels = policy.labels(load_schema(), config.current_rules(conn))
    n = ok = misses = unsafe = 0
    for r in rows:
        unsafe += gate.unsafe(json.loads(r["s_plan"] or "{}"), fallback=True)
        rv: dict[str, Any] = json.loads(r["review"] or "{}")
        if rv.get("verdict") not in ("correct", "fixed") or r["classification"] is None:
            continue
        truth: dict[str, Any] = json.loads(r["classification"])
        if rv["verdict"] == "fixed" and r["human_correction"]:
            truth |= json.loads(r["human_correction"])
        shadow: dict[str, Any] = json.loads(r["s_cls"] or "{}")
        n += 1
        ok += shadow.get("category") == truth.get("category")
        ctx = decide.context(conn, r)
        right = policy.plan(replace(ctx, classification=truth), labels).rule_id
        mine = policy.plan(replace(ctx, classification=shadow), labels).rule_id
        misses += right in gate.FRAUD_RULES and mine not in gate.FRAUD_RULES
    target, threshold = review.GATE_COUNT[sens], gate.THRESHOLD[sens]
    acc = 100 * ok / n if n else 0.0
    checks = (
        gate.Check("reviewed", n >= target,
                   f"{n}/{target} reviewed emails the local model also ran on"),
        gate.Check("accuracy", n > 0 and acc >= threshold,
                   f"{acc:.0f}% accurate (need {threshold}%)" if n
                   else f"no reviews yet (need {threshold}% accurate)"),
        gate.Check("fraud_misses", misses == 0,
                   f"{misses} fraud-guard miss(es) among reviewed emails"),
        gate.Check("unsafe_proposals", unsafe == 0,
                   f"{unsafe} unsafe proposal(s) on payment or fraud emails"),
        gate.synthetic(conn, d, "A"),
    )  # fmt: skip
    return gate.Gate(address_id, sens, d, checks, n, ok, misses, unsafe, preset,
                     (("digest", d),))  # fmt: skip


def inputs(conn: sqlite3.Connection, address_id: str, d: str) -> tuple[Any, ...]:
    """What `compute` reads, cheaply: the tick recomputes only when it changes."""
    shadow = conn.execute(
        "SELECT count(*), max(s.created_at), max(i.updated_at) FROM fallback_shadow s"
        " JOIN items i USING (stable_id) WHERE s.address_id = ? AND s.digest = ?",
        (address_id, d)).fetchone()  # fmt: skip
    cfg = conn.execute("SELECT max(updated_at) FROM settings WHERE key LIKE 'config.%'"
                       " OR key = 'org_domains'").fetchone()  # fmt: skip
    run = conn.execute("SELECT max(created_at) FROM eval_runs WHERE digest = ?", (d,)).fetchone()
    root = slack_admin.setting(conn, evalrun.EVAL_ROOT)
    try:
        st = (Path(root) / "labels.jsonl").stat() if root else None
        labels = (st.st_size, st.st_mtime_ns) if st else None
    except OSError:
        labels = None
    return (d, tuple(shadow), cfg[0], run[0], root, labels)


SEEN: dict[str, tuple[Any, ...]] = {}  # address -> the inputs last computed (this process)


def tick(conn: sqlite3.Connection, clock: Clock) -> None:
    """Record and announce the fallback's gate once it is met for the digest."""
    d = digest()
    for aid, preset in _on(conn):
        if passed(conn, aid, preset, d):
            continue
        seen = inputs(conn, aid, d)
        if SEEN.get(aid) == seen:
            continue
        g = compute(conn, aid)
        SEEN[aid] = seen
        if g.met:
            with write_tx(conn):
                gate.record(conn, g, clock.now(), passed=True, pair=PAIR[preset])
            stages.post(conn, clock, aid, "Local fallback ready",
                        f"{aid}'s local fallback meets its own gate ({g.reviewed} reviewed):"
                        " mail that waits longer than claude_queue_timeout now goes to the local"
                        " model. Shadow runs stop.")  # fmt: skip


def status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per B and C address: the timeout, and the fallback's gate (`ecf doctor`, `ecf status`)."""
    out: list[dict[str, Any]] = []
    for a in addresses.list_addresses(conn):
        if a["preset"] not in PRESETS:
            continue
        aid = str(a["address_id"])
        h = hours(conn, aid)
        row: dict[str, Any] = {"address_id": aid, "hours": h}
        if h is not None:
            ok = passed(conn, aid, str(a["preset"]))
            row |= {"gate_passed": ok, "gate": "met" if ok else compute(conn, aid).text(),
                    "handed_off": handed_off(conn, aid)}  # fmt: skip
        out.append(row)
    return out


def _on(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in conn.execute(
        "SELECT address_id, preset FROM addresses WHERE removed_at IS NULL AND"
        " preset IN ('B', 'C') AND fallback_enabled = 1 ORDER BY address_id")]  # fmt: skip


# ---------------------------------------------------------------------------- the hand-off


def hand_off(conn: sqlite3.Connection, clock: Clock) -> int:
    """Each tick: hand items that waited too long to the local model; returns how many."""
    now = clock.now()
    d = digest()
    moved = 0
    for aid, preset in _on(conn):
        h = hours(conn, aid)
        if h is None or not passed(conn, aid, preset, d):
            continue
        cutoff, ts = to_ts(now - timedelta(hours=h)), to_ts(now)
        with write_tx(conn):  # with review_queue's claims in one transaction: never both
            sids = [str(r[0]) for r in conn.execute(
                "SELECT stable_id FROM items i WHERE address_id = ? AND fallback_at IS NULL"
                " AND model_failed = 0"
                " AND status IN ('awaiting_claude', 'clarified') AND claude_since < ?"
                " AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.stable_id = i.stable_id"
                " AND (c.state = 'held' OR (c.state = 'claimed' AND c.expires_at > ?)))",
                (aid, cutoff, ts))]  # fmt: skip
            for sid in sids:
                conn.execute("UPDATE items SET fallback_at = ?, updated_at = ? WHERE"
                             " stable_id = ?", (ts, ts, sid))  # fmt: skip
                conn.execute(
                    "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                    " VALUES (?, ?, ?, 'fallback.handed_off', 'service', 'ok', ?)",
                    (ts, aid, sid, json.dumps({"hours": h})),
                )
        moved += len(sids)
    if moved:
        log.info("fallback.handed_off", items=moved)
    return moved


def handed_off(conn: sqlite3.Connection, address_id: str, since: str | None = None) -> int:
    return int(conn.execute(
        "SELECT count(*) FROM audit WHERE event = 'fallback.handed_off' AND address_id = ?"
        " AND ts >= ?", (address_id, since or "")).fetchone()[0])  # fmt: skip


# ---------------------------------------------------------------------------- shadow runs

# emails the fallback runs on in shadow, on one address whose fallback gate hasn't passed (the
# caller picks the addresses): from when it was turned on, not handed off, not yet run, text kept
SHADOW = (
    "SELECT i.* FROM items i JOIN addresses a USING (address_id) WHERE i.address_id = ?"
    " AND i.created_at >= a.fallback_since AND i.fallback_at IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM fallback_shadow s WHERE s.stable_id = i.stable_id)"
    " AND EXISTS (SELECT 1 FROM excerpts e WHERE e.stable_id = i.stable_id"
    " AND e.classifier_text IS NOT NULL)"
    " AND coalesce(json_extract(i.facts, '$.precheck.stage'), '') <> 'backfill' AND ("
    "(a.preset = 'C' AND i.prechecked = 1 AND i.status <> 'new')"
    " OR (a.preset = 'B' AND i.classification IS NOT NULL AND (i.status = 'awaiting_claude'"
    " OR json_extract(i.proposal, '$.plan.actor.agent') IS NOT NULL)))"
)


def shadow_addresses(conn: sqlite3.Connection) -> list[str]:
    """Addresses with shadow work waiting (not paused), oldest work first."""
    d = digest()
    out: list[str] = []
    for aid, preset in _on(conn):
        paused = conn.execute("SELECT paused FROM addresses WHERE address_id = ?",
                              (aid,)).fetchone()["paused"]  # fmt: skip
        if paused or passed(conn, aid, preset, d):
            continue
        if conn.execute(SHADOW + " LIMIT 1", (aid,)).fetchone() is not None:
            out.append(aid)
    return out


def next_shadow(conn: sqlite3.Connection, address_id: str,
                tried: set[str]) -> sqlite3.Row | None:  # fmt: skip
    marks = ",".join("?" * len(tried))
    skip = f" AND i.stable_id NOT IN ({marks})" if tried else ""
    row: sqlite3.Row | None = conn.execute(
        SHADOW + skip + " ORDER BY i.created_at, i.stable_id LIMIT 1",
        (address_id, *sorted(tried))).fetchone()  # fmt: skip
    return row


def shadow_item(conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
                item: sqlite3.Row) -> ItemResult:  # fmt: skip
    """The model queue's shadow `Work`: run the fallback on one email; nothing in the mailbox or
    on the item changes."""
    a = conn.execute("SELECT preset, stage FROM addresses WHERE address_id = ?",
                     (item["address_id"],)).fetchone()  # fmt: skip
    tags = {"address_id": item["address_id"], "preset": a["preset"], "stage": a["stage"]}
    sid = str(item["stable_id"])
    schema = load_schema()
    ex = conn.execute("SELECT classifier_text, actor_text FROM excerpts WHERE stable_id = ?",
                      (sid,)).fetchone()  # fmt: skip
    metrics = None
    try:
        if a["preset"] == "C":
            reply = classifier.ask(client, classifier.fit(str(ex["classifier_text"] or "")), schema)
            metrics = reply.metrics
            cls = None if classifier.truncated(reply) else classifier.parse(reply.content, schema)
            if cls is None:
                return _failed(conn, clock, ready, item, tags, reply.metrics, "schema_failure")
        else:
            cls = json.loads(item["classification"])
        ctx = decide.context(conn, item, cls)
        labels = policy.labels(schema, ctx.rules)
        p = policy.plan(ctx, labels)
        proposed: dict[str, Any] | None = None
        if p.to_actor:
            reply = actor.ask(client, str(ex["actor_text"] or ""), cls, [], labels,
                              ctx.move_folders)  # fmt: skip
            metrics = reply.metrics
            n = reply.metrics.prompt_tokens
            got = None if n is not None and n >= actor.NEAR_CTX else actor.parse(
                reply.content, labels, ctx.move_folders, actor.allowed(cls))  # fmt: skip
            if got is None:
                return _failed(conn, clock, ready, item, tags, reply.metrics, "schema_failure")
            proposed = {"action": got["action"], "target": got["target"] or None,
                        "fallback": True}  # fmt: skip
    except OllamaError as e:
        if e.cause not in ("timeout", "http"):
            raise  # the server itself: the round ends, nothing is recorded for the email
        return _failed(conn, clock, ready, item, tags, None,
                       "timeout" if e.cause == "timeout" else "error")  # fmt: skip
    ollama.record_call(conn, clock, role="fallback_shadow", outcome="ok", digest=ready.digest,
                       metrics=metrics, **tags)  # fmt: skip
    plan = {"rule": p.rule_id, "to_actor": p.to_actor, "high_risk": p.high_risk,
            "payment_or_fraud": p.payment_or_fraud, "actor": proposed}  # fmt: skip
    _store(conn, clock, item, digest(), "ok", cls, plan)
    return ItemResult("ok", metrics)


def _failed(conn: sqlite3.Connection, clock: Clock, ready: ollama.Ready, item: sqlite3.Row,
            tags: dict[str, Any], metrics: ollama.Metrics | None,
            outcome: ollama.Outcome) -> ItemResult:  # fmt: skip
    ollama.record_call(conn, clock, role="fallback_shadow", outcome=outcome, digest=ready.digest,
                       metrics=metrics, **tags)  # fmt: skip
    _store(conn, clock, item, digest(), "failed", None, None)
    return ItemResult("failed", metrics)


def _store(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, d: str, outcome: str,
           cls: dict[str, Any] | None, plan: dict[str, Any] | None) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO fallback_shadow (stable_id, address_id, digest, outcome,"
            " classification, plan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item["stable_id"], item["address_id"], d, outcome,
             json.dumps(cls, sort_keys=True) if cls is not None else None,
             json.dumps(plan, sort_keys=True) if plan is not None else None,
             to_ts(clock.now())),
        )  # fmt: skip


# ---------------------------------------------------------------------------- handed-off work


def classify(conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
             item: sqlite3.Row) -> ItemResult:  # fmt: skip
    """A C item handed off at `awaiting_claude`: the local classifier, recorded under the digest
    (so it counts toward neither gate)."""
    return classifier.classify_item(conn, clock, client, ready, item,
                                    expected=Status.AWAITING_CLAUDE,
                                    pins={"pin_key": digest(), "fallback": True})  # fmt: skip
