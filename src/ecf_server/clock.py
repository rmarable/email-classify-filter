"""The Clock port (SPEC §3.3): wall-clock time for due-ness and TTLs, monotonic for delays."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """A settable clock for tests and `ecf-server dev` (SPEC §17.3)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


def to_ts(dt: datetime) -> str:
    """Timestamps are stored as fixed-width UTC ISO-8601 text, so they compare correctly as text."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
