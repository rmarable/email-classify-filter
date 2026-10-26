"""Excerpt preparation (pre-classifier plan step 10, §6 B; SPEC §5.1 step 4).

The classifier and actor excerpts are cut from the prepared body, not from the first text part:

- **Body:** every text/plain body part, joined in order; the visible HTML text only when the
  message has no plain part (S10). Attachments never count, except the text of an attached
  message (`message/rfc822`), which mail clients show inline: it counts as quoted.
- **Bound** (P22): preparation reads the first `HEAD_CHARS` characters of the body, counted again
  after normalizing (NFKC can expand a character up to 18 times; review G7); the excerpts are at
  most `message.ACTOR_CHARS` long, so nothing they could show is lost.
- **Cleanup** (P18): `skeleton.normalize` (NFKC, format characters removed) before anything else,
  so a zero-width character can't split an injection phrase past `redact_injection`; then runs of
  spaces become one and runs of blank lines one blank line (kept: redaction works per paragraph).
- **Marked, never moved or deleted** (P4; pre-gate review G1, 2026-10-11): quoted replies (`>`
  lines; a line `On … wrote:`, `-----Original Message-----`, a forwarded-message line, or `From:`
  with `Sent:` or `Date:` within the next four lines, each starting a quote that runs to the end;
  the HTML quote containers `htmltext` marks), the signature (from a `--` line to the quote or the
  end) and footer paragraphs (a phrase from `keywords.yaml`'s `excerpt_footers`) are put behind
  `MOVED_MARK` only when no new text follows them, in that part or a later one. The text keeps its
  order, so nothing the sender writes can push other text past the excerpt's cut; a quote with new
  text after it stays where it is, unmarked. The sender can write any of these markers, and the
  models' instructions say so; a sender's line that starts like ecf's own lines (`[ecf`, `[text
  removed by ecf`) is prefixed so it can't pass for one (review G5).

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

VERSION = 3  # joins the prompt digests (P18): a change here is a new gate (2: review G1, G5-G7;
# 3: the classifier's window, 4,000 from 1,500, OD-558)
HEAD_CHARS = 256 * 1024  # operator decision 2026-10-11 (plan §6 B: 64-256 KB)
MARKER_CHARS = 400  # a longer line is never a quote marker
MARKER_LOOKAHEAD = 4  # lines after `From:` searched for `Sent:` or `Date:`
MOVED_MARK = ("[ecf: quoted replies, signatures and footers start below this line; the sender can"
              " write text that looks like any of these]")  # fmt: skip
_OURS = re.compile(r"^[ \t]*(?=\[(?:ecf|text removed by ecf))", re.I | re.M)  # ecf's own lines
FORGED = "(sender's text) "  # put before a sender's line that starts like one of ecf's

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
    last = max((i for i, ln in enumerate(lines) if not moved[i] and ln.strip()), default=-1)
    for i in range(last):  # only a block with no new text after it is marked (review G1)
        moved[i] = False
    new = [ln for ln, m in zip(lines, moved, strict=True) if not m]
    out = [ln for ln, m in zip(lines, moved, strict=True) if m]
    return tidy("\n".join(new)), tidy("\n".join(out))


def prepare(parts: Iterable[tuple[str, str]]) -> str:
    """The prepared body from its parts, each (text, text already known to be quoted, which
    follows it), in order; read up to `HEAD_CHARS` characters in all. The text keeps its order:
    `MOVED_MARK` goes before the quoted text that has no new text after it (review G1)."""
    segments: list[tuple[bool, str]] = []  # (quoted, text) in the body's order
    left = HEAD_CHARS
    for text, quoted in parts:
        for piece, known in ((text, False), (quoted, True)):
            if left <= 0 or not piece:
                continue
            norm = _neutral(normalize(piece[:left]))[:left]
            left -= len(norm)
            n, m = ("", tidy(norm)) if known else split(norm)
            segments += [(False, n), (True, m)]
    segments = [(q, t) for q, t in segments if t]
    last = max((i for i, (q, _) in enumerate(segments) if not q), default=-1)
    first = "\n\n".join(t for _, t in segments[: last + 1])
    rest = "\n\n".join(t for _, t in segments[last + 1 :])
    if first and rest:
        return f"{first}\n\n{MOVED_MARK}\n\n{rest}"
    return first or rest


def _neutral(text: str) -> str:
    """A sender's line that starts like ecf's own (`[ecf`, `[text removed by ecf`), prefixed."""
    return _OURS.sub(FORGED, text)


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
