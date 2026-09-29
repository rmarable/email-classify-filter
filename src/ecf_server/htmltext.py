"""HTML to text for the fact scan and excerpts (SPEC §5.1 step 4, §8.5).

Two texts come out of one pass: the **full** text (everything a parser can see, hidden text
included) and the **visible** text (hidden elements dropped). Triggers run over both, so text hidden
from the reader still counts (§8.5). Hidden means: `display:none`, `visibility:hidden`, a zero
font size or opacity, the `hidden` attribute, and `<script>`, `<style>`, `<head>`, `<template>`
and comments. Colour tricks (white on white) are not detected; the full text still carries them.
Embedded `data:` URIs are removed before the size is measured (they carry no readable text).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                  "param", "source", "track", "wbr"})  # fmt: skip
NEVER_TEXT = frozenset({"script", "style", "head", "template", "noscript", "title"})
BLOCK = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote",
     "section", "article", "header", "footer", "ul", "ol", "hr"}
)  # fmt: skip
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in BLOCK:
            self._newline()
        if tag in VOID:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        hidden = "hidden" in a or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        no_text = tag in NEVER_TEXT
        self._stack.append((tag, hidden, no_text))
        self._hidden += hidden
        self._no_text += no_text

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCK:
            self._newline()
        # close up to the matching tag, tolerating unclosed inner tags
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for _t, hidden, no_text in self._stack[i:]:
                    self._hidden -= hidden
                    self._no_text -= no_text
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._no_text:
            return
        self.full.append(data)
        if not self._hidden:
            self.visible.append(data)

    def _newline(self) -> None:
        self.full.append("\n")
        if not self._hidden:
            self.visible.append("\n")


def html_to_text(html: str) -> tuple[str, str]:
    """(full, visible) text of an HTML document, whitespace tidied."""
    w = _Walker()
    w.feed(strip_data_uris(html))
    w.close()
    return tidy("".join(w.full)), tidy("".join(w.visible))


def tidy(text: str) -> str:
    """Collapse runs of spaces and blank lines; trim each line."""
    lines = [re.sub(r"[ \t\f\v\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()
