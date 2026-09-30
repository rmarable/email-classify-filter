"""When each address is checked (SPEC §5.3, §5.4, §5.5). V1.1 step 11b.

- **Intervals:** `mail_fetch_interval_workday` (10 min) inside business hours, `_offhours` (30 min)
  outside; range 5-120 (OD-033). Install defaults in the settings table, per-address overrides in
  `addresses.overrides`.
- **Business hours:** days, start, end and an IANA zone; default Mon-Fri 08:00-17:00
  America/New_York (OD-034).
- **Catch-up** (OD-031): when a check leaves mail waiting, the next one starts 30 s later, until
  the backlog is empty or `catch_up_max_minutes` passes; then a `catch_up_cooldown_minutes`
  cooldown. On a laptop only on AC power, unless `catch_up_on_battery`.
- **Sleep** (§5.5): when wall-clock time has moved more than 60 s further than monotonic time since
  the last tick, the computer slept; every address becomes due at once.
- A busy lease retries in 60 s. Pause never stops the pre-check (§5.4).

The tick only decides and enqueues `fetch` jobs; a worker thread runs them (service.py).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ecf.ids import AddressId
from ecf_server import health, jobs
from ecf_server.checks import CheckReport
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

DEFAULTS: dict[str, Any] = {
    "business_hours": {
        "days": [0, 1, 2, 3, 4],
        "start": "08:00",
        "end": "17:00",
        "tz": "America/New_York",
    },
    "mail_fetch_interval_workday": 10,
    "mail_fetch_interval_offhours": 30,
    "catch_up": "auto",
    "catch_up_cooldown_minutes": 15,
    "catch_up_on_battery": False,
}
CATCH_UP_PAUSE = timedelta(seconds=30)
BUSY_RETRY = timedelta(seconds=60)
LOGIN_RETRY = timedelta(hours=1)
SLEEP_DRIFT_S = 60.0
CHECK_TIMEOUT_S = 420  # a check's 6-minute budget plus a margin (job claims expire at 6x this)


@dataclass(frozen=True)
class Power:
    laptop: bool  # has a battery
    on_ac: bool


def host_power() -> Power:
    """macOS: `pmset -g batt`; Linux: /sys/class/power_supply. Unknown means a desktop on AC."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/usr/bin/pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            return Power(
                "InternalBattery" in out, "AC Power" in out or "InternalBattery" not in out
            )
        supplies = list(Path("/sys/class/power_supply").glob("*"))
        batteries = [p for p in supplies if _read(p / "type") == "Battery"]
        mains = [p for p in supplies if _read(p / "type") == "Mains"]
        on_ac = not batteries or any(_read(p / "online") == "1" for p in mains)
        return Power(bool(batteries), on_ac)
    except OSError:
        return Power(False, True)


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def default_catch_up_max(power: Power) -> int:
    """SPEC §5.3: 30 on fanless or unknown Apple laptops, 60 elsewhere. Without a verified
    `hw.model` table of fanless Macs, every Mac laptop gets 30 (the stricter value)."""
    return 30 if sys.platform == "darwin" and power.laptop else 60


def settings(conn: sqlite3.Connection, address_id: str, power: Power) -> dict[str, Any]:
    """Install defaults, then the settings table, then the address's overrides."""
    out = dict(DEFAULTS) | {"catch_up_max_minutes": default_catch_up_max(power)}
    for row in conn.execute(
        "SELECT key, value FROM settings WHERE key IN (SELECT value FROM json_each(?))",
        (json.dumps(list(out)),),
    ):
        out[row["key"]] = json.loads(row["value"])
    row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?", (address_id,))
    overrides: dict[str, Any] = json.loads(row.fetchone()["overrides"])
    return out | {k: v for k, v in overrides.items() if k in out}


def in_business_hours(now: datetime, bh: dict[str, Any]) -> bool:
    local = now.astimezone(ZoneInfo(bh["tz"]))
    start, end = time.fromisoformat(bh["start"]), time.fromisoformat(bh["end"])
    return local.weekday() in bh["days"] and start <= local.time() < end


def interval(now: datetime, s: dict[str, Any]) -> timedelta:
    workday = in_business_hours(now, s["business_hours"])
    minutes = s["mail_fetch_interval_workday" if workday else "mail_fetch_interval_offhours"]
    return timedelta(minutes=min(120, max(5, int(minutes))))


def interval_offhours(conn: sqlite3.Connection) -> timedelta:
    """The install's off-hours interval: model rounds on battery run at most this often (OD-029)."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'mail_fetch_interval_offhours'"
    ).fetchone()
    minutes = json.loads(row["value"]) if row else DEFAULTS["mail_fetch_interval_offhours"]
    return timedelta(minutes=min(120, max(5, int(minutes))))


def after_check(
    conn: sqlite3.Connection, clock: Clock, report: CheckReport, power: Power
) -> datetime:
    """Decide and store when the address is due next; returns that time."""
    now = clock.now()
    s = settings(conn, report.address_id, power)
    row = conn.execute(
        "SELECT catch_up_since, cooldown_until, next_due_at FROM check_state WHERE address_id = ?",
        (report.address_id,),
    ).fetchone()
    since = from_ts(row["catch_up_since"]) if row and row["catch_up_since"] else None
    cooldown = from_ts(row["cooldown_until"]) if row and row["cooldown_until"] else None
    backlog = report.remaining + report.backfill_remaining  # an `ecf backfill` counts too
    waiting = report.status in ("ok", "reset_recovered") and backlog > 0
    allowed = (
        s["catch_up"] == "auto"
        and (cooldown is None or now >= cooldown)
        and (power.on_ac or not power.laptop or bool(s["catch_up_on_battery"]))
    )
    if report.status == "busy":
        due = now + BUSY_RETRY
    elif report.status == "login_rejected" and health.login_backoff(conn, report.address_id):
        due = now + LOGIN_RETRY  # don't get the account locked (SPEC §13.3)
    elif waiting and allowed:
        since = since or now
        if now - since >= timedelta(minutes=int(s["catch_up_max_minutes"])):
            since, cooldown = None, now + timedelta(minutes=int(s["catch_up_cooldown_minutes"]))
            due = now + interval(now, s)
        else:
            due = now + CATCH_UP_PAUSE
    else:
        since = None
        due = now + interval(now, s)
    # made due during this check (an Undo click): keep it, or it waits a whole interval
    asked = row["next_due_at"] if row and row["next_due_at"] else None
    if asked and asked > report.started_at and from_ts(asked) < due:
        due = from_ts(asked)
    with write_tx(conn):
        conn.execute(
            "INSERT INTO check_state (address_id, next_due_at, catch_up_since, cooldown_until)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (address_id) DO UPDATE SET"
            " next_due_at = excluded.next_due_at, catch_up_since = excluded.catch_up_since,"
            " cooldown_until = excluded.cooldown_until",
            (
                report.address_id,
                to_ts(due),
                to_ts(since) if since else None,
                to_ts(cooldown) if cooldown else None,
            ),
        )
    return due


class Scheduler:
    """Called on every tick: enqueues a `fetch` job for each address that is due."""

    def __init__(self, clock: Clock, power: Callable[[], Power] = host_power) -> None:
        self.clock, self.power = clock, power
        self._last_wall: datetime | None = None
        self._last_mono: float | None = None

    def slept(self) -> bool:
        wall, mono = self.clock.now(), self.clock.monotonic()
        slept = (
            self._last_wall is not None
            and self._last_mono is not None
            and (wall - self._last_wall).total_seconds() - (mono - self._last_mono) > SLEEP_DRIFT_S
        )
        self._last_wall, self._last_mono = wall, mono
        return slept

    def tick(self, conn: sqlite3.Connection) -> list[str]:
        """Enqueue checks that are due; returns their address IDs."""
        now = to_ts(self.clock.now())
        if self.slept():
            with write_tx(conn):
                conn.execute("UPDATE check_state SET next_due_at = ?", (now,))
        due = [
            r["address_id"]
            for r in conn.execute(
                "SELECT a.address_id FROM addresses a LEFT JOIN check_state c USING (address_id)"
                " WHERE a.removed_at IS NULL AND (c.next_due_at IS NULL OR c.next_due_at <= ?)"
                " AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.address_id = a.address_id"
                " AND j.queue = 'fetch' AND j.state IN ('queued', 'claimed'))",
                (now,),
            )
        ]
        for address_id in due:
            jobs.enqueue(
                conn,
                self.clock,
                jobs.Queue.FETCH,
                AddressId(address_id),
                {"reason": "schedule"},
                timeout_s=CHECK_TIMEOUT_S,
            )
        return due
