"""Cards to Slack messages (SPEC §10.1; V1.2 step 3). Every string on a card may come from an email,
so everything is rendered as plain text:

- Block Kit `plain_text` objects only, never `mrkdwn`: Slack shows `<!channel> *bold* <link>` in
  them literally (tested 2026-09-29, §21.1). The one exception is a mention of your member ID on
  escalations (`Card.mention`): a `mrkdwn` section holding only `<@U…>`, built from a validated ID
  (V1.2 step 6). It notifies on desktop and phone and counts as a mention (real-service test 0c,
  2026-09-29, §21.1). The other is a draft card's warning (`Card.warnings`): bold, and only ecf's
  fixed wording (`chat.FIXED_WARNINGS`; operator decision 2026-11-01); anything else in that field
  stays plain text. The notification fallback `text` is escaped (`&`,
  `<`, `>`) and sent with `mrkdwn: false`, and posts turn unfurls off.
- Control and format characters are removed (a right-to-left override can reorder a sender line;
  zero-width characters hide text), keeping line breaks and tabs.
- URL schemes are defanged (`https[:]//`), so nothing on a card is a link, even where a Slack
  client might link bare URLs in plain text (unverified; security review of the V1.2 plan).
- Slack's size limits are applied: 150 characters for a header, 2,000 per field, 3,000 per text
  block, 75 per button label, 10 fields per section, 25 buttons per message (Block Kit reference;
  unverified here beyond the test's own messages). Card text longer than one block goes on in the
  next, so a draft's whole text is on its card (§8.4); `fits` says whether all of it is there.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ecf_server.chat import FIXED_WARNINGS, Card

HEADER_MAX = 150
FIELD_MAX = 2000
TEXT_MAX = 3000
LABEL_MAX = 75
FIELDS_PER_SECTION = 10
BUTTONS_MAX = 25
FALLBACK_MAX = 3000
TEXT_LINES_MAX = 30  # lines with a section of their own; the lines after share the last one
# text sections per card: with the header, mention, fields, buttons and note a card stays under
# Slack's 50 blocks per message (unverified)
TEXT_BLOCKS_MAX = 40
MARK = "│ "  # starts every draft line and each section it continues in (M8 plan §6)
WARNING_SIGN = "\u26a0\ufe0f"  # with its emoji selector: a yellow sign, not a black-and-white one
_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}):(//)")
_MEMBER = re.compile(r"[UW][A-Z0-9]{2,20}")


def scrub(text: str) -> str:
    kept = [
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs")
    ]
    return _SCHEME.sub(r"\1[:]\2", "".join(kept))


def clean(text: str, limit: int) -> str:
    """Plain, safe, bounded text for Slack."""
    out = scrub(text)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def fits(text: str) -> bool:
    """Whether a card shows all of this text, each line apart: what an approval of a draft relies
    on (§8.4). Past TEXT_LINES_MAX lines run together, so a long draft doesn't fit (AI8)."""
    return _pieces(text)[1]


def fallback(text: str) -> str:
    """The notification text: escaped, since Slack reads `text` as markup."""
    safe = clean(text, FALLBACK_MAX)
    return safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plain(text: str, limit: int) -> dict[str, Any]:
    return {"type": "plain_text", "text": clean(text, limit) or " ", "emoji": False}


def blocks(card: Card) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = (
        [{"type": "header", "text": _plain(card.title, HEADER_MAX)}] if card.title else []
    )
    if _MEMBER.fullmatch(card.mention):  # the one mrkdwn object: a mention, built by ecf
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<@{card.mention}>"}})
    fields = [_plain(f"{label}: {value}", FIELD_MAX) for label, value in card.fields]
    for i in range(0, len(fields), FIELDS_PER_SECTION):
        out.append({"type": "section", "fields": fields[i : i + FIELDS_PER_SECTION]})
    if card.context or card.warnings:
        out.append({"type": "context", "elements": _context_elements(card)})
    out += _text_sections(card.text)
    if card.status:
        out.append({"type": "section", "text": _plain(card.status, TEXT_MAX)})
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


def _context_elements(card: Card) -> list[dict[str, Any]]:
    """`verbatim`: Slack parses nothing in the fixed wording. A context block takes 10 elements."""
    out = [_plain(card.context, TEXT_MAX)] if card.context else []
    for w in card.warnings:
        out.append({"type": "mrkdwn", "verbatim": True, "text": f"{WARNING_SIGN} *{w}*"}
                   if w in FIXED_WARNINGS else _plain(f"{WARNING_SIGN} {w}", TEXT_MAX))  # fmt: skip
    return out[:10]


def _text_sections(text: str) -> list[dict[str, Any]]:
    """One section per non-empty line: Slack runs the lines of one plain_text section together
    (seen in the V1.2 shadow run, 2026-09-30). Lines past the cap share the last section."""
    return [{"type": "section", "text": _plain(p, TEXT_MAX)} for p in _pieces(text)[0]]


def _pieces(text: str) -> tuple[list[str], bool]:
    """The card text as sections of at most TEXT_MAX characters, and whether all of it is there,
    each line on its own. A line too long for one section goes on in the next, split at a space
    where there is one, keeping its mark; only past TEXT_BLOCKS_MAX sections is the end cut."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    apart = len(lines) <= TEXT_LINES_MAX
    if not apart:
        lines = [*lines[: TEXT_LINES_MAX - 1], " · ".join(lines[TEXT_LINES_MAX - 1 :])]
    out: list[str] = []
    for ln in lines:
        rest = scrub(ln)
        mark = MARK if rest.startswith(MARK) else ""
        while len(rest) > TEXT_MAX:
            cut = rest.rfind(" ", TEXT_MAX // 2, TEXT_MAX)
            cut = cut if cut > 0 else TEXT_MAX
            # a cut can start a new word that reads as a URL scheme: scrub each side again, and
            # cut earlier while defanging makes the head too long, so no section is shortened
            while len(head := scrub(rest[:cut])) > TEXT_MAX and cut > 1:
                cut -= 1
            out.append(head)
            rest = mark + scrub(rest[cut:].lstrip())
        if rest:
            out.append(rest)
    if len(out) <= TEXT_BLOCKS_MAX:
        return out, apart
    tail = clean(" ".join(out[TEXT_BLOCKS_MAX - 1 :]), TEXT_MAX)
    return [*out[: TEXT_BLOCKS_MAX - 1], tail], False


def action_name(action_id: str) -> str:
    """The card's action from a Slack `action_id` (`approve#0` -> `approve`)."""
    return action_id.split("#", 1)[0]
