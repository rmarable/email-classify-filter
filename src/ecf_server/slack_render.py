"""Cards to Slack messages (SPEC §10.1; V1.2 step 3). Every string on a card may come from an email,
so everything is rendered as plain text:

- Block Kit `plain_text` objects only, never `mrkdwn`: Slack shows `<!channel> *bold* <link>` in
  them literally (tested 2026-09-29, §21.1). The one exception is a mention of your member ID on
  escalations (`Card.mention`): a `mrkdwn` section holding only `<@U…>`, built from a validated ID
  (V1.2 step 6). It notifies on desktop and phone and counts as a mention (real-service test 0c,
  2026-09-29, §21.1). The notification fallback `text` is escaped (`&`,
  `<`, `>`) and sent with `mrkdwn: false`, and posts turn unfurls off.
- Control and format characters are removed (a right-to-left override can reorder a sender line;
  zero-width characters hide text), keeping line breaks and tabs.
- URL schemes are defanged (`https[:]//`), so nothing on a card is a link, even where a Slack
  client might link bare URLs in plain text (unverified; security review of the V1.2 plan).
- Slack's size limits are applied: 150 characters for a header, 2,000 per field, 3,000 per text
  block, 75 per button label, 10 fields per section, 25 buttons per message (Block Kit reference;
  unverified here beyond the test's own messages).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ecf_server.chat import Card

HEADER_MAX = 150
FIELD_MAX = 2000
TEXT_MAX = 3000
LABEL_MAX = 75
FIELDS_PER_SECTION = 10
BUTTONS_MAX = 25
FALLBACK_MAX = 3000
_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}):(//)")
_MEMBER = re.compile(r"[UW][A-Z0-9]{2,20}")


def clean(text: str, limit: int) -> str:
    """Plain, safe, bounded text for Slack."""
    kept = [
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs")
    ]
    out = _SCHEME.sub(r"\1[:]\2", "".join(kept))
    return out if len(out) <= limit else out[: limit - 1] + "…"


def fallback(text: str) -> str:
    """The notification text: escaped, since Slack reads `text` as markup."""
    safe = clean(text, FALLBACK_MAX)
    return safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plain(text: str, limit: int) -> dict[str, Any]:
    return {"type": "plain_text", "text": clean(text, limit) or " ", "emoji": False}


def blocks(card: Card) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"type": "header", "text": _plain(card.title, HEADER_MAX)}]
    if _MEMBER.fullmatch(card.mention):  # the one mrkdwn object: a mention, built by ecf
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<@{card.mention}>"}})
    fields = [_plain(f"{label}: {value}", FIELD_MAX) for label, value in card.fields]
    for i in range(0, len(fields), FIELDS_PER_SECTION):
        out.append({"type": "section", "fields": fields[i : i + FIELDS_PER_SECTION]})
    if card.text:
        out.append({"type": "section", "text": _plain(card.text, TEXT_MAX)})
    if card.buttons:
        elements: list[dict[str, Any]] = []
        for i, b in enumerate(card.buttons[:BUTTONS_MAX]):
            button: dict[str, Any] = {
                "type": "button",
                "action_id": f"{b.action}#{i}",  # unique within the block, as Slack requires
                "text": _plain(b.label, LABEL_MAX),
                "value": b.ref,
            }
            if b.style:
                button["style"] = b.style
            elements.append(button)
        out.append({"type": "actions", "elements": elements})
    if card.note:
        out.append({"type": "context", "elements": [_plain(card.note, TEXT_MAX)]})
    return out


def action_name(action_id: str) -> str:
    """The card's action from a Slack `action_id` (`approve#0` -> `approve`)."""
    return action_id.split("#", 1)[0]
