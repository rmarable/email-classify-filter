"""A typed facade over the one PyObjC Security call ecf needs (OD-163).

`SecKeychainSetUserInteractionAllowed(False)` makes Keychain reads the process isn't trusted for
fail at once instead of showing a dialog (tested 2026-09-27: 0.02 s, error -25293). It is a legacy
Keychain API; if Apple removes it, ADR 0001 is revisited.
"""

from __future__ import annotations

import sys
from typing import Any


def _security() -> Any:
    if sys.platform != "darwin":
        raise RuntimeError("Keychain interaction control is macOS-only")
    import Security  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415

    return Security


def set_interaction_allowed(allowed: bool) -> None:
    status = _security().SecKeychainSetUserInteractionAllowed(allowed)
    if status != 0:
        raise OSError(f"SecKeychainSetUserInteractionAllowed failed: {status}")


def interaction_allowed() -> bool:
    status, allowed = _security().SecKeychainGetUserInteractionAllowed(None)
    if status != 0:
        raise OSError(f"SecKeychainGetUserInteractionAllowed failed: {status}")
    return bool(allowed)
