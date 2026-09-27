"""`ecf-server local`: the local service process (SPEC §11.1).

Threads: main (signals, watchdog, shutdown), uvicorn on the Unix socket, and the timer tick.
The socket is created by ecf (umask 077, 0600, in a 0700 directory) because uvicorn's own setup
would make it 0666. A single-instance lock guards the data directory.
"""

from __future__ import annotations

import fcntl
import os
import secrets
import signal
import socket
import sqlite3
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TextIO

import uvicorn

from ecf.errors import ServiceUnavailableError
from ecf.log import configure_logging
from ecf.paths import Paths
from ecf_server import breaker, db
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import Clock, SystemClock, to_ts
from ecf_server.log_bridge import log
from ecf_server.secretstore.macos_interaction import set_interaction_allowed
from ecf_server.secretstore.select import (
    choose_backend,
    host_probe,
    interpreter_changed,
    interpreter_sha256,
    record_interpreter,
)

TICK_SECONDS = 60
WATCHDOG_SECONDS = 300
STOP_TIMEOUT = 20.0
EXIT_OK, EXIT_UNAVAILABLE, EXIT_CRASH = 0, 3, 70


@dataclass(frozen=True)
class Options:
    tick_seconds: float = TICK_SECONDS
    watchdog_seconds: float = WATCHDOG_SECONDS


class AlreadyRunningError(ServiceUnavailableError):
    pass


def _private_dir(p: Path) -> None:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.chmod(0o700)


def acquire_lock(paths: Paths) -> TextIO:
    _private_dir(paths.run_dir)
    f = paths.lock.open("a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        f.close()
        raise AlreadyRunningError(
            f"ecf-server is already running for install {paths.install}"
        ) from exc
    return f


def bind_socket(paths: Paths) -> socket.socket:
    """Create the 0600 socket. Call only while holding the instance lock (removes a stale file)."""
    paths.check_socket_length()
    paths.socket.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        sock.bind(str(paths.socket))
    finally:
        os.umask(old)
    paths.socket.chmod(0o600)
    sock.listen(64)
    return sock


def write_token(paths: Paths) -> str:
    token = secrets.token_urlsafe(32)
    tmp = paths.token.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.replace(tmp, paths.token)
    return token


class Service:
    def __init__(
        self, paths: Paths, clock: Clock | None = None, opts: Options | None = None
    ) -> None:
        self.paths = paths
        self.clock = clock or SystemClock()
        self.opts = opts or Options()
        self.stop = threading.Event()
        self.exit_code = EXIT_OK
        self._last_tick_mono = self.clock.monotonic()
        self.state = ServiceState(
            install=paths.install, token="", started_at=to_ts(self.clock.now())
        )

    # -- threads -------------------------------------------------------------------------------
    def _timer(self) -> None:
        while not self.stop.wait(self.opts.tick_seconds):
            self.tick()

    def tick(self) -> None:
        self._last_tick_mono = self.clock.monotonic()
        self.state.last_tick_at = to_ts(self.clock.now())
        self.state.ticks += 1

    def watchdog_expired(self) -> bool:
        # monotonic time stops while the computer sleeps, so sleep never trips the watchdog
        return self.clock.monotonic() - self._last_tick_mono > self.opts.watchdog_seconds

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        log.info("service.signal", signal=signal.Signals(signum).name)
        self.stop.set()

    def _secret_store_report(self, conn: sqlite3.Connection) -> dict[str, object]:
        try:
            backend = choose_backend(host_probe())
        except ServiceUnavailableError as exc:
            return {"backend": None, "detail": exc.detail, "interpreter_changed": False}
        current = interpreter_sha256()
        changed = interpreter_changed(conn, current)
        if not changed:
            record_interpreter(conn, self.clock, current, by="service")
        return {"backend": backend, "interpreter_changed": changed}

    # -- run -----------------------------------------------------------------------------------
    def run(self) -> int:
        _private_dir(self.paths.data_dir)
        configure_logging("service", log_file=self.paths.log)
        try:
            lock = acquire_lock(self.paths)
        except AlreadyRunningError as exc:
            sys.stderr.write(f"ecf-server: {exc.detail}\n")
            return EXIT_UNAVAILABLE
        try:
            return self._run_locked()
        finally:
            lock.close()

    def _run_locked(self) -> int:
        st = breaker.on_start(self.paths.crash_state, self.paths.running_marker, self.clock.now())
        self.state.breaker = {"recent_crashes": len(st.crashes), "tripped": st.tripped}
        if st.tripped:
            log.error("service.breaker_tripped", crashes=len(st.crashes))
            sys.stderr.write(
                "ecf-server: stopped after repeated crashes; run `ecf service start`\n"
            )
            breaker.mark_clean_exit(self.paths.running_marker)
            return EXIT_OK  # exit 0 so launchd/systemd don't restart it
        breaker.mark_running(self.paths.running_marker)
        if sys.platform == "darwin":
            set_interaction_allowed(False)  # OD-163: never wait on a Keychain dialog
        conn = db.connect(self.paths.db)
        applied = db.migrate(conn)
        with db.write_tx(conn):
            conn.execute("DELETE FROM leases")  # one process in v1: all leases are stale at start
        self.state.secret_store = self._secret_store_report(conn)
        conn.close()
        self.state.token = write_token(self.paths)
        sock = bind_socket(self.paths)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(self.state),
                uds=str(self.paths.socket),
                log_config=None,
                lifespan="off",
                access_log=False,
            )
        )
        web = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="api", daemon=True
        )
        timer = threading.Thread(target=self._timer, name="timer", daemon=True)
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        web.start()
        timer.start()
        log.info("service.started", install=self.paths.install, migrations=applied)
        while not self.stop.wait(1.0):
            if self.watchdog_expired():
                log.error("service.watchdog", seconds=self.opts.watchdog_seconds)
                self.exit_code = EXIT_CRASH
                self.stop.set()
        server.should_exit = True
        web.join(STOP_TIMEOUT)
        timer.join(STOP_TIMEOUT)
        sock.close()
        with suppress(FileNotFoundError):
            self.paths.socket.unlink()
        if self.exit_code == EXIT_OK:
            breaker.mark_clean_exit(self.paths.running_marker)
        log.info("service.stopped", exit_code=self.exit_code)
        return self.exit_code
