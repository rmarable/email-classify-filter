"""Text from email, made safe to show outside Slack: in a terminal and in the step-up dialog.

Subjects and senders are decoded headers the sender controls (RFC 2047 can carry any code point),
so control and format characters (escape sequences, right-to-left overrides, zero-width marks) are
removed before they reach a terminal or the OS authentication dialog (V1.2 review, 2026-09-30).
Slack cards have their own cleaner (`ecf_server.slack_render.clean`).
"""

from __future__ import annotations

import unicodedata
from typing import Any

_DROP = ("Cc", "Cf", "Co", "Cs")


def plain(text: Any) -> str:
    """No control or format characters except line breaks and tabs."""
    return "".join(ch for ch in str(text) if ch in "\n\t" or unicodedata.category(ch) not in _DROP)


def one_line(text: Any, limit: int = 400) -> str:
    """`plain`, on one line: line breaks and runs of spaces become one space; capped."""
    flat = " ".join(plain(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
