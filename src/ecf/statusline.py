"""The status line of an `ecf claude` session (SPEC §13.4; V1.4 step 6): `python -m ecf.statusline
<socket>`, run by Claude Code with the session's JSON on stdin.

It sends the service only the plan-usage numbers (`rate_limits.five_hour` and `seven_day`:
`used_percentage` and `resets_at`; Pro and Max plans only, verified 2026-09-27 and seen on Max
2026-10-02) with the session's WORK token, and prints a short line; while an eval runs, the line
starts with the run's progress from the service's reply (v2.2.0). Standard library only, so it
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


def _count(ev: dict[str, Any], key: str) -> int | None:
    v = ev.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def head(reply: Any) -> str:
    """`ecf eval <run> · N/M scored · K out` while an eval runs (the service's counts, never the
    session's), else `ecf review`."""
    ev: Any = cast(dict[str, Any], reply).get("eval") if isinstance(reply, dict) else None
    if not isinstance(ev, dict):
        return "ecf review"
    e = cast(dict[str, Any], ev)
    run, scored, total, out = (e.get("run_id"), _count(e, "scored"), _count(e, "total"),
                               _count(e, "in_progress"))  # fmt: skip
    if not (isinstance(run, str) and run.isalnum() and len(run) <= 16) or None in (
            scored, total, out):  # fmt: skip
        return "ecf review"
    parts = [f"ecf eval {run}", f"{scored}/{total} scored", f"{out} out"]
    if repeats := _count(e, "repeats_left"):
        parts.append(f"{repeats} repeats left")
    return " · ".join(parts)


def line(u: dict[str, Any], reply: Any = None) -> str:
    parts = [f"{label} {u[k]:.0f}%" for k, label in (("five_hour", "5h"), ("seven_day", "7d"))
             if k in u]  # fmt: skip
    return " · ".join([head(reply), *parts])


def send(sock: str, token: str, body: dict[str, Any]) -> Any:
    """The service's reply: `recorded` and, while an eval runs, its progress."""
    conn = _UnixConnection(sock)
    try:
        conn.request("POST", "/v1/statusline", body=json.dumps(body),
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"})  # fmt: skip
        return json.loads(conn.getresponse().read() or b"null")
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
    reply: Any = None
    if token and args:  # also without plan numbers (Team, Enterprise): an eval's progress
        with suppress(OSError, http.client.HTTPException, ValueError):  # down, slow or garbled
            reply = send(args[0], token, u)
    sys.stdout.write(line(u, reply) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
