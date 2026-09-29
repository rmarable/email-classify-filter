"""The Stepper port and its adapters (V1.2 step 1). No test here shows a real Touch ID dialog: the
LocalAuthentication context is scripted, replying from another thread as macOS does (§21.1)."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

from ecf_server import _localauth, stepper
from ecf_server.stepper import FakeStepper, MacStepper, PamStepper


class _Err:
    def __init__(self, code: int) -> None:
        self._code = code

    def code(self) -> int:
        return self._code


class _Ctx:
    """Replies on its own thread after `delay`, or never; `invalidate` replies -9."""

    def __init__(self, success: bool, code: int | None, delay: float = 0.01) -> None:
        self.success, self.code, self.delay = success, code, delay
        self.invalidated = False
        self.reasons: list[str] = []
        self._reply: Any = None

    def evaluatePolicy_localizedReason_reply_(self, policy: int, reason: str, reply: Any) -> None:
        del policy
        self.reasons.append(reason)
        self._reply = reply
        if self.delay >= 0:

            def later() -> None:
                time.sleep(self.delay)
                reply(self.success, _Err(self.code) if self.code is not None else None)

            threading.Thread(target=later, daemon=True).start()

    def invalidate(self) -> None:
        self.invalidated = True
        if self._reply is not None:
            self._reply(False, _Err(-9))


def _evaluate(ctx: _Ctx, timeout: float = 2.0) -> str:
    return _localauth.evaluate("approve archive (code AB12)", timeout, context=lambda: ctx,
                               policy=lambda: 2)  # fmt: skip


@pytest.mark.parametrize(
    ("success", "code", "want"),
    [
        (True, None, "verified"),
        (False, -2, "declined"),  # Cancel (seen in test 0b)
        (False, -4, "declined"),  # the system cancelled (another app took focus)
        (False, -6, "unavailable"),  # no Touch ID and no fallback
        (False, -1, "failed"),  # wrong password or finger
        (False, -99, "failed"),  # anything unknown is a failure, never a success
    ],
)
def test_localauth_outcomes(success: bool, code: int | None, want: str) -> None:
    assert _evaluate(_Ctx(success, code)) == want


def test_localauth_timeout_withdraws_the_dialog() -> None:
    ctx = _Ctx(False, None, delay=-1)  # never answers, like an unattended Mac
    assert _evaluate(ctx, timeout=0.05) == "timeout"
    assert ctx.invalidated


def test_mac_stepper_passes_the_reason_and_ignores_passwords() -> None:
    seen: list[tuple[str, float]] = []

    def evaluate(reason: str, timeout: float) -> Any:
        seen.append((reason, timeout))
        return "verified"

    assert MacStepper(evaluate).verify("approve X (code AB12)", password="ignored") == "verified"
    assert seen == [("approve X (code AB12)", stepper.TIMEOUT_S)]


def test_one_authentication_at_a_time() -> None:
    """A second request waits for the first: no dialog appears beside a real one."""
    active = 0
    most = 0
    guard = threading.Lock()

    def evaluate(reason: str, timeout: float) -> Any:
        nonlocal active, most
        with guard:
            active += 1
            most = max(most, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return "verified"

    s = MacStepper(evaluate)
    threads = [threading.Thread(target=s.verify, args=(f"r{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == 1


def test_pam_stepper_needs_a_password_and_checks_the_running_user() -> None:
    import getpass  # noqa: PLC0415

    calls: list[tuple[str, str]] = []

    def check(user: str, password: str) -> bool:
        calls.append((user, password))
        return password == "right"

    s = PamStepper(check)
    assert s.needs_password
    assert s.verify("r") == "failed" and calls == []  # no password: never asks PAM
    assert s.verify("r", password="wrong") == "failed"
    assert s.verify("r", password="right") == "verified"
    assert {u for u, _ in calls} == {getpass.getuser()}


def test_fake_stepper_scripts_outcomes() -> None:
    f = FakeStepper(["declined", "verified"])
    assert [f.verify("a"), f.verify("b"), f.verify("c")] == ["declined", "verified", "verified"]
    assert f.reasons == ["a", "b", "c"]


def test_host_stepper_matches_the_platform() -> None:
    s = stepper.host_stepper()
    if sys.platform == "darwin":
        assert isinstance(s, MacStepper)
    elif sys.platform.startswith("linux"):
        assert isinstance(s, PamStepper)
