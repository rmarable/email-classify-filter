"""The client side of step-up (SPEC §9.6; V1.2 step 2). The client never decides anything: it
asks the service for a nonce, prints the dialog text and short code the service computed, sends
the password only where the service's check needs one (Linux PAM), and retries the action with the
verified nonce. The service performs the check and binds it to the exact change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ecf.client import LocalClient
from ecf.errors import StepupFailedError, StepupRequiredError
from ecf.prompts import hidden

VERIFY_TIMEOUT_S = 90.0  # the service's check waits up to 60 s for Touch ID or the password

REFUSALS = {
    "declined": "you declined",
    "timeout": "no answer within a minute (a locked screen or nobody at the Mac)",
    "unavailable": "step-up isn't available here (no Touch ID or password check)",
    "failed": "authentication failed",
}


def step_up(
    c: LocalClient,
    purpose: str,
    target: dict[str, Any],
    *,
    echo: Callable[[str], None] = print,
    ask: Callable[[str], str] | None = None,
) -> str:
    """Run one step-up; returns the verified nonce ID, or raises StepupFailedError."""
    n = c.request("POST", "/v1/stepup/nonces", {"purpose": purpose, "target": target})
    echo(f"Step-up: {n['prompt_text']}")
    echo(f"The dialog should show the same code: {n['code']}")
    body: dict[str, Any] = {}
    if n["needs_password"]:
        prompt = "Your login password (hidden): "
        body["password"] = hidden(prompt, ask=ask) if ask else hidden(prompt)
    r = c.request("POST", f"/v1/stepup/{n['nonce_id']}/verify", body, timeout=VERIFY_TIMEOUT_S)
    if not r["verified"]:
        raise StepupFailedError(f"step-up refused: {REFUSALS.get(r['outcome'], r['outcome'])}")
    return str(n["nonce_id"])


def with_step_up[T](
    c: LocalClient,
    action: Callable[[str | None], T],
    *,
    echo: Callable[[str], None] = print,
) -> T:
    """Call `action(None)`; if the service answers that it needs step-up, run it for exactly the
    purpose and target the service named, then call `action(nonce_id)` once more."""
    try:
        return action(None)
    except StepupRequiredError as exc:
        nonce = step_up(c, str(exc.extra["purpose"]), dict(exc.extra["target"]), echo=echo)
        return action(nonce)
