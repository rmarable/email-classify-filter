"""`ecf reprocess <address> --since … --until … [--rules-only] [--force] [--dry-run]` (SPEC §10.2;
OD-507, plan v3.0.0-STEP5 §4; D9-D11, P3, P5, P8, P14-P16).

Mail ecf holds is decided again after its classifier, prompt, schema or rules changed. The key
(`classifier.store` writes it to `pinned_models`): `provider`, `classifier` (model id), `prompt`,
`schema` and `rules` (`rules.composed_digest`). Outside it, stated: the actor's pins, the action
policy, move folders, sensitivity, templates and forwards (`--force` covers them); no ecf version.

- **Eligible:** status `observed` or `executed` (an allow-list), not reviewed or corrected
  (`items.review`), and no fraud, weak fraud or regulator trigger (D11); an executed item on an
  address now in shadow is skipped too (planning it again would end it `observed`, losing its
  Undo), and one with an Undo queued. Everything else is skipped and counted by reason.
- **The actor's items** (review H2): an item the actor decided goes back through the actor (it
  is classified again) when only the rules changed; `--rules-only` skips it ("actor decided;
  needs a model").
- **Per item, the minimum (P5):** classified again when `classifier`, `prompt` or `schema`
  differ (or are missing: an item from before v3.0.0 step 5); planned again on its stored
  classification when only `rules` differs. `--rules-only` never calls a model: it plans again
  where `rules` differs (missing only with `--force`), and skips items with no classification.
  `--force`: every eligible item, classified again (`--rules-only --force`: every classified one
  planned again).
- **Guards:** the dry run (and the CLI's first screen) counts what would be classified again,
  planned again and could touch the mailbox, and what is skipped; a step-up when more than `CAP`
  would be classified again or could touch the mailbox, bound to the address, range, options and
  both counts.
- **A service job (P8):** each tick handles one batch of at most `BATCH` items: their marker
  (`facts.reprocess`; for an executed item `facts.reprocess_from`, and `facts.reprocess_moved`
  when ecf moved its message) and an `item.reprocessed` audit row (old and new key) in one
  transaction, then each item's move (`awaiting_claude` for Claude, or `proposed` and the policy
  with no actor, then its rules digest). A move that fails leaves an unmoved item unmarked, to be
  taken again, and its id in `--status` (review M6). No IMAP or model call; progress in
  `settings` (`reprocess.<address>`), so `--status` and `--stop` work.
- **Execution** (decide.py, mailbox_actions.py; §4.3): marked items are old mail (`marked.py`):
  every hide needs approval, no card, one summary per run. An executed item's message moved by
  ecf gets no mailbox action; one still in INBOX gets only what the new plan adds.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ecf.errors import ConflictError
from ecf.status import Status
from ecf_server import (
    addresses,
    backfill,
    classifier,
    claude_pins,
    config,
    decide,
    items,
    jev_keys,
    scans,
    stepup,
)
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.fetch import utc_moment
from ecf_server.log_bridge import log
from ecf_server.state_machine import Stage, TransitionContext

KEY = "reprocess.{}"
LAST_KEY = "reprocess.last.{}"
BATCH = 50  # items per transaction and per tick (P8)
LOOK = 500  # items read per tick to find a batch
CAP = 500  # classified again, or touching the mailbox, without a step-up (plan §4.2)
ELIGIBLE = (Status.OBSERVED.value, Status.EXECUTED.value)
CLASSIFY_KEYS = ("classifier", "prompt", "schema")
FAILED_KEPT = 50  # failed items' short ids kept for `--status`
_MOVES = frozenset({"archive", "move", "junk"})


def current_key(conn: sqlite3.Connection, address_id: str) -> dict[str, Any]:
    """The key an item classified now would get: Claude's for C; for D (v3.0.0 step 4, S4-D16)
    Jev's pin and the prompt digest `jev_dispatch` stored."""
    schema = config.current_schema(conn)
    a = conn.execute("SELECT sensitivity, preset FROM addresses WHERE address_id = ?",
                     (address_id,)).fetchone()  # fmt: skip
    if a["preset"] == jev_keys.PRESET:
        return {"provider": "jev", "classifier": jev_keys.pin(),
                "prompt": classifier.jev_prompt(conn, schema), "schema": schema.digest,
                "rules": config.current_rules(conn).digest}  # fmt: skip
    sens = a["sensitivity"]
    role = "classifier_high" if sens == "high" else "classifier"
    return {"provider": "claude", "classifier": claude_pins.effective(conn)[role],
            "prompt": classifier.prompt_digest(schema, "claude"), "schema": schema.digest,
            "rules": config.current_rules(conn).digest}  # fmt: skip


def _skip(r: sqlite3.Row, stage: str) -> str | None:
    if r["status"] not in ELIGIBLE:
        return f"status {r['status']}"
    if r["review"]:
        return "reviewed or corrected"
    t: dict[str, Any] = json.loads(r["facts"] or "{}").get("triggers") or {}
    if t.get("fraud") or t.get("fraud_weak") or t.get("regulator"):
        return "fraud or regulator trigger"
    if r["status"] == Status.EXECUTED and stage == Stage.SHADOW:
        return "executed, address now in shadow"
    if json.loads(r["proposal"] or "{}").get("undo") == "queued":
        return "undo queued"
    return None


def _actor_decided(r: sqlite3.Row) -> bool:
    """The actor (a model) decided this item: planning it again by the rules alone would drop
    that decision (review H2)."""
    plan: dict[str, Any] = json.loads(r["proposal"] or "{}").get("plan") or {}
    return r["decision_source"] == "actor" or bool(plan.get("to_actor"))


def mode_of(r: sqlite3.Row, key: dict[str, Any], *, rules_only: bool,
            force: bool) -> tuple[str | None, str | None]:  # fmt: skip
    """(`reclassify`, `replan` or None, why it is skipped or None) for an eligible item."""
    old: dict[str, Any] = json.loads(r["pinned_models"] or "{}")
    if rules_only:
        if r["classification"] is None:
            return None, "not classified"
        if force or ("rules" in old and old["rules"] != key["rules"]):
            return (None, "actor decided; needs a model") if _actor_decided(r) else ("replan", None)
        return None, None
    if force or any(old.get(k) != key[k] for k in CLASSIFY_KEYS) or r["classification"] is None:
        return "reclassify", None
    if old.get("rules") != key["rules"]:  # an actor's item goes back through the actor (H2)
        return ("reclassify" if _actor_decided(r) else "replan"), None
    return None, None


def _in_range(r: sqlite3.Row, since: datetime, until: datetime | None) -> bool:
    loc: dict[str, Any] = json.loads(r["locator"] or "{}")
    when = datetime.fromisoformat(loc["internaldate"]) if loc.get("internaldate") else (
        from_ts(r["created_at"]))  # fmt: skip
    return since <= when and (until is None or when <= until)


def _done(r: sqlite3.Row) -> list[dict[str, Any]]:
    doc: dict[str, Any] = json.loads(r["proposal"] or "{}")
    done: list[dict[str, Any]] = doc.get("done") or []
    return done


def _moved(r: sqlite3.Row) -> bool:
    return any(d.get("name") in _MOVES for d in _done(r))


def _touches(conn: sqlite3.Connection, r: sqlite3.Row, mode: str) -> bool:
    """Whether this item could touch the mailbox: a re-plan whose new plan adds a mailbox action
    to a message still in INBOX; any item classified again whose message is in INBOX (an upper
    bound: its new classification isn't known yet)."""
    if r["status"] == Status.EXECUTED and _moved(r):
        return False
    if mode == "reclassify":
        return True
    _ctx, p = decide.plan_for(conn, r)
    had = decide.done_keys(_done(r))
    return any(decide.done_key(a.name, a.target) not in had
               for a in p.actions if a.name in decide.MAILBOX)  # fmt: skip


def survey(conn: sqlite3.Connection, aid: str, since: datetime, until: datetime | None, *,
           rules_only: bool, force: bool) -> dict[str, Any]:  # fmt: skip
    """The dry run's counts (plan §4.2): nothing is written."""
    key = current_key(conn, aid)
    stage = conn.execute("SELECT stage FROM addresses WHERE address_id = ?",
                         (aid,)).fetchone()["stage"]  # fmt: skip
    out: dict[str, Any] = {"in_range": 0, "reclassify": 0, "replan": 0, "unchanged": 0,
                           "touch": 0, "skipped": {}}  # fmt: skip
    for r in conn.execute("SELECT * FROM items WHERE address_id = ? ORDER BY created_at,"
                          " stable_id", (aid,)):  # fmt: skip
        if not _in_range(r, since, until):
            continue
        out["in_range"] += 1
        why = _skip(r, stage)
        mode = None
        if why is None:
            mode, why = mode_of(r, key, rules_only=rules_only, force=force)
        if why is not None:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
        elif mode is None:
            out["unchanged"] += 1
        else:
            out[mode] += 1
            out["touch"] += _touches(conn, r, mode)
    return out


def _range(clock: Clock, since: str, until: str | None) -> tuple[str, str | None]:
    return backfill.resolve_range(clock, since, until)


def dry_run(conn: sqlite3.Connection, clock: Clock, ref: str, since: str, until: str | None, *,
            rules_only: bool, force: bool) -> dict[str, Any]:  # fmt: skip
    aid = addresses.get_address(conn, ref)["address_id"]
    s, u = _range(clock, since, until)
    return {"address_id": aid, "since": s, "until": u} | survey(
        conn, aid, utc_moment(s), utc_moment(u) if u else None, rules_only=rules_only,
        force=force)  # fmt: skip


def _target(aid: str, s: str, u: str | None, rules_only: bool, force: bool,
            n: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    return {"address_id": aid, "since": s, "until": u, "rules_only": rules_only, "force": force,
            "reclassify": n["reclassify"], "touch": n["touch"]}  # fmt: skip


@stepup.purpose("reprocess")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to the address, the range, the options and the service's own counts."""
    aid = str(target.get("address_id", ""))
    s, u = str(target.get("since", "")), target.get("until")
    rules_only, force = target.get("rules_only") is True, target.get("force") is True
    n = survey(conn, aid, utc_moment(s), utc_moment(str(u)) if u else None,
               rules_only=rules_only, force=force)  # fmt: skip
    if (n["reclassify"], n["touch"]) != (target.get("reclassify"), target.get("touch")):
        from ecf.errors import InvalidInputError  # noqa: PLC0415

        raise InvalidInputError("the counts changed: run ecf reprocess again")
    email = addresses.get_address(conn, aid)["email"]
    prompt = (
        f"ecf: decide {n['reclassify'] + n['replan']} old emails of {email} again"
        f" ({n['reclassify']} with Claude, {n['touch']} could change the mailbox)"
    )
    return stepup.Bound(stepup.digest("reprocess", aid, s, u, rules_only, force,
                                      n["reclassify"], n["touch"]), prompt)  # fmt: skip


def start(  # noqa: PLR0913 - the request's parts
        conn: sqlite3.Connection, clock: Clock, ref: str, since: str, until: str | None, *,
          rules_only: bool, force: bool, nonce: str | None = None,
          typed: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    aid = addresses.get_address(conn, ref)["address_id"]
    if load(conn, aid) is not None:
        raise ConflictError(f"a reprocess of {aid} is already running: ecf reprocess --status")
    s, u = _range(clock, since, until)
    n = survey(conn, aid, utc_moment(s), utc_moment(u) if u else None, rules_only=rules_only,
               force=force)  # fmt: skip
    if n["reclassify"] > CAP or n["touch"] > CAP:
        stepup.consume(conn, clock, "reprocess", _target(aid, s, u, rules_only, force, n), nonce)
    mode = "rules only" + (", forced" if force else "") if rules_only else (
        "forced" if force else "changed items")  # fmt: skip
    with write_tx(conn):
        run_id = scans.new_run(conn, clock, kind="reprocess", address_id=aid, since=s, until=u,
                               mode=mode)  # fmt: skip
        state: dict[str, Any] = {"run_id": run_id, "since": s, "until": u, "rules_only": rules_only,
                 "force": force, "after": ["", ""], "reclassify": 0, "replan": 0,
                 "skipped": {}, "errors": 0}  # fmt: skip
        _put(conn, clock, KEY.format(aid), state)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'reprocess.started', 'os_user', 'ok', ?)",
            (to_ts(clock.now()), aid, json.dumps({k: v for k, v in state.items() if k != "after"}
                                                 | {"typed": typed or {}, "survey": n})),
        )  # fmt: skip
    return {"address_id": aid, "run_id": run_id, "since": s, "until": u} | n


def load(conn: sqlite3.Connection, aid: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY.format(aid),)).fetchone()
    return json.loads(row[0]) if row else None


def _put(conn: sqlite3.Connection, clock: Clock, key: str, doc: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(doc), to_ts(clock.now())),
    )


def tick(conn: sqlite3.Connection, clock: Clock) -> int:
    """One batch for each running reprocess, then the open runs' summary lines; returns how many
    items moved."""
    moved = 0
    for (key,) in conn.execute("SELECT key FROM settings WHERE key LIKE 'reprocess.%'"
                               " AND key NOT LIKE 'reprocess.last.%'").fetchall():  # fmt: skip
        moved += _batch(conn, clock, str(key).removeprefix("reprocess."))
    for doc in scans.runs(conn):
        if doc["kind"] == "reprocess" and not doc["settled"]:
            lines = summary_lines(conn, doc["run_id"])
            if lines != doc["lines"]:  # written only on change (review L6)
                scans.note(conn, clock, doc["run_id"], lines)
    return moved


def _batch(conn: sqlite3.Connection, clock: Clock, aid: str) -> int:
    state = load(conn, aid)
    a = conn.execute("SELECT stage FROM addresses WHERE address_id = ? AND removed_at IS NULL",
                     (aid,)).fetchone()  # fmt: skip
    if state is None:
        return 0
    if a is None:
        _finish(conn, clock, aid, state, "address removed")
        return 0
    key = current_key(conn, aid)
    since = utc_moment(state["since"])
    until = utc_moment(state["until"]) if state["until"] else None
    after: list[str] = state["after"]
    rows = conn.execute(
        "SELECT * FROM items WHERE address_id = ? AND (created_at > ? OR (created_at = ?"
        " AND stable_id > ?)) ORDER BY created_at, stable_id LIMIT ?",
        (aid, after[0], after[0], after[1], LOOK)).fetchall()  # fmt: skip
    if not rows:
        _finish(conn, clock, aid, state, "done")
        return 0
    picked: list[tuple[sqlite3.Row, str]] = []
    for r in rows:
        after = [r["created_at"], r["stable_id"]]
        if not _in_range(r, since, until):
            continue
        why = _skip(r, a["stage"])
        mode = None
        if why is None:
            mode, why = mode_of(r, key, rules_only=state["rules_only"], force=state["force"])
        if why is not None:
            state["skipped"][why] = state["skipped"].get(why, 0) + 1
        elif mode is not None:
            picked.append((r, mode))
            if len(picked) == BATCH:
                break
    _mark(conn, clock, aid, state["run_id"], picked, key)
    for r, mode in picked:
        try:
            _move(conn, clock, r, mode, key)
            state[mode] += 1
        except Exception as exc:  # one item never stops the run (review M6)
            state["errors"] += 1
            failed: list[str] = state.setdefault("failed", [])
            if len(failed) < FAILED_KEPT:
                failed.append(r["stable_id"][:8])
            _unmark(conn, r)
            log.warning("reprocess.item_failed", stable_id=r["stable_id"][:8],
                        error_type=type(exc).__name__)  # fmt: skip
    state["after"] = after
    with write_tx(conn):
        _put(conn, clock, KEY.format(aid), state)
    return len(picked)


def _mark(conn: sqlite3.Connection, clock: Clock, aid: str, run_id: str,
          picked: list[tuple[sqlite3.Row, str]], key: dict[str, Any]) -> None:  # fmt: skip
    """The batch's marker, a re-plan's rules digest and `item.reprocessed` rows, in one
    transaction (no IMAP or model call inside; P8)."""
    if not picked:
        return
    now = to_ts(clock.now())
    with write_tx(conn):
        for r, mode in picked:
            old: dict[str, Any] = json.loads(r["pinned_models"] or "{}")
            conn.execute("UPDATE items SET facts = json_set(facts, '$.reprocess', ?)"
                         " WHERE stable_id = ?", (run_id, r["stable_id"]))  # fmt: skip
            if r["status"] == Status.EXECUTED:  # only what the new plan adds runs (decide.py)
                conn.execute("UPDATE items SET facts = json_set(facts, '$.reprocess_from',"
                             " 'executed') WHERE stable_id = ?", (r["stable_id"],))  # fmt: skip
                if _moved(r):  # ecf moved its message: `close_gone` leaves it alone (H1)
                    conn.execute("UPDATE items SET facts = json_set(facts, '$.reprocess_moved',"
                                 " 1) WHERE stable_id = ?", (r["stable_id"],))  # fmt: skip
            conn.execute(
                "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, 'item.reprocessed', 'service', 'ok', ?)",
                (now, aid, r["stable_id"], json.dumps({
                    "run": run_id, "mode": mode, "from": r["status"],
                    "old": _short(old), "new": _short(key)}, sort_keys=True)))  # fmt: skip


def _short(key: dict[str, Any]) -> dict[str, Any]:
    """A key for the audit log: model ids and 16-hex digest prefixes (as `item.classified`)."""
    out: dict[str, Any] = {k: key.get(k) for k in ("provider", "classifier")}
    for k in ("prompt", "schema", "rules"):
        v = key.get(k)
        out[k] = str(v).removeprefix("schema-")[:16] if v else None
    return out


def _move(conn: sqlite3.Connection, clock: Clock, r: sqlite3.Row, mode: str,
          key: dict[str, Any]) -> None:  # fmt: skip
    sid = r["stable_id"]
    ctx = TransitionContext(reprocess=True)
    frm = Status(r["status"])
    if mode == "reclassify":  # its whole key is written when the classification is stored
        # the old classification goes in the same write, or `/ecf-review` would only offer the
        # item to the actor (`claude_review.need_of`); its key is in the `item.reprocessed` audit
        items.transition(conn, clock, sid, Status.AWAITING_CLAUDE, ctx, actor="service",
                         expected=frm, clear_classification=True)  # fmt: skip
        preset = conn.execute("SELECT preset FROM addresses WHERE address_id = ?",
                              (r["address_id"],)).fetchone()[0]  # fmt: skip
        if preset == jev_keys.PRESET:
            # D: Jev's drain takes only an unclassified item, after new mail (S4-D16, P12)
            with write_tx(conn):
                # and Jev's old caution (`facts.jev`), read with no classification otherwise
                conn.execute("UPDATE items SET facts = json_remove(facts, '$.jev_attempts',"
                             " '$.jev') WHERE stable_id = ?", (sid,))  # fmt: skip
            jev_keys.WAKE.set()
        return
    items.transition(conn, clock, sid, Status.PROPOSED, ctx, actor="service", expected=frm)
    decide.apply(conn, clock, sid, actor=False)  # planned again; never the actor (no model)
    with write_tx(conn):  # the rules it was planned under: only once that worked (review M6)
        conn.execute("UPDATE items SET pinned_models = json_set(coalesce(pinned_models, '{}'),"
                     " '$.rules', ?) WHERE stable_id = ?", (key["rules"], sid))  # fmt: skip


def _unmark(conn: sqlite3.Connection, r: sqlite3.Row) -> None:
    """A move that failed: an item still where it was loses the marker, so a later run takes it
    again; one that moved keeps it (it is old mail; review M6)."""
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = json_remove(facts, '$.reprocess',"
                     " '$.reprocess_from', '$.reprocess_moved') WHERE stable_id = ?"
                     " AND status = ?", (r["stable_id"], r["status"]))  # fmt: skip


def _finish(conn: sqlite3.Connection, clock: Clock, aid: str, state: dict[str, Any],
            outcome: str) -> None:  # fmt: skip
    last = {k: v for k, v in state.items() if k != "after"} | {
        "outcome": outcome, "at": to_ts(clock.now())}  # fmt: skip
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (KEY.format(aid),))
        _put(conn, clock, LAST_KEY.format(aid), last)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'reprocess.finished', 'service', 'ok', ?)",
            (to_ts(clock.now()), aid, json.dumps(last)),
        )
    scans.finish(conn, clock, state["run_id"], outcome, summary_lines(conn, state["run_id"]))


def stop(conn: sqlite3.Connection, clock: Clock, ref: str) -> dict[str, Any]:
    """`ecf reprocess <address> --stop`: no more batches; items already moved go on."""
    aid = addresses.get_address(conn, ref)["address_id"]
    state = load(conn, aid)
    if state is None:
        raise ConflictError(f"no reprocess of {aid} is running")
    _finish(conn, clock, aid, state, "stopped")
    return {"address_id": aid, "reclassify": state["reclassify"], "replan": state["replan"]}


def status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in addresses.list_addresses(conn):
        aid = a["address_id"]
        state = load(conn, aid)
        if state is not None:
            out.append({"address_id": aid, "outcome": "running"}
                       | {k: v for k, v in state.items() if k != "after"})  # fmt: skip
            continue
        row = conn.execute("SELECT value FROM settings WHERE key = ?",
                           (LAST_KEY.format(aid),)).fetchone()  # fmt: skip
        if row is not None:
            out.append({"address_id": aid} | json.loads(row[0]))
    return out


def summary_lines(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """The run summary's own lines (plan §4.3, §11 P28): plans that now differ for moved mail,
    mail that now wouldn't be hidden (`ecf item undo`), dropped labels, and new unsafe actor
    proposals the go-live gate counts."""
    from ecf_server import gate  # noqa: PLC0415 - gate imports far more than this needs

    rows = conn.execute(
        "SELECT stable_id, data FROM audit WHERE event = 'item.reprocess_differs'"
        " AND json_extract(data, '$.run') = ? ORDER BY id", (run_id,)).fetchall()  # fmt: skip
    differs = [r["stable_id"][:8] for r in rows if json.loads(r["data"])["hides"] is True]
    unhide = [r["stable_id"][:8] for r in rows if json.loads(r["data"])["hides"] is False]
    dropped = sum(int(json.loads(r["data"]).get("dropped_labels") or 0) for r in rows)
    out: list[str] = []
    if differs:
        out.append(f"plan now differs: {len(differs)} ({', '.join(differs[:20])}); no mailbox"
                   " action ran")  # fmt: skip
    if unhide:
        out.append(f"now wouldn't hide: {len(unhide)} ({', '.join(unhide[:20])}): ecf item undo"
                   " <id> to put one back")  # fmt: skip
    if dropped:
        out.append(f"labels the new plan drops, left on the mail: {dropped}")
    unsafe = 0
    for (p,) in conn.execute("SELECT proposal FROM items WHERE json_extract(facts,"
                             " '$.reprocess') = ?", (run_id,)):  # fmt: skip
        unsafe += gate.unsafe(json.loads(p or "{}").get("plan") or {})
    if unsafe:
        out.append(f"{unsafe} new unsafe proposal(s): the go-live gate counts them")
    return out
