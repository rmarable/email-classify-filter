"""The Notifier port (SPEC §3.3, §11.8): desktop notifications. V1.1 step 13b (OD-190).

macOS: `osascript` with the text passed as arguments to `on run argv`, never pasted into the
script, so nothing in the text can become AppleScript (§11.8; it shows as Script Editor, unverified,
confirm in V1.2). Linux desktops: `notify-send`. Headless or unknown: none. Notifications never
carry email-derived text: callers pass fixed titles, address IDs and host names only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Protocol

from ecf_server.log_bridge import log

TIMEOUT_S = 10


class Notifier(Protocol):
    name: str

    def notify(self, title: str, body: str) -> None: ...


class NullNotifier:
    name = "none"

    def notify(self, title: str, body: str) -> None:
        del title, body


class MacNotifier:
    name = "macos"

    def notify(self, title: str, body: str) -> None:
        _run(
            [
                "/usr/bin/osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e",
                "end run",
                title,
                body,
            ]
        )


class LinuxNotifier:
    name = "notify-send"

    def __init__(self, path: str) -> None:
        self.path = path

    def notify(self, title: str, body: str) -> None:
        _run([self.path, "--app-name=ecf", title, body])


class FakeNotifier:
    """Records notifications (tests and `ecf-server dev`)."""

    name = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


def host_notifier() -> Notifier:
    if sys.platform == "darwin":
        return MacNotifier()
    path = shutil.which("notify-send")
    return LinuxNotifier(path) if path else NullNotifier()


def _run(args: list[str]) -> None:
    """A notification that fails is logged, never raised: it must not break a check."""
    try:
        done = subprocess.run(args, capture_output=True, timeout=TIMEOUT_S, check=False)  # noqa: S603 - fixed program, text as arguments
        if done.returncode != 0:
            log.warning("notify.failed", code=done.returncode)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("notify.failed", error_type=type(exc).__name__)
