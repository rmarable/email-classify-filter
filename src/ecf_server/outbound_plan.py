"""Drafts and sends, resolved before approval and bound into the grant (SPEC §6.4, §8.4; OD-317).

A proposed `draft_reply`, `reply_template` or `forward_internal` becomes a *payload* that says
exactly what would be written and to whom, and the grant's action hash covers it (actions.Planned):

- `draft_reply`: the recipient (the email's From address, never a Reply-To) and the draft's text
  (written by the actor: at most 4,000 characters, control and format characters removed, line
  breaks kept);
- `reply_template`: the recipient, the template's id and a hash of its rendered subject and body;
- `forward_internal`: the forward allow-list entry's id and its address.

So an approval covers that text and that recipient only: if a template or allow-list entry
changes after the approval, `check` (run again at execution) finds the payload no longer matches
and the action is refused, never carried out with the new text or address.

The guardrails of §8.4 that depend on the email are policy's (policy.send_refusal).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from typing import Any

from ecf.errors import InvalidInputError
from ecf_server import config, templates
from ecf_server.outbound_msg import addr_spec

DRAFT_MAX = 4000
SENDS = frozenset({"forward_internal", "reply_template"})
OUTBOUND = SENDS | {"draft_reply"}


class Unresolved(InvalidInputError):
    """The draft or send can't be resolved (no usable sender, unknown template or entry)."""


def clean_draft(text: str) -> str:
    """The actor's draft text: control and format characters removed (line breaks and tabs kept),
    trailing spaces trimmed, at most DRAFT_MAX characters."""
    kept = "".join(
        c if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf") else ""
        for c in text.replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [ln.rstrip() for ln in kept.split("\n")]
    return "\n".join(lines).strip()[:DRAFT_MAX]


def enabled_templates(conn: sqlite3.Connection) -> dict[str, templates.Template]:
    stored = config.current(conn)["templates"]
    found = (templates.load_templates(config.canonical_json(stored)) if stored is not None
             else templates.load_default_templates())  # fmt: skip
    return {k: t for k, t in found.items() if t.enabled}


def forward_entries(conn: sqlite3.Connection) -> dict[str, str]:
    entries: list[dict[str, str]] = config.current(conn)["forward_allow_list"] or []
    return {str(e["id"]): str(e["address"]) for e in entries}


def resolve(conn: sqlite3.Connection, item: sqlite3.Row, name: str, target: str | None,
            text: str | None = None) -> dict[str, Any]:  # fmt: skip
    """The payload bound into the grant; raises Unresolved."""
    if name == "forward_internal":
        to = forward_entries(conn).get(target or "")
        if to is None:
            raise Unresolved(f"{str(target)[:40]!r} isn't on the forward allow-list")
        return {"entry": target, "to": to}
    sender = _sender(item)
    if name == "draft_reply":
        body = clean_draft(text or "")
        if not body:
            raise Unresolved("the draft has no text")
        return {"to": sender, "text": body}
    if name == "reply_template":
        t = enabled_templates(conn).get(target or "")
        if t is None:
            raise Unresolved(f"{str(target)[:40]!r} isn't an enabled template")
        r = render(item, t)
        return {"to": sender, "template": t.id, "rendered": _digest(r.subject, r.body)}
    raise Unresolved(f"{name} has no payload")


def render(item: sqlite3.Row, t: templates.Template) -> templates.Rendered:
    return templates.render(t, sender_name=str(item["sender_name"] or ""),
                            subject=str(item["subject"] or ""))  # fmt: skip


def check(conn: sqlite3.Connection, item: sqlite3.Row, name: str, target: str | None,
          payload: dict[str, Any] | None) -> str | None:  # fmt: skip
    """At execution: why the approved payload no longer matches the current config, or None."""
    if payload is None:
        return "no approved text or recipient"
    try:
        now = resolve(conn, item, name, target, payload.get("text"))
    except Unresolved as exc:
        return str(exc.detail)
    if now != payload:
        return "the template, allow-list entry or recipient changed since the approval"
    return None


def _sender(item: sqlite3.Row) -> str:
    try:
        return addr_spec(str(item["sender"] or ""))
    except InvalidInputError:
        raise Unresolved("the sender's address can't be replied to") from None


def _digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()
