"""Review posts (SPEC §9.2; OD-239; V1.3 step 6a): how you tell ecf whether the local model got
each email right, which the go-live gate counts (V1.3 step 6b).

- **When:** one post per address channel per business hour, listing emails the model classified
  since the last post that haven't been reviewed: all of them until the address reaches its gate
  count (100 on `standard`, 200 on `high`), then a sample at `review_sample_rate` (default 10%).
  An email left out of the sample is marked so and never offered again; one shown in a post is
  marked asked and not repeated (its buttons keep working).
- **What each line says:** the email (ID, sender, subject), what the model said (category,
  priority, fraud risk, payment, the fixed items that hold; then `ext:` and the extension fields'
  values, kept short) and what ecf would do. The model's words are labelled as such.
- **Buttons:** ✏️ Fix on every line opens a form to correct the classification (the shipped
  fields in `FIXABLE`, then each of your own fields, labelled "(yours)"); ✅ Correct appears on
  the lines "All others correct" may not confirm; **All others correct** confirms the rest of the
  post that you haven't fixed. A post holds at most 20 emails and at most 25 buttons (Slack's
  limit), so it lists fewer when many need their own button.
- **"All others correct" never counts** (OD-239) an email with a payment, fraud, regulator,
  bank detail change, phishing or claimed-colleague signal or `content_unscanned` (the facts OR
  the classification; v3.0.0 S3, OD-500), nor any email on a `high` address:
  those need their own Correct or Fix. Each review records whether it was individual or bulk, so
  `ecf stage status` and the go-live dialog can show both.
- A review is a person's statement about the model's output; it changes nothing in the mailbox.
  Audited `review.recorded`. First review wins.
- **Accuracy** (the go-live gate's, §9.3; v3.0.0 S4, D4, P30, OD-502): reviews whose Fix changed
  none of the 13 `FIXABLE` fields (the 11 fixed ones, `category`, `sender_type`) over reviews:
  `gated_ok`, written at record time; a review from before S4 has no key and counts as its
  verdict (`correct`, no Fix at all; then no user field existed). A Fix to one of your own fields
  is counted (`user_fixed`) and reported, not gated. Fixed-item and fixed-field accuracy (no Fix
  to a `FIXED_ITEMS` value, or to another `ecf.schema.FIXED_FIELDS` value; apart, so `priority`,
  the noisiest field, doesn't hide the items, P22) and category accuracy are reported too.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.schema import FIXED_FIELDS, FIXED_ITEMS, CompiledSchema, FieldKind, fixed_item
from ecf_server import (
    cards,
    claude_pins,
    config,
    digests,
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
# the Fix form: the default fields, then the fixed ones (v3.0.0 S3, OD-500)
FIXABLE = ("category", "priority", "sender_type", "fraud_risk", "payment_related", *FIXED_ITEMS,
           "requires_action", "requires_reply", "deadline_mentioned")  # fmt: skip
# the fixed fields that aren't items (`ecf.schema.FIXED_FIELDS`), for fixed-field accuracy (P22)
FIXED_NOT_ITEMS = tuple(f for f in FIXED_FIELDS if f not in FIXED_ITEMS)
YOURS = " (yours)"  # the Fix form labels your own fields so (S4, R36)
ITEM_WORDS = {"bank_detail_change": "bank change", "regulator_claim": "regulator claim",
              "phishing": "phishing", "claims_internal": "claims internal",
              "private_individual": "private individual"}  # fmt: skip
OPTIONS_MAX = 100  # Slack's limit on a static select's options
EXT_CHARS = 80  # the review line's `ext:` part
MODEL_NOTE = "What ecf's model said (it can be wrong)."


# Who needs what: which emails need their own review, and the reviewed counts and progress the
# go-live gate reads.


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
        or any(fixed_item(cls, i) for i in ("regulator_claim", "bank_detail_change", "phishing",
                                             "claims_internal"))  # OD-500: raise risk only
    )  # fmt: skip


def reviewed(
    conn: sqlite3.Connection, address_id: str, digest: str | None = None
) -> dict[str, int]:
    """Counts for `ecf stage status` and the gate: reviewed; `gated_ok`, the gated accuracy (no
    Fix to a `FIXABLE` field, D4; `no_fix` its old name, kept); `user_fixed` (a Fix to one of
    your fields); category correct; fixed items and the other fixed fields correct (no Fix
    changed one, from the classification and the correction; P22); individual, bulk (only
    reviews of emails classified under `digest` when it's given)."""
    rows = conn.execute("SELECT review, pinned_models, classification, human_correction FROM items"
                        " WHERE address_id = ?"
                        " AND json_extract(review, '$.verdict') IN ('correct', 'fixed')",
                        (address_id,)).fetchall()  # fmt: skip
    out = {"reviewed": 0, "gated_ok": 0, "no_fix": 0, "user_fixed": 0, "category_ok": 0,
           "fixed_ok": 0, "items_ok": 0, "fields_ok": 0, "individual": 0, "bulk": 0}  # fmt: skip
    for r in rows:
        if digest is not None and claude_pins.item_key(r["pinned_models"]) != digest:
            continue
        rv: dict[str, Any] = json.loads(r["review"])
        out["reviewed"] += 1
        ok = rv.get("gated_ok")  # pre-S4: no key, and no user field to fix then (D4)
        out["gated_ok"] += bool(ok) if isinstance(ok, bool) else rv.get("verdict") == "correct"
        out["no_fix"] += rv.get("verdict") == "correct"
        out["user_fixed"] += bool(rv.get("user_fixed"))
        out["category_ok"] += bool(rv.get("category_ok"))
        cls: dict[str, Any] = json.loads(r["classification"] or "{}")  # once per row (R10)
        fix: dict[str, Any] = json.loads(r["human_correction"] or "{}")
        out["fixed_ok"] += _unfixed(cls, fix, FIXED_FIELDS)
        out["items_ok"] += _unfixed(cls, fix, FIXED_ITEMS)
        out["fields_ok"] += _unfixed(cls, fix, FIXED_NOT_ITEMS)
        out["bulk" if rv.get("bulk") else "individual"] += 1
    out["no_fix"] = out["gated_ok"]  # the gate's figure, under its old name too
    return out


def fixed_fields_ok(classification: str | None, correction: str | None,
                    fields: tuple[str, ...] = FIXED_FIELDS) -> bool:  # fmt: skip
    """No Fix changed one of `fields` (default every fixed field, `ecf.schema.FIXED_FIELDS`);
    a fixed item a classification lacks is false (step 1c P13)."""
    return _unfixed(json.loads(classification or "{}"), json.loads(correction or "{}"), fields)


def _unfixed(cls: dict[str, Any], fix: dict[str, Any], fields: tuple[str, ...]) -> bool:
    """`fixed_fields_ok` on parsed dicts."""
    return all((cls.get(k) is True) == (v is True) if k in FIXED_ITEMS else cls.get(k) == v
               for k, v in fix.items() if k in fields)  # fmt: skip


def progress(conn: sqlite3.Connection, address_id: str, sensitivity: str,
             digest: str | None = None) -> dict[str, Any]:  # fmt: skip
    c = reviewed(conn, address_id, digest)
    target = GATE_COUNT[sensitivity]
    to_go = max(0, target - c["reviewed"])
    accuracy = round(100 * c["no_fix"] / c["reviewed"]) if c["reviewed"] else None
    text = f"{c['reviewed']}/{target} reviewed"
    if accuracy is not None:
        text += f", {accuracy}% accurate"
    text += f", {to_go} to go ({c['individual']} one by one, {c['bulk']} with All others correct)"
    return c | {"target": target, "to_go": to_go, "accuracy": accuracy, "text": text}


# Posting: choosing the sampled emails and building each address's review post.


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
    # every email until the gate's count is reached for the pinned models (the gate counts per
    # pin key, so a model change starts the count again)
    full = reviewed(conn, aid, claude_pins.address_key(conn, aid))["reviewed"] < GATE_COUNT[sens]
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
    schema = config.current_schema(conn)
    for r in rows[:POST_MAX]:
        own = needs_own_review(conn, r)
        cost = 2 if own else 1
        if len(buttons) + cost + 1 > BUTTONS_MAX:
            break
        lines.append(_line(r, schema) + (" (needs its own review)" if own else ""))
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


def _line(r: sqlite3.Row, schema: CompiledSchema) -> str:
    cls: dict[str, Any] = json.loads(r["classification"] or "{}")
    doc: dict[str, Any] = json.loads(r["proposal"] or "{}")
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    what = ", ".join(str(a["name"]) + (f" {a['target']}" if a.get("target") else "")
                     for a in doc.get("actions", [])) or "nothing"  # fmt: skip
    pay = ", payment" if cls.get("payment_related") else ""
    items = "".join(f", {w}" for i, w in ITEM_WORDS.items() if fixed_item(cls, i))
    sender = cards.short_sender(cards.sender_line(r, facts))
    return (f"{r['stable_id'][: cards.SHORT_ID]} {sender}: {cards.subject_line(r)[:50]}"
            f" → {cls.get('category')}, {cls.get('priority')}, fraud {cls.get('fraud_risk')}"
            f"{pay}{items}{_ext(cls, schema)}; would: {what}")  # fmt: skip


def _ext(cls: dict[str, Any], schema: CompiledSchema) -> str:
    """`, ext: field=value, ...` for the extension fields the classification has (an item
    classified before the extension has none), cut to `EXT_CHARS`."""
    parts = [f"{f}={_shown(cls[f])}" for f in schema.extension_fields if f in cls]
    if not parts:
        return ""
    text = ", ".join(parts)
    return ", ext: " + (text if len(text) <= EXT_CHARS else text[: EXT_CHARS - 1] + "…")


def _shown(v: Any) -> str:
    return str(v).lower() if isinstance(v, bool) else str(v)


# Recording: storing a review or correction, and the fields the Fix form offers.


def _set_review(conn: sqlite3.Connection, now: datetime, sid: str, review: dict[str, Any],
                correction: dict[str, Any] | None = None) -> None:  # fmt: skip
    with write_tx(conn):  # updated_at too: the gate's tick looks for changed items (stages.tick)
        conn.execute("UPDATE items SET review = ?, updated_at = ?,"
                     " human_correction = coalesce(?, human_correction) WHERE stable_id = ?",
                     (json.dumps(review, sort_keys=True), to_ts(now),
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
    # D4, P30: accurate for the gate unless a Fix changed one of the 13 fixed and default fields
    # (an absent fixed item is false, so a Fix to false isn't a change); your own fields reported
    gated = {k for k, v in changed.items() if k in FIXABLE
             and not (k in FIXED_ITEMS and cls.get(k) is None and v is False)}  # fmt: skip
    review = {"verdict": verdict, "category_ok": "category" not in changed, "bulk": bulk,
              "gated_ok": not gated, "user_fixed": any(k not in FIXABLE for k in changed),
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


def fixable(schema: CompiledSchema) -> tuple[str, ...]:
    """The Fix form's fields: the shipped ones in `FIXABLE` the schema has, then each extension
    field."""
    return (*(f for f in FIXABLE if f in schema.fields), *schema.extension_fields)


def _choices(field: str, schema: CompiledSchema) -> list[str]:
    spec = schema.fields[field]
    if spec.kind is FieldKind.BOOLEAN:
        return ["true", "false"]
    return list(spec.values[:OPTIONS_MAX])


def correction_from(values: dict[str, str], schema: CompiledSchema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fixable(schema):
        v = values.get(f, "")
        if not v:
            continue
        if v not in _choices(f, schema):
            raise InvalidInputError(f"{f}: not one of the choices")
        out[f] = v == "true" if schema.fields[f].kind is FieldKind.BOOLEAN else v
    return out


# Slack: the handlers for the review post's buttons and its Fix form.


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
    schema = config.current_schema(conn)
    for f in fixable(schema):
        current = _shown(cls.get(f))
        choices = _choices(f, schema)
        options = [{"text": {"type": "plain_text", "text": c}, "value": c} for c in choices]
        element: dict[str, Any] = {"type": "static_select", "action_id": "v", "options": options}
        if current in choices:
            element["initial_option"] = {"text": {"type": "plain_text", "text": current},
                                         "value": current}  # fmt: skip
        label = f.replace("_", " ") + (YOURS if f in schema.extension_fields else "")
        blocks.append({"type": "input", "block_id": f, "optional": True, "element": element,
                       "label": {"type": "plain_text", "text": label}})  # fmt: skip
    return {"type": "modal", "callback_id": FIX, "private_metadata": click.ref,
            "title": {"type": "plain_text", "text": "Fix the classification"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"}, "blocks": blocks}  # fmt: skip


@slack_in.handles(FIX)
def _fix_submitted(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    if click.kind != "form":
        return
    correction = correction_from(click.values, config.current_schema(conn))
    verdict = record(conn, clock, click.ref, actor=f"slack:{click.user}", correction=correction)
    slack_out.enqueue_post(conn, clock, key=f"review-fix:{click.ref}:{to_ts(clock.now())}",
                           route=RouteRef(click.user),
                           card=Card(f"Recorded: {click.ref[:8]} {verdict}"))  # fmt: skip


def _tell(conn: sqlite3.Connection, clock: Clock, click: Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip
