"""Hidden prompts for secrets (SPEC §3.2). They need a real terminal: without one (a pipe, or a tool
that captures input such as Claude Code's `!` commands) input can't be hidden, so ecf refuses
instead of echoing the secret (observed 2026-09-28)."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from ecf.errors import InvalidInputError

NO_TERMINAL = (
    "this command asks for a secret and needs a real terminal to hide what you type; "
    "run it in Terminal (not through a tool that captures input, such as Claude Code's `!`)"
)


def require_terminal(stdin: TextIO | None = None) -> None:
    """Call before asking anything else, so a command that will need a secret fails at once."""
    if not (stdin or sys.stdin).isatty():
        raise InvalidInputError(NO_TERMINAL)


def hidden(
    prompt: str,
    *,
    confirm: bool = False,
    stdin: TextIO | None = None,
    ask: Callable[[str], str] = getpass.getpass,
) -> str:
    require_terminal(stdin)
    value = ask(prompt)
    if not value:
        raise InvalidInputError("nothing entered")
    if confirm and ask("Again, to confirm: ") != value:
        raise InvalidInputError("the two entries don't match")
    return value
