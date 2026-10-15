"""One time-range parser (SPEC §10.2; OD-507, plan v3.0.0-STEP5 §3.1, D3, P13): `ecf backfill`,
`ecf reprocess`, `ecf logs` and `ecf stats` read `--since` and `--until` with it, and the service
validates with it (`ecf_server` imports it, as it imports `ecf.errors`).

| Form | Example | Meaning |
|---|---|---|
| date | `2026-09-01` | start of that day, local |
| date-time | `2026-09-29T08:00[:SS]`, `"2026-09-29 08:00"` | that moment, local |
| zone | `...Z`, `...-04:00` | as given |
| bare time | `08:00`, `08:00:30` | today, local; refused if in the future |
| age | `90s 30m 2h 3d 2w 6mo 1y`, `1h30m` | before now; `mo` = 30 days, `y` = 365 |

Units are lowercase (`M` and `30min` are refused); each unit once in a compound age. A local time
that doesn't exist (the spring-forward gap) is refused; one that happens twice (the fall-back hour)
is refused with both UTC readings, so the person adds the zone. Local is this computer's zone
(`/etc/localtime`, or `TZ`). Results are UTC and written `%Y-%m-%dT%H:%M:%S.%fZ`, the form the
audit log's timestamps compare against as strings (`ecf logs`).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ecf.errors import InvalidInputError

FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400, "mo": 30 * 86400,
         "y": 365 * 86400}  # fmt: skip
HINT = (
    'use a date (2026-09-01), a date and time (2026-09-29T08:00 or "2026-09-29 08:00"; add'
    " Z or -04:00 for a zone), a time today (08:00), or an age before now (90s 30m 2h 3d 2w"
    " 6mo 1y, or 1h30m); minutes, hours, days, weeks, months and years are m h d w mo y"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# seconds may carry a fraction: the form `stamp` writes, which the service reads back
_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)(Z|[+-]\d{2}:\d{2})?$"
)
_TIME = re.compile(r"^\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$")
_AGE = re.compile(r"^(?:\d+(?:mo|[smhdwy]))+$")
_PART = re.compile(r"(\d+)(mo|[smhdwy])")


def local_zone() -> tzinfo:
    """This computer's zone, with its DST rules when they can be read."""
    name = os.environ.get("TZ", "").removeprefix(":")
    try:
        if name:
            return ZoneInfo(name)
        with open("/etc/localtime", "rb") as f:
            return ZoneInfo.from_file(f)
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return datetime.now().astimezone().tzinfo or UTC


def stamp(at: datetime) -> str:
    """`at` in UTC, as the audit log writes timestamps."""
    return at.astimezone(UTC).strftime(FORMAT)


def parse(text: str, *, now: datetime, zone: tzinfo | None = None,
          what: str = "--since") -> datetime:  # fmt: skip
    """`text` as an aware UTC moment (module table). `now` is aware; `zone` defaults to local."""
    s = text.strip()
    z = zone or local_zone()
    if _AGE.fullmatch(s):
        try:
            return now.astimezone(UTC) - _age(s, what)
        except OverflowError:
            raise InvalidInputError(f"{what} {s!r}: too far back") from None
    if _DATE.fullmatch(s):
        return _local(datetime.combine(_date(s, what), time()), z, what)
    m = _DATETIME.fullmatch(s)
    if m:
        day, clock, offset = m.groups()
        naive = datetime.combine(_date(day, what), _time(clock, what))
        if offset is None:
            return _local(naive, z, what)
        if offset == "Z":
            return naive.replace(tzinfo=UTC)
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            raise InvalidInputError(f"{what} {text!r}: not a zone offset; {HINT}")
        delta = timedelta(hours=hours, minutes=minutes) * sign
        return (naive - delta).replace(tzinfo=UTC)
    if _TIME.fullmatch(s):
        today = now.astimezone(z).date()
        at = _local(datetime.combine(today, _time(s, what)), z, what)
        if at > now:
            raise InvalidInputError(f"{what} {text!r} is later today; give the date too"
                                    " (2026-09-29T08:00) for an earlier day")  # fmt: skip
        return at
    raise InvalidInputError(f"{what} {text!r}: {HINT}")


def _age(s: str, what: str) -> timedelta:
    parts = _PART.findall(s)
    units = [u for _, u in parts]
    if len(set(units)) != len(units):
        raise InvalidInputError(f"{what} {s!r}: each unit once (1h30m); {HINT}")
    try:
        return timedelta(seconds=sum(int(n) * UNITS[u] for n, u in parts))
    except OverflowError:
        raise InvalidInputError(f"{what} {s!r}: too far back") from None


def _date(s: str, what: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise InvalidInputError(f"{what} {s!r}: not a date; {HINT}") from None


def _time(s: str, what: str) -> time:
    try:
        return time.fromisoformat(s)
    except ValueError:
        raise InvalidInputError(f"{what} {s!r}: not a time; {HINT}") from None


def _local(naive: datetime, z: tzinfo, what: str) -> datetime:
    """A local wall-clock time in UTC; refused in a DST gap or fold (P13)."""
    first, second = naive.replace(tzinfo=z, fold=0), naive.replace(tzinfo=z, fold=1)
    if first.astimezone(UTC).astimezone(z).replace(tzinfo=None) != naive:
        raise InvalidInputError(f"{what} {naive:%Y-%m-%d %H:%M:%S} doesn't exist here (the clocks"
                                " went forward); give another time or a zone")  # fmt: skip
    if first.utcoffset() != second.utcoffset():
        a, b = sorted((first.astimezone(UTC), second.astimezone(UTC)))
        raise InvalidInputError(f"{what} {naive:%Y-%m-%d %H:%M:%S} happens twice here (the clocks"
                                f" went back): {a:%H:%M}Z or {b:%H:%M}Z; add the zone, e.g."
                                f" {naive:%Y-%m-%dT%H:%M}{_offset(first)}")  # fmt: skip
    return first.astimezone(UTC)


def _offset(at: datetime) -> str:
    off = at.utcoffset() or timedelta()
    sign = "-" if off < timedelta() else "+"
    minutes = abs(int(off.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def resolve(since: str, until: str | None, *, now: datetime,
            zone: tzinfo | None = None) -> tuple[datetime, datetime | None]:  # fmt: skip
    """A `--since`/`--until` pair: since not in the future, until after since and capped at now."""
    start = parse(since, now=now, zone=zone)
    if start > now:
        raise InvalidInputError(f"--since {since!r} is in the future")
    if until is None:
        return start, None
    end = min(parse(until, now=now, zone=zone, what="--until"), now.astimezone(UTC))
    if end <= start:
        raise InvalidInputError(f"--until {until!r} isn't after --since {since!r}")
    return start, end
