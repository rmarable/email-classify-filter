"""Excerpt preparation (pre-classifier plan step 10, §6 B; SPEC §5.1 step 4).

The classifier and actor excerpts are cut from the prepared body, not from the first text part:

- **Body:** every text/plain body part, joined; the visible HTML text only when the message has no
  plain part (S10). Attachments never count.
- **Bound** (P22): preparation reads the first `HEAD_CHARS` characters of the body; the excerpts
  are at most `message.ACTOR_CHARS` long, so nothing they could show is lost.
- **Cleanup** (P18): `skeleton.normalize` (NFKC, format characters removed) before anything else,
  so a zero-width character can't split an injection phrase past `redact_injection`; then runs of
  spaces become one and runs of blank lines one blank line (kept: redaction works per paragraph).
- **Moved, never deleted** (P4): quoted replies (`>` lines; a line `On … wrote:`, `-----Original
  Message-----`, a forwarded-message line, or `From:` with `Sent:` or `Date:` within the next four
  lines, each starting a quote that runs to the end; the HTML quote containers `htmltext` marks),
  the signature (from a `--` line to the quote or the end) and footer paragraphs (a phrase from
  `keywords.yaml`'s `excerpt_footers`) go after the new text, behind `MOVED_MARK`. The sender can
  write any of these markers, so a forged one can reorder a scam but not hide it, and the models'
  instructions say so.

Pure and bounded: every marker regex runs on one line of at most `MARKER_CHARS`, and the footer
check is a substring test on casefolded paragraphs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache
from importlib import resources

from ecf.yamlio import load_yaml
from ecf_server.skeleton import normalize

VERSION = 1  # joins the prompt digests (P18): a change here is a new gate
HEAD_CHARS = 256 * 1024  # operator decision 2026-10-11 (plan §6 B: 64-256 KB)
MARKER_CHARS = 400  # a longer line is never a quote marker
MARKER_LOOKAHEAD = 4  # lines after `From:` searched for `Sent:` or `Date:`
MOVED_MARK = ("[ecf moved quoted replies, signatures and footers below this line; the sender can"
              " write text that looks like any of these]")  # fmt: skip

_WROTE = re.compile(r"on\s.{1,300}\swrote:", re.I | re.S)
_ORIGINAL = re.compile(r"-{2,}\s*(?:original message|forwarded message)\s*-{2,}", re.I)
_FORWARDED = re.compile(r"begin forwarded message:", re.I)
_FROM = re.compile(r"\**from:\**\s*\S.*", re.I)
_SENT = re.compile(r"\**(?:sent|date):\**\s*\S.*", re.I)
_SPACES = re.compile(r"[ \t\f\v]+")


@cache
def footers() -> tuple[str, ...]:
    """The footer phrases, casefolded."""
    text = resources.files("ecf_server.data").joinpath("keywords.yaml").read_text("utf-8")
    data = load_yaml(text, source="keywords.yaml")
    return tuple(str(p).casefold() for p in data["excerpt_footers"]["phrases"])


def _marker(lines: list[str], i: int) -> bool:
    """Whether line `i` starts a quote that runs to the end."""
    line = lines[i].strip()
    if not line or len(line) > MARKER_CHARS:
        return False
    if _ORIGINAL.fullmatch(line) or _FORWARDED.fullmatch(line) or _WROTE.fullmatch(line):
        return True
    if (line[:2].casefold() == "on" and i + 1 < len(lines)
            and len(lines[i + 1].strip()) <= MARKER_CHARS
            and _WROTE.fullmatch(f"{line} {lines[i + 1].strip()}")):  # fmt: skip
        return True  # Gmail wraps a long attribution before "wrote:"
    if _FROM.fullmatch(line):
        ahead = lines[i + 1 : i + 1 + MARKER_LOOKAHEAD]
        return any(len(n) <= MARKER_CHARS and _SENT.fullmatch(n.strip()) for n in ahead)
    return False


def split(text: str) -> tuple[str, str]:
    """(new text, moved text) of one normalized body text, each tidied."""
    lines = text.split("\n")
    moved = [False] * len(lines)
    end = next((i for i in range(len(lines)) if _marker(lines, i)), len(lines))
    for i in range(end, len(lines)):
        moved[i] = True
    signature = False
    for i in range(end):
        stripped = lines[i].strip()
        if stripped == "--":
            signature = True
        if signature or stripped.startswith(">"):
            moved[i] = True
    start = 0  # footer paragraphs among what is left
    for i in range(end + 1):
        if i == end or not lines[i].strip():
            para = [j for j in range(start, i) if not moved[j]]
            folded = "\n".join(lines[j] for j in para).casefold()
            if para and any(f in folded for f in footers()):
                for j in para:
                    moved[j] = True
            start = i + 1
    new = [ln for ln, m in zip(lines, moved, strict=True) if not m]
    out = [ln for ln, m in zip(lines, moved, strict=True) if m]
    return tidy("\n".join(new)), tidy("\n".join(out))


def prepare(parts: Iterable[tuple[str, str]]) -> str:
    """The prepared body from its parts, each (text, text already known to be quoted), in order;
    read up to `HEAD_CHARS` characters in all."""
    new: list[str] = []
    moved: list[str] = []
    left = HEAD_CHARS
    for text, quoted in parts:
        if left <= 0:
            break
        head = text[:left]
        left -= len(head)
        n, m = split(normalize(head))
        new.append(n)
        moved.append(m)
        if quoted and left > 0:
            q = quoted[:left]
            left -= len(q)
            moved.append(tidy(normalize(q)))
    first = "\n\n".join(t for t in new if t)
    rest = "\n\n".join(t for t in moved if t)
    if first and rest:
        return f"{first}\n\n{MOVED_MARK}\n\n{rest}"
    return first or rest


def tidy(text: str) -> str:
    """Runs of spaces as one, each line trimmed, runs of blank lines as one blank line."""
    out: list[str] = []
    blank = False
    for raw in text.split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        if not line:
            blank = bool(out)
            continue
        if blank:
            out.append("")
            blank = False
        out.append(line)
    return "\n".join(out)
