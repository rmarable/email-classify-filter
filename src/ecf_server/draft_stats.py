"""`ecf drafts stats` (M8 plan §7, OD-583): what happened to the drafts ecf wrote, from
`draft_versions` and the items' stored classifications. Counts only, no text.

Coverage counts reviewed reply-needed items (an item still waiting for review has got nothing yet)
by what they got: a draft, a draft the checker refused, no draft
offered (by policy's code), or a draft the actor chose not to write. Before and after compares the
two newest actor prompts on the saved share and the 👍 share, with Newcombe's interval, once each
side has `MIN_N`. Cost is the session average per reviewed item: `claude_calls` has no item or role,
so a draft's own cost can't be told apart (P11).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ecf.eval.metrics import newcombe_unpaired, wilson
from ecf_server import claude_usage
from ecf_server.clock import from_ts, to_ts

BY = {"model": "drafter_model", "prompt": "actor_prompt", "category": "category",
      "address": "address_id", "seen_before": "sender_seen_before"}  # fmt: skip
MIN_N = 20
WEEKS = 8
SAVED = frozenset({"saved", "sent_unchanged", "sent_edited", "sent_unread", "deleted_unsent",
                   "still_waiting"})  # fmt: skip
SENT = frozenset({"sent_unchanged", "sent_edited"})


def _share(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "share": round(k / n, 3) if n else None,
            "low": round(lo, 3), "high": round(hi, 3)}  # fmt: skip


def _counts(rows: Iterable[sqlite3.Row]) -> dict[str, int]:
    out = {"drafts": 0, "refused": 0, "offered": 0, "settled": 0, "saved": 0, "sent": 0,
           "sent_unchanged": 0, "rejected": 0, "expired": 0, "up": 0, "down": 0}  # fmt: skip
    for r in rows:
        outcome = r["outcome"]
        out["drafts"] += 1
        out["refused"] += outcome == "refused"
        out["offered"] += outcome not in ("refused", "closed")
        out["settled"] += outcome not in (None, "refused", "closed")  # the rates' denominator
        out["saved"] += outcome in SAVED
        out["sent"] += outcome in SENT
        out["sent_unchanged"] += outcome == "sent_unchanged"
        out["rejected"] += outcome == "rejected"
        out["expired"] += outcome == "expired"
        out["up"] += r["rating"] == "up"
        out["down"] += r["rating"] == "down"
    return out


def _coverage(conn: sqlite3.Connection, cut: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT json_extract(i.proposal, '$.draft_withheld') AS withheld,"
        " json_extract(i.facts, '$.sender_seen_before') AS seen,"
        " EXISTS (SELECT 1 FROM draft_versions d WHERE d.stable_id = i.stable_id"
        " AND (d.outcome IS NULL OR d.outcome NOT IN ('refused', 'closed'))) AS drafted,"
        " EXISTS (SELECT 1 FROM draft_versions d WHERE d.stable_id = i.stable_id"
        " AND d.outcome = 'refused') AS refused,"
        " EXISTS (SELECT 1 FROM draft_versions d WHERE d.stable_id = i.stable_id"
        " AND d.outcome = 'closed') AS closed"
        " FROM items i WHERE i.created_at >= ? AND i.status NOT IN ('new', 'classified',"
        " 'awaiting_claude', 'clarified', 'needs_clarification')"
        " AND json_extract(i.classification, '$.requires_reply') = 1",
        (cut,)).fetchall()  # fmt: skip
    rest: Counter[str] = Counter()
    drafted = 0
    seen: dict[str, list[int]] = {"yes": [0, 0], "no": [0, 0], "unknown": [0, 0]}
    for r in rows:
        key = "unknown" if r["seen"] is None else ("yes" if r["seen"] else "no")
        seen[key][1] += 1
        if r["drafted"]:
            drafted += 1
            seen[key][0] += 1
        elif r["refused"]:
            rest["refused by the checker"] += 1
        elif r["closed"]:
            rest["draft withdrawn or not offered"] += 1
        elif r["withheld"]:
            rest[str(r["withheld"])] += 1
        else:
            rest["actor wrote none"] += 1
    return {"reply_needed": len(rows), "drafted": _share(drafted, len(rows)), "rest": dict(rest),
            "seen_before": {k: _share(*v) for k, v in seen.items() if v[1]}}  # fmt: skip


def _before_after(rows: list[sqlite3.Row]) -> dict[str, Any] | None:
    """The two newest actor prompts in the period, oldest first."""
    first: dict[str, str] = {}
    for r in rows:
        first.setdefault(r["actor_prompt"], r["created"])
    if len(first) < 2:
        return None
    before, after = sorted(first, key=lambda k: first[k])[-2:]
    a = _counts(r for r in rows if r["actor_prompt"] == before)
    b = _counts(r for r in rows if r["actor_prompt"] == after)
    out: dict[str, Any] = {"before": before[:12], "after": after[:12]}
    for name, ka, na, kb, nb in (
        ("saved", a["saved"], a["settled"], b["saved"], b["settled"]),
        ("thumbs_up", a["up"], a["up"] + a["down"], b["up"], b["up"] + b["down"]),
    ):
        if min(na, nb) < MIN_N:
            out[name] = {"enough": False, "before_n": na, "after_n": nb}
            continue
        lo, hi = newcombe_unpaired(kb, nb, ka, na)
        out[name] = {"enough": True, "before": _share(ka, na), "after": _share(kb, nb),
                     "difference": round(kb / nb - ka / na, 3),
                     "low": round(lo, 3), "high": round(hi, 3)}  # fmt: skip
    return out


def _weeks(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        y, w, _d = from_ts(r["created"]).isocalendar()
        groups.setdefault(f"{y}-W{w:02d}", []).append(r)
    return [{"week": k} | _counts(groups[k]) for k in sorted(groups)[-WEEKS:]]


def _group_name(by: str, value: Any) -> str:
    if value is None:
        return "unknown"
    if by == "seen_before":
        return "yes" if value else "no"
    return str(value)[:12] if by == "prompt" else str(value)


def _cost(conn: sqlite3.Connection, since: datetime, drafts: int) -> dict[str, Any]:
    u = claude_usage.report(conn, since)
    usd, items, sessions = u["all"]["api_equivalent_usd"], u["items"], u["sessions"]
    return {"sessions": sessions, "items": items, "tokens_per_item": u["tokens_per_item"],
            "usd_per_item": round(usd / items, 4) if usd is not None and items else None,
            "drafts_per_session": round(drafts / sessions, 1) if sessions else None}  # fmt: skip


def report(conn: sqlite3.Connection, since: datetime, *, by: str | None = None) -> dict[str, Any]:
    cut = to_ts(since)
    rows = conn.execute("SELECT * FROM draft_versions WHERE created >= ? ORDER BY created",
                        (cut,)).fetchall()  # fmt: skip
    c = _counts(rows)
    codes: Counter[str] = Counter()
    for r in rows:
        codes.update(json.loads(r["refuse_codes"]))
    reasons = Counter(str(r["reason"]) for r in rows if r["rating"] == "down" and r["reason"])
    out: dict[str, Any] = {
        "since": cut,
        "totals": c,
        "outcomes": dict(Counter(str(r["outcome"] or "waiting") for r in rows)),
        "headline": {"saved": _share(c["saved"], c["settled"]),
                     "rejected": _share(c["rejected"], c["settled"]),
                     "expired": _share(c["expired"], c["settled"]),
                     "sent_unchanged": _share(c["sent_unchanged"], c["sent"]),
                     "thumbs_up": _share(c["up"], c["up"] + c["down"])},
        "coverage": _coverage(conn, cut),
        "reasons": dict(reasons),
        "safety": {"refused_by_code": dict(codes),
                   "drafts_with_copied_links": sum(1 for r in rows if r["links_copied"]),
                   "promised": reasons.get("promised", 0)},
        "weeks": _weeks(rows),
        "before_after": _before_after(rows),
        "cost": _cost(conn, since, c["drafts"]),
    }  # fmt: skip
    if by is not None:
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault(_group_name(by, r[BY[by]]), []).append(r)
        split = [{"name": k} | _counts(groups[k]) for k in sorted(groups)]
        out["by"] = {"field": by, "groups": split}
    return out


def week_counts(conn: sqlite3.Connection, start: datetime, end: datetime) -> dict[str, int]:
    """The weekly post's counts."""
    rows = conn.execute("SELECT outcome, rating FROM draft_versions WHERE created >= ?"
                        " AND created < ?", (to_ts(start), to_ts(end))).fetchall()  # fmt: skip
    c = _counts(rows)
    edited = sum(1 for r in rows if r["outcome"] == "sent_edited")
    keys = ("drafts", "refused", "saved", "rejected", "expired", "up", "down", "sent_unchanged")
    return {k: c[k] for k in keys} | {"sent_edited": edited}
