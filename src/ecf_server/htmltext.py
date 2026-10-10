"""HTML to text for the fact scan and excerpts (SPEC §5.1 step 4, §8.5).

Two texts come out of one pass: the **full** text (everything a parser can see, hidden text
included) and the **visible** text (hidden elements dropped). Triggers run over both, so text hidden
from the reader still counts (§8.5). Hidden means: `display:none`, `visibility:hidden`, a zero
font size or opacity, the `hidden` attribute, and `<script>`, `<style>`, `<head>`, `<template>`
and comments. Colour tricks (white on white) are not detected; the full text still carries them.
Embedded `data:` URIs are removed before the size is measured (they carry no readable text).

The same pass counts links and notes whether a visible anchor's text names a different site than
its href (`link_text_mismatch`; pre-classifier plan §5 C, P22). Recorded for measurement only.
Bounded: the innermost anchor only, the href cut to 2 KB and the anchor text to 200 characters
before they are read, and nothing recorded after `LINK_CAP` anchors (the count saturates). Only
a count and a boolean come out, never a URL or domain.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urlsplit

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                  "param", "source", "track", "wbr"})  # fmt: skip
NEVER_TEXT = frozenset({"script", "style", "head", "template", "noscript", "title"})
BLOCK = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote",
     "section", "article", "header", "footer", "ul", "ol", "hr"}
)  # fmt: skip
# Cells and similar: a space, so "IBAN</td><td>DE89..." doesn't glue a keyword to the next cell's
# text and defeat whole-word matching (V1.1 review, 2026-09-29).
CELL = frozenset({"td", "th", "dt", "dd", "caption", "label", "option", "figcaption"})
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])"
    r"|opacity\s*:\s*0(?![.\d]*[1-9])|max-height\s*:\s*0(?![.\d]*[1-9])",
    re.I,
)
# Only inside a quoted attribute value or CSS url(...), where embedded images live, so body text
# written to look like a data URI is still scanned.
_DATA_URI = re.compile(
    r"(?<=[\"'(])data:[a-z0-9.+/-]*(?:;[a-z0-9=.+-]*)*;base64,[a-z0-9+/=\s]*(?=[\"')])", re.I
)


LINK_CAP = 500  # links counted per text; anchors after this aren't read (P22)
HREF_CHARS = 2048
ANCHOR_TEXT_CHARS = 200
# Common top-level domains: a bare name in anchor text counts as a site only with one of these
# (so "Node.js" isn't one); a scheme or `www.` always counts. Shared with trigger 7's display
# names.
COMMON_TLDS = frozenset(
    {"com", "net", "org", "co", "io", "us", "uk", "de", "fr", "ca", "au", "nl", "es", "it",
     "info", "biz", "gov", "edu", "app", "dev", "ai", "me", "online", "site", "xyz"}
)  # fmt: skip
# Second-level labels under a country code that are registries, not sites (`acme.co.uk`): no
# public-suffix list is shipped, so the site is approximate.
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"})
_TEXT_SITE = re.compile(
    r"(?<![\w@.-])(https?://|www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})"
    r"(?![\w-])"
)
_URL = re.compile(r"(?<![\w-])https?://", re.I)


class Links(NamedTuple):
    number: int = 0  # saturates at LINK_CAP
    text_mismatch: bool = False


def site(host: str) -> str:
    """The registrable part of a host name, approximately: the last two labels, or three under
    a country code's registry label (`co.uk`)."""
    labels = host.lower().rstrip(".").split(".")
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-n:])


def text_site(text: str) -> str | None:
    """The first site named in anchor text: a URL, a `www.` name, or a bare domain with a
    common top-level domain."""
    for m in _TEXT_SITE.finditer(text.casefold()):
        if m.group(1) or m.group(2).rsplit(".", 1)[-1] in COMMON_TLDS:
            return site(m.group(2))
    return None


def href_site(href: str) -> str | None:
    """The site of an http(s) or scheme-relative href, None for anything else."""
    try:
        parts = urlsplit(href[:HREF_CHARS].strip())
        host = parts.hostname
    except ValueError:  # a malformed IPv6 literal
        return None
    if parts.scheme.lower() not in ("http", "https", "") or not host:
        return None
    return site(host)


def plain_links(text: str) -> Links:
    """Links in plain text: http(s) URLs, counted up to LINK_CAP."""
    n = 0
    for _ in _URL.finditer(text):
        n += 1
        if n >= LINK_CAP:
            break
    return Links(n)


def strip_data_uris(html: str) -> str:
    return _DATA_URI.sub("data:", html)


class _Walker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.full: list[str] = []
        self.visible: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []  # (tag, hidden, no_text)
        self._hidden = 0
        self._no_text = 0
        self._open: dict[str, int] = {}  # open elements per tag: end tags matching none are O(1)
        self.links = 0
        self.link_text_mismatch = False
        self._anchor: str | None = None  # the open anchor's href site ("" when it has none)
        self._anchor_text: list[str] = []
        self._anchor_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in BLOCK:
            self._newline()
        elif tag in CELL:
            self._space()
        if tag in VOID:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        hidden = "hidden" in a or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        if tag == "a":
            self._end_anchor()  # an anchor inside another: only the innermost is read
            if "href" in a and self.links < LINK_CAP:
                self.links += 1
                self._anchor = "" if self._hidden or hidden else (href_site(a["href"]) or "")
        no_text = tag in NEVER_TEXT
        self._stack.append((tag, hidden, no_text))
        self._open[tag] = self._open.get(tag, 0) + 1
        self._hidden += hidden
        self._no_text += no_text

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a":
            self._end_anchor()
        if tag in BLOCK:
            self._newline()
        elif tag in CELL:
            self._space()
        if not self._open.get(tag):  # nothing to close: without this check, `<b>`*n `</x>`*n
            return  # scanned the whole stack n times (quadratic; V1.1 review, 2026-09-29)
        # close up to the matching tag, tolerating unclosed inner tags; each element is popped
        # once, so the scans add up to the number of start tags
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for t, hidden, no_text in self._stack[i:]:
                    self._hidden -= hidden
                    self._no_text -= no_text
                    self._open[t] -= 1
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._no_text:
            return
        self.full.append(data)
        if not self._hidden:
            self.visible.append(data)
            if self._anchor and self._anchor_chars < ANCHOR_TEXT_CHARS:
                piece = data[: ANCHOR_TEXT_CHARS - self._anchor_chars]
                self._anchor_text.append(piece)
                self._anchor_chars += len(piece)

    def close(self) -> None:
        super().close()
        self._end_anchor()

    def _end_anchor(self) -> None:
        if self._anchor:
            named = text_site("".join(self._anchor_text))
            if named is not None and named != self._anchor:
                self.link_text_mismatch = True
        self._anchor = None
        self._anchor_text = []
        self._anchor_chars = 0

    def _space(self) -> None:
        self.full.append(" ")
        if not self._hidden:
            self.visible.append(" ")

    def _newline(self) -> None:
        self.full.append("\n")
        if not self._hidden:
            self.visible.append("\n")


class HtmlText(NamedTuple):
    full: str
    visible: str
    links: Links


def html_text(html: str) -> HtmlText:
    """The full and visible text of an HTML document, whitespace tidied, and its links."""
    w = _Walker()
    w.feed(strip_data_uris(html))
    w.close()
    links = Links(w.links, w.link_text_mismatch)
    return HtmlText(tidy("".join(w.full)), tidy("".join(w.visible)), links)


def html_to_text(html: str) -> tuple[str, str]:
    """(full, visible) text of an HTML document, whitespace tidied."""
    t = html_text(html)
    return t.full, t.visible


def tidy(text: str) -> str:
    """Collapse runs of spaces and blank lines; trim each line."""
    lines = [re.sub(r"[ \t\f\v\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()
