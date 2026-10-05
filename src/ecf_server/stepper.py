"""The Stepper port (SPEC §3.3, §9.6): OS authentication for step-up, always run by the service
(OD-073; a result reported by a client is never trusted). The port, the macOS and Linux adapters
and the fake; nonces, binding and the routes are in `stepup.py`.

- **macOS:** LocalAuthentication (Touch ID or the login password) from the LaunchAgent; the reason
  text in the dialog names the action, recipient, address and a short code the CLI also prints.
- **Linux:** PAM with the password the CLI sends over the 0600 socket (unverified until M5);
  PAM only in V1.2; polkit on desktops arrives with M5 (OD-224).

One authentication at a time across the service: a second request waits for the first to end, so
a same-user process can't slip its own dialog in beside a real one (security review of the V1.2
plan, 2026-09-29). An authentication times out after `TIMEOUT_S` (a locked screen or an unattended
Mac), which withdraws the dialog.
"""

from __future__ import annotations

import getpass
import sys
import threading
from collections.abc import Callable
from typing import Protocol

from ecf_server import _localauth, _pam

Outcome = _localauth.Outcome
TIMEOUT_S = 60.0
_ONE_AT_A_TIME = threading.Lock()


class Stepper(Protocol):
    name: str
    needs_password: bool  # the client must collect the password (Linux PAM)

    def verify(self, reason: str, *, password: str | None = None) -> Outcome: ...


class MacStepper:
    name = "localauthentication"
    needs_password = False

    def __init__(self, evaluate: Callable[[str, float], Outcome] = _localauth.evaluate) -> None:
        self._evaluate = evaluate

    def verify(self, reason: str, *, password: str | None = None) -> Outcome:
        del password  # macOS asks for it in its own dialog
        with _ONE_AT_A_TIME:
            return self._evaluate(reason, TIMEOUT_S)


class PamStepper:
    """Unverified until M5."""

    name = "pam"
    needs_password = True

    def __init__(self, check: Callable[[str, str], bool] = _pam.authenticate) -> None:
        self._check = check

    def verify(self, reason: str, *, password: str | None = None) -> Outcome:
        del reason  # PAM shows no dialog; the CLI printed the reason before asking
        if not password:
            return "failed"
        with _ONE_AT_A_TIME:
            return "verified" if self._check(getpass.getuser(), password) else "failed"


class FakeStepper:
    """Scripted outcomes (tests); records each reason it was asked with."""

    name = "fake"
    needs_password = False

    def __init__(self, outcomes: list[Outcome] | None = None) -> None:
        self.outcomes: list[Outcome] = list(outcomes) if outcomes else ["verified"]
        self.reasons: list[str] = []

    def verify(self, reason: str, *, password: str | None = None) -> Outcome:
        del password
        with _ONE_AT_A_TIME:
            self.reasons.append(reason)
            return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def host_stepper() -> Stepper | None:
    """The platform's stepper, or None where step-up can't run (then step-up actions are
    refused, §9.6)."""
    if sys.platform == "darwin":
        return MacStepper()
    if sys.platform.startswith("linux"):
        return PamStepper()
    return None
