"""Item cards (SPEC §10.1; V1.2 step 6): what a Slack card shows about one email.

A card carries metadata only: the sender, the subject, why ecf flagged it, the sender check, the
flags and what was done, and how to open the email (`ecf item show <id>`; no provider webmail
search URL is known and verified yet, §10.1 "where known"). Never the body; a short excerpt only
through Show excerpt, visible to you alone (OD-214). Every string here may come from an email:
`slack_render` shows it as plain text.

A classified item's card also says how it was classified (OD-507 §6, D14, P26): "Classified:
<category>, <priority> priority, by <model>", the model by its display name ("Claude Sonnet
5.5"), "a model no longer installed" for one this release doesn't pin (the local model's, before
v3.0.0), and "corrected by you" after your correction. Nothing for an item not classified.
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
from importlib import resources
from typing import Any

from ecf_server.chat import Button, Card
from ecf_server.precheck import payment_or_fraud

URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{1,20}://|www\.)\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def defang(text: str) -> str:
    """Model text shown in full but not clickable (a draft on its approval card, OD-317): links
    and addresses keep their wording with their separators bracketed."""
    t = URL.sub(lambda m: m.group(0).replace("://", "[:]//").replace(".", "[.]"), text)
    return EMAIL.sub(lambda m: m.group(0).replace("@", "[at]").replace(".", "[.]"), t)


PAYMENT_NOTE = "This acts on the email only. ecf never pays anything."
SHORT_ID = 8
SHOW_EXCERPT, DISMISS = "show_excerpt", "dismiss"


def kind(facts: dict[str, Any]) -> str:
    """'quarantine', 'fraud' or 'regulator': what the escalation is about, most serious first."""
    t: dict[str, Any] = facts.get("triggers") or {}
    if facts.get("quarantined"):
        return "quarantine"
    # impersonation is in `fraud` only with a payment keyword; rule 1 also escalates it on the
    # classifier's payment_related alone (OD-436), so it counts here either way (V1.6)
    return "fraud" if t.get("fraud") or t.get("impersonation") else "regulator"


def severity(facts: dict[str, Any]) -> int:
    """0 is most severe: a known sender with bank or change wording (§5.4: "a known sender with a
    bank change first"), then other fraud and quarantined mail, then regulatory mail."""
    k = kind(facts)
    if k == "regulator":
        return 2
    kw: dict[str, Any] = facts.get("keywords") or {}
    known = facts.get("sender_confirmed") or facts.get("sender_seen_before")
    return 0 if known and (kw.get("bank") or kw.get("change")) else 1


def dismissible(facts: dict[str, Any]) -> bool:
    """Dismiss is never offered on payment, fraud or regulator items (OD-213)."""
    t: dict[str, Any] = facts.get("triggers") or {}
    return not payment_or_fraud(facts) and not t.get("regulator")


TITLES = {
    "quarantine": "Quarantined email: ecf couldn't read it safely",
    "fraud": "Possible fraud",
    "regulator": "Regulatory mail",
}


def item_card(item: sqlite3.Row, *, mention: str = "", title: str = "") -> Card:
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    t: dict[str, Any] = facts.get("triggers") or {}
    k = kind(facts)
    heading = title or TITLES[k] + (" (also regulatory)" if k != "regulator" and t.get("regulator")
                                    else "")  # fmt: skip
    buttons = [Button(SHOW_EXCERPT, "Show excerpt", item["stable_id"])]
    if dismissible(facts):
        buttons.append(Button(DISMISS, "Dismiss", item["stable_id"]))
    return Card(
        heading,
        fields=(
            ("From", sender_line(item, facts)),
            ("Subject", subject_line(item)),
            *((("Classified", line),) if (line := classified_line(item)) else ()),
            ("Why", why(facts)),
            ("Sender check", sender_check(facts)),
            ("Flags", flags(facts)),
            ("Done", done(facts)),
            ("Open", f"ecf item show {item['stable_id'][:SHORT_ID]}"),
        ),
        buttons=tuple(buttons),
        note=PAYMENT_NOTE if payment_or_fraud(facts) else "",
        mention=mention,
    )


def escalation_line(r: sqlite3.Row) -> str:
    """One escalated item in a list (a burst card, a scan summary's reply)."""
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    who = sender_line(r, facts)
    return (f"{TITLES[kind(facts)]}: {who}: {subject_line(r)}"
            f" (ecf item show {r['stable_id'][:SHORT_ID]})")  # fmt: skip


_MODEL_ID = re.compile(r"claude-(haiku|sonnet|opus)-(\d+)-(\d+)(?:-\d{8})?")
NOT_INSTALLED = "a model no longer installed"


@functools.cache
def _known_models() -> frozenset[str]:
    """Every model ID this release pins (`models.lock`: the roles and their lifecycle)."""
    raw = json.loads(resources.files("ecf_server.data").joinpath("models.lock").read_text("utf-8"))
    ids = {str(v) for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}
    return frozenset(ids | {str(k) for k in raw.get("lifecycle", {})})


def model_name(model_id: str | None) -> str:
    """A model ID as a person reads it: `claude-sonnet-5-5` is "Claude Sonnet 5.5"."""
    m = _MODEL_ID.fullmatch(model_id or "")
    if model_id not in _known_models() or m is None:
        return NOT_INSTALLED
    return f"Claude {m.group(1).title()} {m.group(2)}.{m.group(3)}"


def classified_line(item: sqlite3.Row) -> str | None:
    """The card's "Classified" field (module notes), or None for an unclassified item."""
    keys = item.keys()
    if "classification" not in keys or not item["classification"]:
        return None
    cls: dict[str, Any] = json.loads(item["classification"])
    fix: dict[str, Any] = json.loads(item["human_correction"] or "{}") if (
        "human_correction" in keys) else {}  # fmt: skip
    changed = {k: v for k, v in fix.items() if cls.get(k) != v}
    cls |= {k: v for k, v in changed.items() if k in ("category", "priority")}
    pinned: dict[str, Any] = json.loads(item["pinned_models"] or "{}") if (
        "pinned_models" in keys) else {}  # fmt: skip
    by = "corrected by you" if changed else f"by {model_name(pinned.get('classifier'))}"
    parts = [str(cls[k]) for k in ("category",) if cls.get(k)]
    if cls.get("priority"):
        parts.append(f"{cls['priority']} priority")
    return ", ".join([*parts, by])


NOT_RECORDED = " (sender not recorded before V1.2)"


def sender_line(item: sqlite3.Row, facts: dict[str, Any]) -> str:
    sender, name = item["sender"], item["sender_name"]
    if sender:
        return f"{name} <{sender}>" if name else str(sender)
    domain = facts.get("from_domain") or "an unknown domain"
    return f"someone at {domain}{NOT_RECORDED}"


def short_sender(sender: str, limit: int = 40) -> str:
    """A sender line for a list: past `limit`, the display name goes first; the address is never
    cut, since a cut domain can hide a lookalike (V1.2 shadow run, 2026-09-30)."""
    if len(sender) <= limit:
        return sender
    if sender.endswith(">") and "<" in sender:
        return sender[sender.rindex("<") + 1 : -1]
    return sender.removesuffix(NOT_RECORDED)


def subject_line(item: sqlite3.Row) -> str:
    if item["subject"] is None:
        return "(not recorded before V1.2)"
    return str(item["subject"]) or "(no subject)"


def why(facts: dict[str, Any]) -> str:
    t: dict[str, Any] = facts.get("triggers") or {}
    parts: list[str] = [*t.get("fraud", []), *t.get("regulator", [])]
    parts += [x for x in t.get("impersonation", []) if x not in parts]  # weak ones too (V1.6)
    if not t.get("fraud"):  # weak reasons when nothing stronger says why (OD-490)
        parts += [x for x in t.get("fraud_weak", []) if x not in parts]
    parts += [f"looks like {d}" for d in t.get("lookalikes", [])]
    if facts.get("quarantined"):
        parts.insert(0, "reading it crashed ecf twice; it was set aside")
    precheck: dict[str, Any] = facts.get("precheck") or {}
    reasons: list[str] = precheck.get("reasons") or []
    return "; ".join(parts) or "; ".join(reasons)


def sender_check(facts: dict[str, Any]) -> str:
    result = facts.get("auth_result") or "none"
    auth: dict[str, Any] = facts.get("auth") or {}
    reason = auth.get("reason")
    return f"{result}: {reason}" if reason else str(result)


def flags(facts: dict[str, Any]) -> str:
    out = [
        label
        for key, label in (
            ("reply_to_mismatch", "Reply-To differs from From"),
            ("recipient_mismatch", "not addressed to this mailbox"),
            ("content_unscanned", "not fully scanned"),
            ("bulk_signal", "bulk mail"),
        )
        if facts.get(key)
    ]
    if not (facts.get("sender_seen_before") or facts.get("sender_confirmed")):
        out.insert(0, "first-time sender")
    return ", ".join(out) or "none"


def done(facts: dict[str, Any]) -> str:
    p: dict[str, Any] = facts.get("precheck") or {}
    if p.get("stage") == "shadow":
        return "nothing in the mailbox (shadow stage)"
    executed: list[str] = p.get("executed") or []
    return ", ".join(executed) if executed else str(p.get("skipped") or "nothing yet")
