"""A typed facade over the one PyObjC Security call ecf needs (OD-163).

`SecKeychainSetUserInteractionAllowed(False)` makes Keychain reads the process isn't trusted for
fail at once instead of showing a dialog (tested 2026-09-27: 0.02 s, error -25293). Apple deprecated
it in macOS 10.10; it is still exported in the macOS 27.0 SDK, and no supported call does the same
for ecf's legacy keychain items (ADR 0001). Turning prompts off raises KeychainPromptsError when the
call is missing, fails, or the setting still reads back as on, and the service then runs without
secrets (OD-564). A macOS whose set and get calls are both no-op stubs would get past this.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

from ecf_server.secretstore import SecretStoreNeedsYouError

PROMPTS_GONE = (
    "macOS no longer lets ecf turn Keychain prompts off, so the service won't read the Keychain"
    " unattended; check for an ecf update"
)


class KeychainPromptsError(SecretStoreNeedsYouError):
    """Keychain prompts couldn't be turned off; the service must not read the Keychain."""


def _import_security() -> Any:
    return importlib.import_module("Security")


def _security() -> Any:
    if sys.platform != "darwin":
        raise RuntimeError("Keychain interaction control is macOS-only")
    try:
        return _import_security()
    except Exception:  # missing or broken PyObjC: treated like a missing call
        return None


def set_interaction_allowed(allowed: bool) -> None:
    call = getattr(_security(), "SecKeychainSetUserInteractionAllowed", None)
    if call is None:
        if allowed:
            return  # prompts are on by default (a read without the call showed one, 2026-10-27)
        raise KeychainPromptsError(PROMPTS_GONE)
    try:
        status = call(allowed)
    except Exception as exc:  # a changed PyObjC bridge: treated like a failed call
        raise KeychainPromptsError(f"turning Keychain prompts {'on' if allowed else 'off'}"
                                   f" failed: {type(exc).__name__}") from exc  # fmt: skip
    if status != 0:
        raise KeychainPromptsError(
            f"turning Keychain prompts {'on' if allowed else 'off'} failed: {status}"
        )
    if not allowed and interaction_allowed():  # a stub that answers noErr and does nothing
        raise KeychainPromptsError(PROMPTS_GONE)


def interaction_allowed() -> bool:
    call = getattr(_security(), "SecKeychainGetUserInteractionAllowed", None)
    if call is None:
        raise KeychainPromptsError(PROMPTS_GONE)
    try:
        status, allowed = call(None)
    except Exception as exc:
        raise KeychainPromptsError(
            f"reading the Keychain prompt setting failed: {type(exc).__name__}"
        ) from exc
    if status != 0:
        raise KeychainPromptsError(f"reading the Keychain prompt setting failed: {status}")
    return bool(allowed)
