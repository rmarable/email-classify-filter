"""The status line of an `ecf claude` session (SPEC §13.4; V1.4 step 6): `python -m ecf.statusline
<socket>`, run by Claude Code with the session's JSON on stdin.

It sends the service only the plan-usage numbers (`rate_limits.five_hour` and `seven_day`:
`used_percentage` and `resets_at`; Pro and Max plans only, verified 2026-09-27 and seen on Max
2026-10-02) with the session's WORK token, and prints a short line. Standard library only, so it
starts fast; it never fails the status line (no answer within a second is skipped). That Claude
Code passes the session's environment (`ECF_PROFILE_TOKEN`) to the status-line command is
unverified, confirm in V1.4 step 13.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
from contextlib import suppress
from typing import Any, cast

TIMEOUT_S = 1.0


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("ecf", timeout=TIMEOUT_S)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT_S)
        s.connect(self._path)
        self.sock = s


def usage(data: Any) -> dict[str, Any]:
    """The numbers sent to the service; nothing else from the session's JSON."""
    out: dict[str, Any] = {}
    limits: Any = cast(dict[str, Any], data).get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return out
    for key in ("five_hour", "seven_day"):
        w: Any = cast(dict[str, Any], limits).get(key)
        if not isinstance(w, dict):
            continue
        window = cast(dict[str, Any], w)
        pct, resets = window.get("used_percentage"), window.get("resets_at")
        if isinstance(pct, int | float) and not isinstance(pct, bool):
            out[key] = pct
        if isinstance(resets, str | int | float) and not isinstance(resets, bool):
            out[f"{key}_resets_at"] = resets
    return out


def line(u: dict[str, Any]) -> str:
    parts = [f"{label} {u[k]:.0f}%" for k, label in (("five_hour", "5h"), ("seven_day", "7d"))
             if k in u]  # fmt: skip
    return " · ".join(["ecf review", *parts])


def send(sock: str, token: str, body: dict[str, Any]) -> None:
    conn = _UnixConnection(sock)
    try:
        conn.request("POST", "/v1/statusline", body=json.dumps(body),
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"})  # fmt: skip
        conn.getresponse().read()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        data: Any = json.load(sys.stdin)
    except ValueError:
        data = None
    u = usage(data)
    token = os.environ.get("ECF_PROFILE_TOKEN", "")
    if u and token and args:
        with suppress(OSError, http.client.HTTPException):  # the service is down or slow
            send(args[0], token, u)
    sys.stdout.write(line(u) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
