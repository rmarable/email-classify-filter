"""Review posts (SPEC §9.2; OD-239; V1.3 step 6a): how you tell ecf whether the local model got
each email right, which the go-live gate counts (V1.3 step 6b).

- **When:** one post per address channel per business hour, listing emails the model classified
  since the last post that haven't been reviewed: all of them until the address reaches its gate
  count (100 on `standard`, 200 on `high`), then a sample at `review_sample_rate` (default 10%).
  An email left out of the sample is marked so and never offered again; one shown in a post is
  marked asked and not repeated (its buttons keep working).
- **What each line says:** the email (ID, sender, subject), what the model said (category,
  priority, fraud risk, payment) and what ecf would do. The model's words are labelled as such.
- **Buttons:** ✏️ Fix on every line opens a form to correct the classification; ✅ Correct appears on
  the lines "All others correct" may not confirm; **All others correct** confirms the rest of the
  post that you haven't fixed. A post holds at most 20 emails and at most 25 buttons (Slack's
  limit), so it lists fewer when many need their own button.
- **"All others correct" never counts** (OD-239) an email with a payment, fraud, regulator or
  `content_unscanned` signal (the facts OR the classification), nor any email on a `high` address:
  those need their own Correct or Fix. Each review records whether it was individual or bulk, so
  `ecf stage status` and the go-live dialog can show both.
- A review is a person's statement about the model's output; it changes nothing in the mailbox.
  Audited `review.recorded`. First review wins.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.schema import load_schema_v1
from ecf_server import (
    cards,
    digests,
    ollama,
    schedule,
    slack_admin,
    slack_in,
    slack_out,
    slack_routes,
)
from ecf_server.chat import Button, Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.precheck import fired
from ecf_server.slack_in import Click

FIX, CORRECT, ALL_OK = "review_fix", "review_correct", "review_all_ok"
LAST = "review_post_at:"  # + address_id
BATCH = "review_batch:"  # + post key: the emails "All others correct" may confirm
EVERY = timedelta(hours=1)
POST_MAX = 20
BUTTONS_MAX = 25
GATE_COUNT = {"standard": 100, "high": 200}  # §9.3
DEFAULT_SAMPLE = 10  # percent, after the gate count (§14)
FIXABLE = ("category", "priority", "fraud_risk", "payment_related", "sender_type")
MODEL_NOTE = "What ecf's model said (it can be wrong)."


# ---------------------------------------------------------------------------- who needs what


def needs_own_review(conn: sqlite3.Connection, item: sqlite3.Row) -> bool:
    """OD-239: payment, fraud, regulator or unscanned signals, or a `high` address."""
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    cls: dict[str, Any] = json.loads(item["classification"] or "{}")
    t: dict[str, Any] = facts.get("triggers") or {}
    addr = conn.execute("SELECT sensitivity FROM addresses WHERE address_id = ?",
                        (item["address_id"],)).fetchone()  # fmt: skip
    return bool(
        (addr is None or addr["sensitivity"] == "high")
        or fired(facts) or t.get("lookalikes") or facts.get("payment_keyword")
        or facts.get("quarantined") or facts.get("content_unscanned")
        or cls.get("payment_related") is True
        or cls.get("fraud_risk") in ("low", "medium", "high")
        or cls.get("category") in ("regulatory", "vendor_change_request")
    )  # fmt: skip


def reviewed(
    conn: sqlite3.Connection, address_id: str, digest: str | None = None
) -> dict[str, int]:
    """Counts for `ecf stage status` and the gate: reviewed, category correct, individual, bulk
    (only reviews of emails classified under `digest` when it's given)."""
    rows = conn.execute("SELECT review, pinned_models FROM items WHERE address_id = ?"
                        " AND json_extract(review, '$.verdict') IN ('correct', 'fixed')",
                        (address_id,)).fetchall()  # fmt: skip
    out = {"reviewed": 0, "category_ok": 0, "individual": 0, "bulk": 0}
    for r in rows:
        if digest is not None and json.loads(r["pinned_models"] or "{}").get("digest") != digest:
            continue
        rv: dict[str, Any] = json.loads(r["review"])
        out["reviewed"] += 1
        out["category_ok"] += bool(rv.get("category_ok"))
        out["bulk" if rv.get("bulk") else "individual"] += 1
    return out


def progress(conn: sqlite3.Connection, address_id: str, sensitivity: str,
             digest: str | None = None) -> dict[str, Any]:  # fmt: skip
    c = reviewed(conn, address_id, digest)
    target = GATE_COUNT[sensitivity]
    to_go = max(0, target - c["reviewed"])
    accuracy = round(100 * c["category_ok"] / c["reviewed"]) if c["reviewed"] else None
    text = f"{c['reviewed']}/{target} reviewed"
    if accuracy is not None:
        text += f", {accuracy}% accurate"
    text += f", {to_go} to go ({c['individual']} one by one, {c['bulk']} with All others correct)"
    return c | {"target": target, "to_go": to_go, "accuracy": accuracy, "text": text}


# ---------------------------------------------------------------------------- posting


def _sample_rate(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'review_sample_rate'").fetchone()
    return int(json.loads(row[0])) if row else DEFAULT_SAMPLE


def _sampled(stable_id: str, rate: int) -> bool:
    """A stable choice per email, so a restart doesn't change which ones are asked about."""
    return int(hashlib.sha256(stable_id.encode()).hexdigest()[:8], 16) % 100 < rate


def pending(conn: sqlite3.Connection, now: datetime, aid: str) -> list[sqlite3.Row]:
    """Emails to put in the next review post; marks the ones the sample leaves out."""
    sens = conn.execute("SELECT sensitivity FROM addresses WHERE address_id = ?",
                        (aid,)).fetchone()["sensitivity"]  # fmt: skip
    # every email until the gate's count is reached for the pinned model (the gate counts per
    # digest, so a model change starts the count again)
    full = reviewed(conn, aid, ollama.load_pin().digest)["reviewed"] < GATE_COUNT[sens]
    rate = _sample_rate(conn)
    rows = conn.execute("SELECT * FROM items WHERE address_id = ? AND classification IS NOT NULL"
                        " AND review IS NULL AND status != 'classified'"
                        " ORDER BY created_at, stable_id LIMIT 200", (aid,)).fetchall()  # fmt: skip
    out: list[sqlite3.Row] = []
    for r in rows:
        if full or _sampled(r["stable_id"], rate):
            out.append(r)
        else:
            _set_review(conn, now, r["stable_id"], {"verdict": "not_sampled"})
    return out


def run(conn: sqlite3.Connection, clock: Clock) -> int:
    """Post each address's review post when due (the Slack thread calls this)."""
    now = clock.now()
    posted = 0
    for (aid,) in conn.execute(
        "SELECT address_id FROM addresses WHERE removed_at IS NULL ORDER BY address_id"
    ).fetchall():
        route = slack_routes.route_for(conn, aid)
        last = slack_admin.setting(conn, LAST + aid)
        if route is None or (last and now - from_ts(last) < EVERY):
            continue
        if not schedule.in_business_hours(now, digests.business_hours(conn, aid)):
            continue
        rows = pending(conn, now, aid)
        if rows:
            key = f"{aid}:{to_ts(now)}"
            slack_out.enqueue_post(conn, clock, key=f"review:{key}", route=route,
                                   card=card(conn, now, aid, rows, key),
                                   identity=slack_routes.identity(aid))  # fmt: skip
            posted += 1
        with write_tx(conn):
            slack_admin.put_setting(conn, LAST + aid, to_ts(now), to_ts(now), actor="service")
    return posted


def card(conn: sqlite3.Connection, now: datetime, aid: str, rows: list[sqlite3.Row],
         key: str) -> Card:  # fmt: skip
    lines: list[str] = [MODEL_NOTE]
    buttons: list[Button] = []
    bulk: list[str] = []
    shown = 0
    for r in rows[:POST_MAX]:
        own = needs_own_review(conn, r)
        cost = 2 if own else 1
        if len(buttons) + cost + 1 > BUTTONS_MAX:
            break
        lines.append(_line(r) + (" (needs its own review)" if own else ""))
        short = r["stable_id"][: cards.SHORT_ID]
        buttons.append(Button(FIX, f"Fix {short}", r["stable_id"]))
        if own:
            buttons.append(Button(CORRECT, f"Correct {short}", r["stable_id"]))
        else:
            bulk.append(r["stable_id"])
        shown += 1
    if bulk:
        with write_tx(conn):
            slack_admin.put_setting(conn, BATCH + key, json.dumps(bulk), to_ts(now),
                                    actor="service")  # fmt: skip
        buttons.append(Button(ALL_OK, f"All others correct ({len(bulk)})", key, "primary"))
    for r in rows[:shown]:  # asked once; its own buttons keep working after the next post
        _set_review(conn, now, r["stable_id"], {"verdict": "asked", "post": key})
    left = len(rows) - shown
    if left > 0:
        lines.append(f"... and {left} more in the next post")
    return Card(f"Review: {aid} ({shown})", text="\n".join(lines), buttons=tuple(buttons))


def _line(r: sqlite3.Row) -> str:
    cls: dict[str, Any] = json.loads(r["classification"] or "{}")
    doc: dict[str, Any] = json.loads(r["proposal"] or "{}")
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    what = ", ".join(str(a["name"]) + (f" {a['target']}" if a.get("target") else "")
                     for a in doc.get("actions", [])) or "nothing"  # fmt: skip
    pay = ", payment" if cls.get("payment_related") else ""
    sender = cards.short_sender(cards.sender_line(r, facts))
    return (f"{r['stable_id'][: cards.SHORT_ID]} {sender}: {cards.subject_line(r)[:50]}"
            f" → {cls.get('category')}, {cls.get('priority')}, fraud {cls.get('fraud_risk')}"
            f"{pay}; would: {what}")  # fmt: skip


# ---------------------------------------------------------------------------- recording


def _set_review(conn: sqlite3.Connection, now: datetime, sid: str, review: dict[str, Any],
                correction: dict[str, Any] | None = None) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET review = ?, human_correction = coalesce(?, human_correction)"
                     " WHERE stable_id = ?",
                     (json.dumps(review, sort_keys=True),
                      json.dumps(correction, sort_keys=True) if correction else None,
                      sid))  # fmt: skip


def record(conn: sqlite3.Connection, clock: Clock, sid: str, *, actor: str, bulk: bool = False,
           correction: dict[str, Any] | None = None) -> str:  # fmt: skip
    """Record one review; the first wins. Returns the verdict."""
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if item is None or item["classification"] is None:
        raise InvalidInputError("nothing to review")
    old: dict[str, Any] = json.loads(item["review"] or "{}")
    if old.get("verdict") in ("correct", "fixed"):
        raise ConflictError("already reviewed")
    if bulk and needs_own_review(conn, item):
        raise ConflictError("this email needs its own review")
    cls: dict[str, Any] = json.loads(item["classification"])
    changed = {k: v for k, v in (correction or {}).items() if cls.get(k) != v}
    verdict = "fixed" if changed else "correct"
    now = to_ts(clock.now())
    review = {"verdict": verdict, "category_ok": "category" not in changed, "bulk": bulk,
              "by": actor, "at": now}  # fmt: skip
    _set_review(conn, clock.now(), sid, review, changed or None)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'review.recorded', ?, 'ok', ?)",
            (now, item["address_id"], sid, actor,
             json.dumps({"verdict": verdict, "bulk": bulk, "fields": sorted(changed)})),
        )  # fmt: skip
    return verdict


def _choices(field: str) -> list[str]:
    spec = load_schema_v1().fields[field]
    return ["true", "false"] if spec.kind.value == "boolean" else list(spec.values)


def correction_from(values: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in FIXABLE:
        v = values.get(f, "")
        if not v:
            continue
        if v not in _choices(f):
            raise InvalidInputError(f"{f}: not one of the choices")
        out[f] = v == "true" if _choices(f) == ["true", "false"] else v
    return out


# ---------------------------------------------------------------------------- Slack


@slack_in.handles(CORRECT)
def _correct(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    record(conn, clock, click.ref, actor=f"slack:{click.user}")
    _tell(conn, clock, click, f"Recorded: {click.ref[:8]} correct.")


@slack_in.handles(ALL_OK)
def _all_ok(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    raw = slack_admin.setting(conn, BATCH + click.ref)
    sids: list[str] = json.loads(raw) if raw else []
    done = skipped = 0
    for sid in sids:
        try:
            record(conn, clock, sid, actor=f"slack:{click.user}", bulk=True)
            done += 1
        except (ConflictError, InvalidInputError):
            skipped += 1  # fixed or reviewed one by one already
    _tell(conn, clock, click, f"Recorded {done} as correct" +
          (f"; {skipped} already reviewed." if skipped else "."))  # fmt: skip


@slack_in.opens_form(FIX)
def _fix_form(conn: sqlite3.Connection, click: Click) -> dict[str, Any]:
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (click.ref,)).fetchone()
    cls: dict[str, Any] = json.loads(item["classification"] or "{}") if item else {}
    blocks: list[dict[str, Any]] = [
        {"type": "context", "elements": [{"type": "plain_text", "text": MODEL_NOTE}]}
    ]
    for f in FIXABLE:
        current = str(cls.get(f)).lower() if isinstance(cls.get(f), bool) else str(cls.get(f))
        options = [{"text": {"type": "plain_text", "text": c}, "value": c} for c in _choices(f)]
        element: dict[str, Any] = {"type": "static_select", "action_id": "v", "options": options}
        if current in _choices(f):
            element["initial_option"] = {"text": {"type": "plain_text", "text": current},
                                         "value": current}  # fmt: skip
        blocks.append({"type": "input", "block_id": f, "optional": True, "element": element,
                       "label": {"type": "plain_text", "text": f.replace("_", " ")}})  # fmt: skip
    return {"type": "modal", "callback_id": FIX, "private_metadata": click.ref,
            "title": {"type": "plain_text", "text": "Fix the classification"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"}, "blocks": blocks}  # fmt: skip


@slack_in.handles(FIX)
def _fix_submitted(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    if click.kind != "form":
        return
    verdict = record(conn, clock, click.ref, actor=f"slack:{click.user}",
                     correction=correction_from(click.values))  # fmt: skip
    slack_out.enqueue_post(conn, clock, key=f"review-fix:{click.ref}:{to_ts(clock.now())}",
                           route=RouteRef(click.user),
                           card=Card(f"Recorded: {click.ref[:8]} {verdict}"))  # fmt: skip


def _tell(conn: sqlite3.Connection, clock: Clock, click: Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip
