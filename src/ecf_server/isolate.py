"""Every message is parsed and verified in a short-lived child process (SPEC §5.1; OD-195, extended
to all messages after the V1.1 review, 2026-09-29, OD-204).

Parsing and verifying a message over 16 MB holds several full copies of it (measured 2026-09-29: a
64.6 MB message peaked at 557 MB of Python memory, 871 MB of process growth), and Python keeps much
of that after the message is done (464 MB still resident). So the service fetches the message, then
hands it to `python -m ecf_server.isolate` through a pipe (never a file): the child parses it and
checks DKIM/DMARC, prints the result as JSON and exits, and its memory goes back to the system.
A child that crashes, times out or prints anything unexpected raises `IsolationError`, which counts
as a crash on that message like any other, so the quarantine rule (§5.1) still applies. Running
every message this way gives each one a hard time limit: no crafted email can stall the service's
single check thread (the V1.1 review found a quadratic HTML case; others may exist). A child costs
about 0.15 s (measured 2026-09-29).

The child opens its own connection to the service database for the DNS cache, with its own DNS
budget, which starts at what is left of the check's. Its output is JSON, never pickle, so a child
subverted by a crafted message can't run code in the service. Its stderr is discarded: a
traceback could quote message text.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ecf_server.message import Attachment, ParsedMessage, TextPart, parse
from ecf_server.senderauth import AuthOutcome, SigResult

# parse and DKIM on a 64.6 MB message took 7-10 s on this Mac (2026-09-29); a small one, 0.15 s
TIMEOUT_BASE_S = 30
TIMEOUT_PER_MB_S = 3
EXIT_ERROR = 3


def timeout_for(size: int) -> float:
    return TIMEOUT_BASE_S + TIMEOUT_PER_MB_S * size / (1024 * 1024)


# (raw, max_scan_bytes) -> parsed message and its sender-authentication outcome
Isolator = Callable[[bytes, int], tuple[ParsedMessage, AuthOutcome]]


class IsolationError(RuntimeError):
    """The child failed on this message; the reason names no message content."""


def subprocess_isolator(
    db_path: Path, *, dns_cap_s: int, dns_budget: Callable[[], float]
) -> Isolator:
    """`dns_budget` gives the seconds of the check's DNS budget left when a message starts."""

    def run(raw: bytes, max_scan_bytes: int) -> tuple[ParsedMessage, AuthOutcome]:
        args = [sys.executable, "-m", "ecf_server.isolate", str(db_path), str(max_scan_bytes)]
        args += [str(dns_cap_s), f"{dns_budget():.3f}"]
        limit = timeout_for(len(raw))
        try:
            done = subprocess.run(  # noqa: S603 - our own interpreter and module
                args, input=raw, capture_output=True, timeout=limit, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise IsolationError(f"child timed out after {limit:.0f} s") from exc
        if done.returncode != 0:
            raise IsolationError(f"child exited {done.returncode}: {_reason(done.stdout)}")
        try:
            return decode(json.loads(done.stdout))
        except (ValueError, KeyError, TypeError) as exc:
            raise IsolationError("child printed an unreadable result") from exc

    return run


def encode(parsed: ParsedMessage, auth: AuthOutcome) -> dict[str, Any]:
    return {"parsed": asdict(parsed), "auth": asdict(auth)}


def decode(data: dict[str, Any]) -> tuple[ParsedMessage, AuthOutcome]:
    p: dict[str, Any] = data["parsed"]
    parsed = ParsedMessage(
        message_id=p["message_id"],
        from_count=int(p["from_count"]),
        from_addr=p["from_addr"],
        from_name=str(p["from_name"]),
        reply_to=tuple(p["reply_to"]),
        to=tuple(p["to"]),
        cc=tuple(p["cc"]),
        subject=str(p["subject"]),
        headers={str(k): tuple(v) for k, v in p["headers"].items()},
        texts=tuple(TextPart(**t) for t in p["texts"]),
        attachments=tuple(Attachment(**a) for a in p["attachments"]),
        content_hash=str(p["content_hash"]),
        defects=int(p["defects"]),
        hash_version=int(p["hash_version"]),
        size=int(p["size"]),
        from_ambiguous=bool(p["from_ambiguous"]),
        headers_ambiguous=bool(p["headers_ambiguous"]),
    )
    a: dict[str, Any] = data["auth"]
    auth = AuthOutcome(
        result=a["result"],
        reason=str(a["reason"]),
        from_domain=a["from_domain"],
        policy=a["policy"],
        signatures=[SigResult(**s) for s in a["signatures"]],
        mime_headers_signed=a["mime_headers_signed"],
    )
    return parsed, auth


def _reason(stdout: bytes) -> str:
    try:
        return str(json.loads(stdout)["error"])[:100]
    except (ValueError, KeyError, TypeError):
        return "no reason given"


def main(argv: list[str]) -> int:
    """Child: raw message on stdin, JSON result on stdout."""
    from ecf_server import db, senderauth  # noqa: PLC0415 - the parent never needs these here
    from ecf_server.clock import SystemClock  # noqa: PLC0415
    from ecf_server.dnscache import DnsCache  # noqa: PLC0415

    db_path, max_scan_bytes, cap_s = Path(argv[0]), int(argv[1]), int(argv[2])
    budget_s = float(argv[3]) if len(argv) > 3 else None
    raw = sys.stdin.buffer.read()
    try:
        conn = db.connect(db_path)
        parsed = parse(raw, max_scan_bytes=max_scan_bytes)
        clock = SystemClock()
        dns = (
            DnsCache(conn, clock, cap_s=cap_s, budget_s=budget_s)
            if budget_s is not None
            else DnsCache(conn, clock, cap_s=cap_s)
        )
        auth = senderauth.evaluate(raw, parsed, dns)
        out = json.dumps(encode(parsed, auth))
    except Exception as exc:  # any failure: reported by type only, never by message
        sys.stdout.write(json.dumps({"error": type(exc).__name__}))
        return EXIT_ERROR
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
