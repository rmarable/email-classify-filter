"""`ecf-server local`: the local service process (SPEC §11.1).

Threads: main (signals, watchdog, shutdown), uvicorn on the Unix socket, and the timer tick.
The socket is created by ecf (umask 077, 0600, in a 0700 directory) because uvicorn's own setup
would make it 0666. A single-instance lock guards the data directory.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import socket
import sqlite3
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TextIO

import uvicorn

from ecf.errors import NotFoundError, ServiceUnavailableError
from ecf.log import configure_logging
from ecf.paths import Paths
from ecf_server import audit, breaker, checks, db, health, jobs, schedule
from ecf_server.api import DevHooks, ServiceState, create_app
from ecf_server.chat import FakeChat
from ecf_server.clock import Clock, FakeClock, SystemClock, to_ts
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.mail.imap import ImapSource
from ecf_server.notify import Notifier, NullNotifier, host_notifier
from ecf_server.schedule import Scheduler
from ecf_server.secretstore import SecretStore
from ecf_server.secretstore.macos_interaction import set_interaction_allowed
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.secretstore.select import (
    choose_backend,
    host_probe,
    interpreter_changed,
    interpreter_sha256,
    open_store,
    record_interpreter,
)
from ecf_server.stepper import FakeStepper, host_stepper

TICK_SECONDS = 60
WORKER = "checks"
WATCHDOG_SECONDS = 300
STOP_TIMEOUT = 20.0
EXIT_OK, EXIT_UNAVAILABLE, EXIT_CRASH = 0, 3, 70


@dataclass(frozen=True)
class Options:
    tick_seconds: float = TICK_SECONDS
    watchdog_seconds: float = WATCHDOG_SECONDS


def imap_factory(host: str, user: str, password: Callable[[], str]) -> MailSource:
    return ImapSource(host, user, password)


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
        raise AlreadyRunningError(f"already running for install {paths.install}") from exc
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


def _clear_stale(conn: sqlite3.Connection) -> None:
    """One process in v1: at start every lease and claimed job belongs to a dead process."""
    with db.write_tx(conn):
        conn.execute("DELETE FROM leases")
    jobs.release_claims(conn)


class Service:
    def __init__(
        self,
        paths: Paths,
        clock: Clock | None = None,
        opts: Options | None = None,
        *,
        dev: bool = False,
    ) -> None:
        self.paths = paths
        self.clock = clock or SystemClock()
        self.opts = opts or Options()
        self.stop = threading.Event()
        self.exit_code = EXIT_OK
        self._last_tick_mono = self.clock.monotonic()
        self.dev = dev
        self.secrets: SecretStore | None = MemorySecretStore() if dev else None
        self.state = ServiceState(
            install=paths.install, token="", started_at=to_ts(self.clock.now()), clock=self.clock
        )
        self.scheduler = Scheduler(self.clock)
        self.work = threading.Event()  # set when checks are due

    # -- threads -------------------------------------------------------------------------------
    def _timer(self) -> None:
        while not self.stop.wait(self.opts.tick_seconds):
            self.tick()

    def tick(self) -> None:
        self._last_tick_mono = self.clock.monotonic()
        self.state.last_tick_at = to_ts(self.clock.now())
        self.state.ticks += 1
        if self.state.db_path is None:
            return
        try:
            conn = db.connect(self.state.db_path)
            try:
                if self.scheduler.tick(conn):
                    self.work.set()
                audit.flush(conn, self.clock, self.paths.audit_dir, self.paths.install)
            finally:
                conn.close()
        except Exception as exc:  # a scheduling failure must never stop the timer
            log.error("schedule.tick_failed", error_type=type(exc).__name__)

    def _final_flush(self) -> None:
        """Copy the last audit rows to the files before exiting."""
        if self.state.db_path is None:
            return
        try:
            conn = db.connect(self.state.db_path)
            try:
                audit.flush(conn, self.clock, self.paths.audit_dir, self.paths.install)
            finally:
                conn.close()
        except Exception as exc:  # the rows stay in the table; the next start copies them
            log.error("audit.flush_failed", error_type=type(exc).__name__)

    def _notifier(self) -> Notifier:
        """Desktop notifications (OD-190): none in dev mode or with `notifications: off`."""
        if self.dev or self.state.db_path is None:
            return NullNotifier()
        conn = db.connect(self.state.db_path)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'notifications'").fetchone()
        finally:
            conn.close()
        return NullNotifier() if row and json.loads(row["value"]) == "off" else host_notifier()

    def _checks(self) -> None:
        """Run due checks from the `fetch` queue, one at a time (SPEC §5.4)."""
        while not self.stop.is_set():
            self.work.wait(5.0)
            self.work.clear()
            if self.state.db_path is None:
                continue
            conn = db.connect(self.state.db_path)
            try:
                while not self.stop.is_set() and self._one_check(conn):
                    pass
            finally:
                conn.close()

    def _one_check(self, conn: sqlite3.Connection) -> bool:
        job = jobs.claim(conn, self.clock, jobs.Queue.FETCH, WORKER)
        if job is None:
            return False
        try:
            try:
                store = self.state.store()
            except Exception as exc:  # no usable secret store: record it, don't retry blindly
                report = checks.record_failure(
                    conn,
                    self.clock,
                    job.address_id,
                    "secret_unavailable",
                    f"secret store unavailable ({type(exc).__name__})",
                )
                schedule.after_check(conn, self.clock, report, self.scheduler.power())
                jobs.complete(conn, job.job_id, WORKER)
                return True
            report = checks.run_check(
                conn,
                self.clock,
                address_id=job.address_id,
                install=self.paths.install,
                secrets=store,
                factory=self.state.mail_factory or imap_factory,
                connect=self.state.connect,
            )
            health.after_check(conn, self.clock, self.state.notifier, report)
            schedule.after_check(conn, self.clock, report, self.scheduler.power())
            jobs.complete(conn, job.job_id, WORKER)
        except NotFoundError:  # removed since it was queued
            jobs.complete(conn, job.job_id, WORKER)
        except Exception as exc:
            log.error("check.crashed", address_id=job.address_id, error_type=type(exc).__name__)
            jobs.fail(conn, self.clock, job.job_id, WORKER, type(exc).__name__)
        return True

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
        os.umask(0o077)  # everything the service creates is private (logs, state, rotated files)
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

    def _prepare_state(self, conn: sqlite3.Connection) -> None:
        """Dev hooks and memory secrets in dev mode; the OS secret store (prompts off) otherwise."""
        if self.dev:
            self.state.mode = "dev"
            self.state.secret_store = {"backend": "memory", "interpreter_changed": False}
            if not isinstance(self.clock, FakeClock):
                raise TypeError("dev mode needs a FakeClock")
            self.state.dev = DevHooks(self.clock, FakeChat(), self.tick)
        else:
            self.state.secret_store = self._secret_store_report(conn)
            if self.state.secret_store.get("backend"):
                try:
                    self.secrets = open_store(
                        self.paths.install,
                        self.paths.data_dir,
                        interactive=False,
                        probe=host_probe(),
                    )
                except ServiceUnavailableError as exc:
                    self.state.secret_store["detail"] = exc.detail

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
        if sys.platform == "darwin" and not self.dev:
            set_interaction_allowed(False)  # OD-163: never wait on a Keychain dialog
        conn = db.connect(self.paths.db)
        applied = db.migrate(conn)
        _clear_stale(conn)
        self._prepare_state(conn)
        conn.close()
        self.state.db_path = self.paths.db
        self.state.notifier = self._notifier()
        # dev mode never shows a real Touch ID dialog; its fake approves (dev refuses production)
        self.state.stepper = FakeStepper() if self.dev else host_stepper()
        self.state.secrets = self.secrets
        self.state.mail_factory = imap_factory
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
        worker = threading.Thread(target=self._checks, name="checks", daemon=True)
        self._last_tick_mono = self.clock.monotonic()  # the watchdog counts from here, not __init__
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        web.start()
        timer.start()
        worker.start()
        self.work.set()  # check anything already due at start
        log.info("service.started", install=self.paths.install, migrations=applied)
        while not self.stop.wait(1.0):
            if self.watchdog_expired():
                log.error("service.watchdog", seconds=self.opts.watchdog_seconds)
                self.exit_code = EXIT_CRASH
                self.stop.set()
        server.should_exit = True
        web.join(STOP_TIMEOUT)
        timer.join(STOP_TIMEOUT)
        self.work.set()
        worker.join(STOP_TIMEOUT)
        self._final_flush()
        sock.close()
        with suppress(FileNotFoundError):
            self.paths.socket.unlink()
        if self.exit_code == EXIT_OK:
            breaker.mark_clean_exit(self.paths.running_marker)
        log.info("service.stopped", exit_code=self.exit_code)
        return self.exit_code
