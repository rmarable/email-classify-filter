"""A typed facade over `python-pam` (Linux), for step-up (SPEC §9.6; §17.2: one facade per untyped
library). Unverified until V1.6's Linux real-service test.

python-pam 2.1.0, read from its wheel's source (2026-09-29): the module-level
`pam.authenticate(username, password, service='login', ...)` makes a fresh `PamAuthenticator` per
call (safe across threads), returns a bool, and prints nothing on failure by default. SPEC §17.5
notes it checks only the running user's password (unverified, V1.6). Whether distributions need a
service other than `login` is confirmed in V1.6. The password is passed through and never stored
or logged.
"""

from __future__ import annotations

import importlib
from typing import Any

SERVICE = "login"


def authenticate(user: str, password: str) -> bool:
    pam: Any = importlib.import_module("pam")
    return bool(pam.authenticate(user, password, service=SERVICE))
