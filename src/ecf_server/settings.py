"""`ecf settings show|set` (SPEC §14; V1.2 step 10a).

A registry of the keys `settings set` may change: each with its scope (the install, an address, or
both), a parser that validates it, and whether it takes effect only when the service restarts.
Install values live in the settings table; address values in the address's overrides, which win.
Only keys something reads today are here. Keys SPEC lists that nothing reads yet are refused with
the milestone that brings them, and keys another command owns point there (member ID: `ecf slack
set-member`; alert routes: `ecf alerts set`; org domains and other security-relevant config:
`ecf config apply`). None of today's keys needs step-up (§9.6).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ecf.errors import InvalidInputError
from ecf_server import addresses, schedule
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

MB = 1_048_576
MAX_MB = 64  # OD-220
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HOURS = re.compile(
    r"\s*([a-z]{3})(?:-([a-z]{3}))?\s+(\d\d:\d\d)-(\d\d:\d\d)\s+(\S+)\s*", re.IGNORECASE
)


def _int(lo: int, hi: int) -> Callable[[str], int]:
    def parse(v: str) -> int:
        try:
            n = int(v)
        except ValueError:
            raise InvalidInputError(f"a whole number from {lo} to {hi}") from None
        if not lo <= n <= hi:
            raise InvalidInputError(f"a whole number from {lo} to {hi}")
        return n

    return parse


def _bool(v: str) -> bool:
    low = v.strip().lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    raise InvalidInputError("true or false")


def _choice(*options: str) -> Callable[[str], str]:
    def parse(v: str) -> str:
        if v not in options:
            raise InvalidInputError(" or ".join(options))
        return v

    return parse


def _megabytes(v: str) -> int:
    """1 to 64 MB: 64 MB is the largest size whose memory use was measured (§5.1, OD-195);
    raising the ceiling needs a new measurement first (operator decision 2026-09-30, OD-220)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mb|MB)?\s*", v)
    if not m or not 1 <= float(m.group(1)) <= MAX_MB:
        raise InvalidInputError(f"a size in MB from 1 to {MAX_MB} (1 MB is 1,048,576 bytes)")
    return round(float(m.group(1)) * MB)


def business_hours(v: str) -> dict[str, Any]:
    """`mon-fri 08:00-17:00 America/New_York` (days, a time range, an IANA zone)."""
    m = _HOURS.fullmatch(v)
    if not m:
        raise InvalidInputError("like: mon-fri 08:00-17:00 America/New_York")
    first, last = m.group(1).lower(), (m.group(2) or m.group(1)).lower()
    if first not in DAYS or last not in DAYS:
        raise InvalidInputError(f"days are {', '.join(DAYS)}")
    a, b = DAYS.index(first), DAYS.index(last)
    days = list(range(a, b + 1)) if a <= b else [*range(a, 7), *range(0, b + 1)]
    try:
        start, end = time.fromisoformat(m.group(3)), time.fromisoformat(m.group(4))
        ZoneInfo(m.group(5))
    except ValueError:
        raise InvalidInputError("times are HH:MM, 00:00 to 23:59") from None
    except ZoneInfoNotFoundError:
        raise InvalidInputError(f"not a time zone: {m.group(5)} (e.g. America/New_York)") from None
    if start >= end:
        raise InvalidInputError("the start must be before the end")
    return {"days": days, "start": m.group(3), "end": m.group(4), "tz": m.group(5)}


def _folder_name(v: str) -> str:
    """A folder `label_folder` copies suspicious and regulatory mail into (§8.3): any name the
    mailbox has except INBOX; checked against the mailbox's folders when it's used."""
    name = v.strip()
    if not name or len(name) > 200 or re.search(r"[\x00-\x1f\x7f]", name):
        raise InvalidInputError("a folder name")
    if name.upper() == "INBOX":
        raise InvalidInputError("INBOX isn't a label folder")
    return name


@dataclass(frozen=True)
class Key:
    name: str
    scope: str  # "install", "address" or "both"
    parse: Callable[[str], Any]
    default: Any
    restart: bool = False  # read when the service starts


_both = [
    Key("business_hours", "both", business_hours, schedule.DEFAULTS["business_hours"]),
    Key("mail_fetch_interval_workday", "both", _int(5, 120), 10),
    Key("mail_fetch_interval_offhours", "both", _int(5, 120), 30),
    Key("catch_up", "both", _choice("auto", "off"), "auto"),
    Key("catch_up_max_minutes", "both", _int(5, 240), "by hardware (30 or 60)"),
    Key("catch_up_cooldown_minutes", "both", _int(0, 120), 15),
    Key("catch_up_on_battery", "both", _bool, False),
    Key("escalations_per_hour", "both", _int(1, 200), 20),  # OD-035; fraud and regulator exempt
]
_install = [
    Key("notifications", "install", _choice("on", "off"), "on", restart=True),
    Key("deadman_offhours", "install", _bool, False),  # OD-219
    Key("stale_item_days", "install", _int(7, 365), 30),
    Key("max_per_check", "install", _int(1, 30), 6),  # minutes of IMAP and rules work (OD-228)
    Key("resident", "install", _bool, False),  # keep the local model loaded (§5.2)
    Key("review_sample_rate", "install", _int(0, 100), 10),  # percent, after the gate count
]
_address = [
    Key("max_message_bytes", "address", _megabytes, "64 MB high, 16 MB standard"),
    Key("max_scan_bytes_per_part", "address", _megabytes, 10 * MB),
    Key("approval_ttl_days", "address", _int(1, 60), 14),
    Key("approval_ttl_days_send", "address", _int(1, 60), 4),
    Key("label_folder", "address", _folder_name, None),  # §8.3 (Purelymail)
]
KEYS: dict[str, Key] = {k.name: k for k in (*_both, *_install, *_address)}
ELSEWHERE = {
    "slack_member_id": "change it with `ecf slack set-member` (step-up)",
    "alerts.routes": "change it with `ecf alerts set`",
    "org_domains": "change it with `ecf config apply` (step-up)",
    "log_retention_days": "change it with `ecf retention set` (step-up)",
    "install_role": "fixed when the install is created",
    "sensitivity": "change it with `ecf sensitivity set`",
    "stage": "change it with `ecf stage set`",
    "claude_queue_timeout": "arrives with presets B and C in V1.4 (OD-227)",
    "classifier_high_batch": "arrives with Claude on demand in V1.4",
    "export_schedule": "arrives with exports in V1.5",
    "export_dir": "arrives with exports in V1.5",
    "export_keep": "arrives with exports in V1.5",
    "max_sends_per_hour": "arrives with outbound in V1.5",
    "max_sends_per_day": "arrives with outbound in V1.5",
    "dns.doh_url": "not built yet",
    "claude_review_reminder_hours": "arrives with Claude on demand in V1.4",
    "alerts.email.monitored_address": "arrives with email alerts in V1.5 (OD-206)",
    "alerts.email.destination_address": "arrives with email alerts in V1.5 (OD-206)",
    "outbound": "change it with `ecf outbound enable|disable`, which arrive in V1.5",
    "preset": "chosen with `ecf address add`",
    "security_config_delay_minutes": "fixed at 0 in local mode (OD-074)",
    "sensitivity_downgrade_delay_minutes": "fixed at 0 in local mode",
    "trust_provider_authentication_results": "fixed: provider headers are never trusted in v1",
}


def key(name: str, address: str | None) -> Key:
    if name in ELSEWHERE:
        raise InvalidInputError(f"{name}: {ELSEWHERE[name]}")
    k = KEYS.get(name)
    if k is None:
        raise InvalidInputError(f"no setting {name!r}; see `ecf settings show`")
    if address and k.scope == "install":
        raise InvalidInputError(f"{name} is for the whole install, not one address")
    if not address and k.scope == "address":
        raise InvalidInputError(f"{name} is per address: add --address")
    return k


def get(conn: sqlite3.Connection, name: str, address_id: str | None = None) -> Any:
    """An address's override, else the install's value, else the default."""
    if address_id:
        row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                           (address_id,)).fetchone()  # fmt: skip
        overrides: dict[str, Any] = json.loads(row["overrides"]) if row else {}
        if name in overrides:
            return overrides[name]
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (name,)).fetchone()
    return json.loads(row[0]) if row else KEYS[name].default


def show(conn: sqlite3.Connection, address_id: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k in KEYS.values():
        if address_id and k.scope == "install":
            continue
        if not address_id and k.scope == "address":
            continue
        out.append({"key": k.name, "value": get(conn, k.name, address_id),
                    "default": k.default, "restart": k.restart})  # fmt: skip
    return out


def set_value(
    conn: sqlite3.Connection, clock: Clock, name: str, raw: str, *, address: str | None, actor: str
) -> dict[str, Any]:
    k = key(name, address)
    value = k.parse(raw)
    now = to_ts(clock.now())
    aid = addresses.get_address(conn, address)["address_id"] if address else None
    with write_tx(conn):
        if aid:
            row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                               (aid,)).fetchone()  # fmt: skip
            overrides: dict[str, Any] = json.loads(row["overrides"]) | {name: value}
            conn.execute("UPDATE addresses SET overrides = ? WHERE address_id = ?",
                         (json.dumps(overrides, sort_keys=True), aid))  # fmt: skip
        else:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (name, json.dumps(value), now, actor),
            )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'settings.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps({"key": name, "value": value})),
        )
    return {"key": name, "value": value, "address_id": aid, "restart": k.restart}
