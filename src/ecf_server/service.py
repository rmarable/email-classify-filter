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
from ecf_server import (
    alerts,
    answers,
    approvals,
    audit,
    breaker,
    checks,
    claude_queue,
    claude_review,
    daily,
    db,
    decide,
    health,
    jobs,
    mailbox_actions,
    modelq,
    models,
    needs_you,
    ollama_log,
    pipeline,
    retention,
    schedule,
    stages,
)
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
from ecf_server.slack_runtime import SlackRuntime
from ecf_server.stepper import FakeStepper, host_stepper

TICK_SECONDS = 60
WORKER = "checks"
WATCHDOG_SECONDS = 300
SLEEP_GAP_S = 120.0  # wall time running this far ahead of monotonic time between ticks: a sleep
TICK_ALERT_AFTER = 5  # failing ticks in a row (minutes) before a desktop System Error
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
    """One process in v1: at start every lease, claimed job and review claim belongs to a dead
    process."""
    with db.write_tx(conn):
        conn.execute("DELETE FROM leases")
    jobs.release_claims(conn)
    claude_review.release_all(conn)  # `/ecf-review` sessions die with the process


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
        self._signalled: int | None = None  # set by the signal handler, read by the main loop
        self.exit_code = EXIT_OK
        self._last_tick_mono = self.clock.monotonic()
        self._last_tick_wall = self.clock.now()
        self.dev = dev
        self.secrets: SecretStore | None = MemorySecretStore() if dev else None
        self.state = ServiceState(
            install=paths.install, token="", started_at=to_ts(self.clock.now()), clock=self.clock
        )
        self.scheduler = Scheduler(self.clock)
        self.work = threading.Event()  # set when checks are due
        self.model_wake = threading.Event()  # set when the local model has new work
        self.rounds = modelq.RoundSchedule(self.clock)
        # registered in checks.IN_LEASE on import: actions run in their address's check (V1.3)
        self.in_check = mailbox_actions.run_in_check
        self._ollama_log_at: float | None = None  # monotonic time of the last look (OD-266)
        self.throttle = self.state.throttle  # speeds across rounds, `ecf check`'s too (OD-243)

    # -- threads -------------------------------------------------------------------------------
    def _timer(self) -> None:
        while not self.stop.wait(self.opts.tick_seconds):
            self.tick()

    def tick(self) -> None:
        mono, wall = self.clock.monotonic(), self.clock.now()
        awake = mono - self._last_tick_mono  # the monotonic clock stops during sleep (§5.5)
        slept = (wall - self._last_tick_wall).total_seconds() - awake > SLEEP_GAP_S
        self._last_tick_mono, self._last_tick_wall = mono, wall
        self.state.last_tick_at = to_ts(wall)
        self.state.ticks += 1
        if self.state.db_path is None:
            return
        ok = True
        try:
            conn = db.connect(self.state.db_path)
            try:
                power = self.scheduler.power()
                if power.laptop and not power.on_ac and not slept:
                    daily.record_battery(conn, self.clock, awake)
                if self.scheduler.tick(conn):
                    self.work.set()
                audit.flush(conn, self.clock, self.paths.audit_dir, self.paths.install)
            finally:
                conn.close()
        except Exception as exc:  # a scheduling failure must never stop the timer
            ok = self._tick_failed("schedule.tick_failed", exc)
        if not self._approvals(awake, woke=slept):
            ok = False
        if ok:
            self.state.tick_failures, self.state.tick_error = 0, None

    def _tick_failed(self, event: str, exc: Exception) -> bool:
        """Log (a SQLite error's own text is safe: no mail content) and count; after
        TICK_ALERT_AFTER failing ticks in a row tell the desktop, once (V1.2 review: a tick that
        kept failing looked fine in doctor)."""
        detail = str(exc)[:200] if isinstance(exc, sqlite3.Error) else ""
        log.error(event, error_type=type(exc).__name__, detail=detail)
        self.state.tick_failures += 1
        self.state.tick_error = f"{type(exc).__name__} {detail}".strip()
        if self.state.tick_failures == TICK_ALERT_AFTER:
            text = (f"ecf's timer work keeps failing ({self.state.tick_error}); approvals, delays"
                    " and retention wait. Details: ecf logs")  # fmt: skip
            self.state.notifier.notify(alerts.title("system_error"), text)
        return False

    def _approvals(self, awake: float, *, woke: bool) -> bool:
        """Expire approvals, count down delayed sends and run approved actions (§9.5; V1.2 step
        7b). V1.3 moves the runner into the checks worker, which holds the address lease."""
        if self.state.db_path is None:
            return True
        try:
            conn = db.connect(self.state.db_path)
            try:
                approvals.expire(conn, self.clock)
                answers.expire(conn, self.clock)
                needs_you.mark_stale(conn, self.clock)
                if retention.due(conn, self.clock):  # once a day (§6.5)
                    retention.run(conn, self.clock)
                alerts.dead_jobs(conn, self.clock, self.state.notifier)
                self._model_check(conn)
                decide.sweep(conn, self.clock)
                claude_queue.sweep(conn, self.clock)  # B and C: items a crash left short of it
                approvals.post_held_cards(conn, self.clock)  # after a large backlog (§5.3)
                stages.tick(conn, self.clock)  # gate announcements; live drops on a model change
                self._ollama_log(conn)
                approvals.advance_delays(conn, self.clock, awake, woke=woke)
                # approved and automatic actions run in their address's check, which has the
                # mailbox open under the lease (mailbox_actions.run_in_check; V1.3 step 5b)
            finally:
                conn.close()
        except Exception as exc:  # never stops the timer; retried next tick
            return self._tick_failed("approvals.tick_failed", exc)
        return True

    def _ollama_log(self, conn: sqlite3.Connection) -> None:
        """Rotate the Ollama login item's log, at most once an hour's look (OD-266)."""
        now = self.clock.monotonic()
        if self._ollama_log_at is not None and now - self._ollama_log_at < (
            ollama_log.CHECK_EVERY.total_seconds()
        ):
            return
        self._ollama_log_at = now
        ollama_log.check(conn, self.clock, self.paths.root)

    def _model_check(self, conn: sqlite3.Connection) -> None:
        """Keep the local-model alert current once models are installed here (V1.3 step 1b); from
        step 2 the model queue also checks before each round."""
        if not models.installed(conn):
            return
        if not models.needed(conn):  # no address uses preset A or B: nothing to watch
            models.quiet(conn, self.clock, self.state.notifier)
            return
        client = self.state.model_client()
        try:
            models.check(conn, self.clock, self.state.notifier, client, **self.state.model_check)
        finally:
            client.close()

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

    def _report_restart(self, crashes: int) -> None:
        """System Error: this start follows a crash (Slack and desktop, §11.1)."""
        try:
            conn = db.connect(self.paths.db)
            try:
                alerts.event(conn, self.clock, self.state.notifier, "system_error",
                             f"ecf restarted after a crash ({crashes} in the last 10 minutes; "
                             "5 stop it). Details: ecf logs.")  # fmt: skip
            finally:
                conn.close()
        except Exception as exc:  # reporting must never stop the start
            log.error("service.restart_report_failed", error_type=type(exc).__name__)

    def _report_trip(self, crashes: int) -> None:
        """The breaker tripped: the service is about to exit, so nothing will send a queued post.
        Tell the desktop, and Slack directly (best effort, §11.1)."""
        text = (f"ecf stopped after {crashes} crashes in 10 minutes and stays stopped."
                " Run `ecf service start` after checking `ecf logs`.")  # fmt: skip
        if self.dev:
            return
        try:
            host_notifier().notify(alerts.title("system_error"), text)
            if sys.platform == "darwin":
                set_interaction_allowed(False)  # OD-163: never wait on a Keychain dialog
            store = open_store(self.paths.install, self.paths.data_dir, interactive=False,
                               probe=host_probe())  # fmt: skip
            conn = db.connect(self.paths.db)
            try:
                alerts.post_now(conn, store, alerts.title("system_error"), text)
            finally:
                conn.close()
        except Exception as exc:  # the service is stopping either way
            log.error("service.trip_report_failed", error_type=type(exc).__name__)

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
            if report.created:
                self.model_wake.set()  # new mail for the local model
            schedule.after_check(conn, self.clock, report, self.scheduler.power())
            jobs.complete(conn, job.job_id, WORKER)
        except NotFoundError:  # removed since it was queued
            jobs.complete(conn, job.job_id, WORKER)
        except Exception as exc:
            log.error("check.crashed", address_id=job.address_id, error_type=type(exc).__name__)
            jobs.fail(conn, self.clock, job.job_id, WORKER, type(exc).__name__)
        return True

    def _models(self) -> None:
        """Run model rounds (SPEC §5.2; V1.3 step 2a): woken by new mail, else every few seconds
        to see whether a round is due. Idle until a `Work` exists (the classifier, V1.3 step 3)."""
        while not self.stop.is_set():
            self.model_wake.wait(5.0)
            self.model_wake.clear()
            work = self.state.model_work
            if work is None or self.state.db_path is None or not self.rounds.due():
                continue
            try:
                self._model_round(work)
            except Exception as exc:  # never stops the worker; the next wake tries again
                log.error("model.round_crashed", error_type=type(exc).__name__)

    def _model_round(self, work: modelq.Work) -> None:
        if self.state.db_path is None:
            return
        conn = db.connect(self.state.db_path)
        try:
            if not modelq.waiting(conn):
                return
            power = self.scheduler.power()
            on_battery = power.laptop and not power.on_ac
            self.throttle.power(not on_battery)
            client = self.state.model_client()
            try:
                with modelq.awake(on_ac=not on_battery):
                    report = modelq.run_round(conn, self.clock, self.state.notifier, client, work,
                                              resident=modelq.resident(conn),
                                              check_kw=self.state.model_check, stop=self.stop,
                                              throttle=self.throttle)  # fmt: skip
            finally:
                client.close()
            self.rounds.after(report, on_battery=on_battery,
                              offhours=schedule.interval_offhours(conn))  # fmt: skip
            log.info("model.round", status=report.status, done=report.done, failed=report.failed,
                     waiting=report.waiting)  # fmt: skip
        finally:
            conn.close()

    def watchdog_expired(self) -> bool:
        # monotonic time stops while the computer sleeps, so sleep never trips the watchdog
        return self.clock.monotonic() - self._last_tick_mono > self.opts.watchdog_seconds

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        """Only records the signal: the main loop logs it and sets `stop` within a second. Setting
        an Event or logging here can deadlock, since the handler runs on the main thread, which
        may be holding the Event's lock inside `stop.wait` (found 2026-09-29 when two SIGTERMs
        arrived together; the service never stopped)."""
        self._signalled = signum

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

    def _open_state(self) -> list[str]:
        """Migrate the database and fill the shared service state; returns migrations applied."""
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
        # the local classifier (V1.3 step 3); a dev service runs it only when asked, so tests that
        # use one never call the Ollama on the developer's computer
        if not self.dev or os.environ.get("ECF_DEV_MODEL") == "1":
            self.state.model_work = pipeline.work
        return applied

    def _api_server(self) -> uvicorn.Server:
        return uvicorn.Server(
            uvicorn.Config(
                create_app(self.state),
                uds=str(self.paths.socket),
                log_config=None,
                lifespan="off",
                access_log=False,
            )
        )

    def _slack_runtime(self) -> tuple[SlackRuntime, threading.Thread]:
        slack = SlackRuntime(self.clock, self.state.notifier, self.state.connect, self.state.store,
                             install=self.paths.install)  # fmt: skip
        self.state.slack = slack.status  # the same dict: status shows it live
        self.state.slack_reload = slack.reload
        approvals.desktop = self.state.notifier  # Slack clicks queued for step-up notify here
        thread = threading.Thread(target=slack.run, args=(self.stop,), name="slack", daemon=True)
        return slack, thread

    def _workers(self) -> tuple[threading.Thread, threading.Thread]:
        """The checks worker and the model worker (V1.3 step 2a)."""
        return (threading.Thread(target=self._checks, name="checks", daemon=True),
                threading.Thread(target=self._models, name="models", daemon=True))  # fmt: skip

    def _join(self, web: threading.Thread, timer: threading.Thread, worker: threading.Thread,
              slack: threading.Thread) -> None:  # fmt: skip
        web.join(STOP_TIMEOUT)
        timer.join(STOP_TIMEOUT)
        self.work.set()  # wake the checks worker so it sees the stop
        worker.join(STOP_TIMEOUT)
        if slack.is_alive():  # never started in dev mode
            slack.join(STOP_TIMEOUT)

    def _run_locked(self) -> int:
        st = breaker.on_start(self.paths.crash_state, self.paths.running_marker, self.clock.now())
        self.state.breaker = {"recent_crashes": len(st.crashes), "tripped": st.tripped}
        if st.tripped:
            log.error("service.breaker_tripped", crashes=len(st.crashes))
            self._report_trip(len(st.crashes))
            sys.stderr.write(
                "ecf-server: stopped after repeated crashes; run `ecf service start`\n"
            )
            breaker.mark_clean_exit(self.paths.running_marker)
            return EXIT_OK  # exit 0 so launchd/systemd don't restart it
        breaker.mark_running(self.paths.running_marker)
        if sys.platform == "darwin" and not self.dev:
            set_interaction_allowed(False)  # OD-163: never wait on a Keychain dialog
        applied = self._open_state()
        if st.crashed_before:
            self._report_restart(len(st.crashes))
        self.state.token = write_token(self.paths)
        sock = bind_socket(self.paths)
        server = self._api_server()
        web = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="api", daemon=True
        )
        timer = threading.Thread(target=self._timer, name="timer", daemon=True)
        worker, model_worker = self._workers()
        slack, slack_thread = self._slack_runtime()
        self._last_tick_mono = self.clock.monotonic()  # the watchdog counts from here, not __init__
        self._last_tick_wall = self.clock.now()
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        web.start()
        timer.start()
        worker.start()
        model_worker.start()
        if not self.dev:  # dev mode has no Slack; its chat is the recording fake
            slack_thread.start()
        self.work.set()  # check anything already due at start
        log.info("service.started", install=self.paths.install, migrations=applied)
        while not self.stop.wait(1.0):
            if self._signalled is not None:
                log.info("service.signal", signal=signal.Signals(self._signalled).name)
                self.stop.set()
            elif self.watchdog_expired():
                log.error("service.watchdog", seconds=self.opts.watchdog_seconds)
                self.exit_code = EXIT_CRASH
                self.stop.set()
        server.should_exit = True
        self._join(web, timer, worker, slack_thread)
        # only a stop you asked for disarms the dead-man's switch (OD-222)
        slack.close(clean_stop=self.exit_code == EXIT_OK and self.state.stopping_on_purpose)
        self._final_flush()
        sock.close()
        with suppress(FileNotFoundError):
            self.paths.socket.unlink()
        if self.exit_code == EXIT_OK:
            breaker.mark_clean_exit(self.paths.running_marker)
        log.info("service.stopped", exit_code=self.exit_code)
        return self.exit_code
