"""A typed facade over macOS LocalAuthentication (PyObjC), for step-up (SPEC §9.6; SPEC §17.2: one
facade per untyped library).

Behavior relied on (V1.2 real-service test 0b, 2026-09-29, §21.1; macOS 27.0, PyObjC 12.2.2):
`evaluatePolicy_localizedReason_reply_` works from a launchd LaunchAgent without
`ProcessType=Interactive`; the reply block runs on a background thread and needs no run loop;
with no action there is no reply, and `invalidate` then replies error -9 (app cancel); Cancel
replies -2; "Use Password..." succeeds under `LAPolicyDeviceOwnerAuthentication`. The API doesn't
say whether Touch ID or the password was used.

Each request uses a fresh context with a reuse duration of 0, so an earlier success is never
reused. Error codes are Apple's `LAError` values; the ones not seen in test 0b are unverified.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from typing import Any, Literal

Outcome = Literal["verified", "declined", "timeout", "unavailable", "failed"]

_USER_CANCEL = -2
_SYSTEM_CANCEL = -4
_APP_CANCEL = -9
_UNAVAILABLE = frozenset({-5, -6, -7, -8, -1004})  # no passcode, no/not enrolled/locked, headless


def _real_context() -> Any:
    la: Any = importlib.import_module("LocalAuthentication")
    ctx = la.LAContext.alloc().init()
    ctx.setTouchIDAuthenticationAllowableReuseDuration_(0)
    return ctx


def _owner_policy() -> int:
    la: Any = importlib.import_module("LocalAuthentication")
    return int(la.LAPolicyDeviceOwnerAuthentication)


def evaluate(
    reason: str,
    timeout_s: float,
    *,
    context: Callable[[], Any] = _real_context,
    policy: Callable[[], int] = _owner_policy,
) -> Outcome:
    """Ask for Touch ID or the login password. `reason` is shown in the dialog. Never raises for
    an authentication result; a missing framework raises ImportError."""
    ctx = context()
    done = threading.Event()
    result: dict[str, Any] = {}

    def reply(success: bool, error: Any) -> None:
        result["success"] = bool(success)
        result["code"] = int(error.code()) if error is not None else None
        done.set()

    ctx.evaluatePolicy_localizedReason_reply_(policy(), reason, reply)
    if not done.wait(timeout_s):
        ctx.invalidate()  # withdraws the dialog; the reply then says -9
        done.wait(5)
        return "timeout"
    return _outcome(result.get("success", False), result.get("code"))


def _outcome(success: bool, code: int | None) -> Outcome:
    if success:
        return "verified"
    if code in (_USER_CANCEL, _SYSTEM_CANCEL):
        return "declined"
    if code == _APP_CANCEL:
        return "timeout"
    if code in _UNAVAILABLE:
        return "unavailable"
    return "failed"
