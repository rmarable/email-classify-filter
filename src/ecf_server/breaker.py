"""Crash-loop breaker (SPEC §11.1): 5 crashes in 10 minutes stop the service until
`ecf service start`; the count resets after 30 minutes without a crash.

A crash is detected at start: the previous run left its `running` marker behind.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ecf_server.clock import from_ts, to_ts

WINDOW = timedelta(minutes=10)
THRESHOLD = 5
QUIET_RESET = timedelta(minutes=30)


@dataclass
class BreakerState:
    crashes: list[str] = field(default_factory=list[str])
    tripped: bool = False


def load(path: Path) -> BreakerState:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return BreakerState([str(c) for c in raw.get("crashes", [])], bool(raw.get("tripped")))
    except (FileNotFoundError, ValueError):
        return BreakerState()


def save(path: Path, st: BreakerState) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"crashes": st.crashes, "tripped": st.tripped}), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def on_start(state_path: Path, marker: Path, now: datetime) -> BreakerState:
    """Record a crash if the last run didn't stop cleanly; trip at the threshold."""
    st = load(state_path)
    if st.crashes and now - from_ts(st.crashes[-1]) > QUIET_RESET:
        st.crashes = []
    if marker.exists():
        st.crashes.append(to_ts(now))
    recent = [c for c in st.crashes if now - from_ts(c) <= WINDOW]
    if len(recent) >= THRESHOLD:
        st.tripped = True
    save(state_path, st)
    return st


def mark_running(marker: Path) -> None:
    marker.write_text(str(os.getpid()), encoding="utf-8")
    marker.chmod(0o600)


def mark_clean_exit(marker: Path) -> None:
    marker.unlink(missing_ok=True)


def reset(state_path: Path) -> None:
    save(state_path, BreakerState())
