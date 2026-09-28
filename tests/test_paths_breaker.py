from datetime import timedelta
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.paths import SUN_PATH_MAX, Paths, paths_for
from ecf_server import breaker
from ecf_server.clock import FakeClock


def test_ecf_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECF_HOME", str(tmp_path))
    p = paths_for("test")
    assert p.data_dir == tmp_path / "test"
    assert p.socket == tmp_path / "test" / "run" / "ecf.sock"


def test_socket_length_check() -> None:
    Paths("default", Path("/tmp/e")).check_socket_length()
    long_root = Path("/tmp/" + "x" * SUN_PATH_MAX)
    with pytest.raises(InvalidInputError, match="socket path"):
        Paths("default", long_root).check_socket_length()


def test_breaker_counts_crashes_and_trips(tmp_path: Path) -> None:
    clock = FakeClock()
    state, marker = tmp_path / "crash.json", tmp_path / "running"
    st = breaker.on_start(state, marker, clock.now())
    assert st.crashes == []  # first start, no marker
    for n in range(1, breaker.THRESHOLD + 1):
        breaker.mark_running(marker)  # the run "crashes": marker left behind
        clock.advance(60)
        st = breaker.on_start(state, marker, clock.now())
        assert len(st.crashes) == n
    assert st.tripped


def test_breaker_resets_after_quiet_period(tmp_path: Path) -> None:
    clock = FakeClock()
    state, marker = tmp_path / "crash.json", tmp_path / "running"
    for _ in range(3):
        breaker.mark_running(marker)
        breaker.on_start(state, marker, clock.now())
    breaker.mark_clean_exit(marker)
    clock.advance(breaker.QUIET_RESET.total_seconds() + 1)
    assert breaker.on_start(state, marker, clock.now()).crashes == []


def test_crashes_outside_window_do_not_trip(tmp_path: Path) -> None:
    clock = FakeClock()
    state, marker = tmp_path / "crash.json", tmp_path / "running"
    st = breaker.BreakerState()
    for _ in range(breaker.THRESHOLD):
        breaker.mark_running(marker)
        clock.advance(breaker.WINDOW.total_seconds() / 2 + timedelta(minutes=1).total_seconds())
        st = breaker.on_start(state, marker, clock.now())
    assert not st.tripped


def test_reset_clears(tmp_path: Path) -> None:
    state = tmp_path / "crash.json"
    breaker.save(state, breaker.BreakerState(["x"], tripped=True))
    breaker.reset(state)
    assert breaker.load(state) == breaker.BreakerState()
