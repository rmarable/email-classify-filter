"""The Slack connection manager (SPEC §10.1; V1.2 step 3b). One thread in the service:

- idle until Slack is installed (`ecf slack install` stores the bot and app-level tokens in the
  secret store and the app and workspace IDs in settings; `slack_admin`);
- reconnects at once when `reload` is called (after an install or new tokens);
- then connects Socket Mode, and runs the output queue (`slack_out`) and the click queue
  (`slack_in`) one job at a time;
- keeps "connected" and "last connected" for `ecf status` and the "Needs you" header (OD-118);
- retries a failed connection every minute; the SDK reconnects a dropped one by itself.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

from ecf_server import (
    _slack,
    alerts,
    answers,
    approvals,
    daily,
    deadman,
    digests,
    escalations,
    health,
    needs_you,
    pause,
    review,
    slack_admin,
    slack_in,
    slack_out,
    slack_routes,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.slack_admin import APP_SECRET, BOT_SECRET
from ecf_server.slack_chat import SlackChat
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.slack_out import SlackSender

# Modules whose Slack button handlers (`slack_in.handles`) must be registered before clicks arrive.
HANDLER_MODULES = (slack_admin, escalations, approvals, answers, pause, digests)
RETRY_S = 60.0
LOOP_ALERT_AFTER = 5  # consecutive failed passes before a desktop System Error
CONNECT_ALERT_AFTER = timedelta(minutes=15)  # as posts (§13.3); a refused token alerts at once
CONNECTION = "slack_connection"
IDLE_S = 0.5
PRUNE_EVERY_S = 3600.0
ROUTES_EVERY_S = 30.0
RECHECK_EVERY_S = 3600.0  # re-invite you everywhere: finds gone channels (slack_routes.ensure)

WebFactory = Callable[[str], Any]
SocketFactory = Callable[[str, Any, Callable[[_slack.Envelope], None]], Any]


class SlackRuntime:
    def __init__(
        self,
        clock: Clock,
        notifier: Notifier,
        connect: Callable[[], sqlite3.Connection],
        secrets: Callable[[], SecretStore],
        *,
        make_web: WebFactory = _slack.Web,
        make_socket: SocketFactory = _slack.Socket,
        install: str = "default",
    ) -> None:
        self._install = install
        self._computer = needs_you.host()
        self._routes_at = -ROUTES_EVERY_S  # monotonic time of the last channel check
        self._recheck_at = -RECHECK_EVERY_S
        self._problem = ""
        self._clock, self._notifier = clock, notifier
        self._connect, self._secrets = connect, secrets
        self._make_web, self._make_socket = make_web, make_socket
        self._socket: Any = None
        self._sender: SlackSender | None = None
        self._receiver = SlackReceiver(clock)
        self._web: Any = None
        self._reload = threading.Event()
        self._lost_at: datetime | None = None  # when a live connection dropped
        self._down_since: datetime | None = None  # first failed connect in a row
        self._failures = 0  # consecutive failed passes of the loop
        self._was_connected = False
        self.status: dict[str, Any] = {"installed": False, "connected": False,
                                       "last_connected_at": None, "channels": None,
                                       "connect_error": None, "error": None}  # fmt: skip

    def start(self) -> bool:
        """Connect if Slack is installed; True when connected."""
        conn = self._connect()
        try:
            ident = slack_admin.identity(conn)
        finally:
            conn.close()
        store = self._secrets()
        bot, app = store.get(BOT_SECRET), store.get(APP_SECRET)
        if ident is None or not bot or not app:
            self.status["installed"] = False
            return False
        self.status["installed"] = True
        self._web = self._make_web(bot)
        self._remember_bot_user()
        self._sender = SlackSender(SlackChat(self._web), self._clock, self._notifier)
        inbound = Inbound(slack_admin.identity, self._clock, self._connect, self._ack,
                          self._open_view)  # fmt: skip
        self._socket = self._make_socket(app, self._web, inbound.on_envelope)
        try:
            self._socket.connect()
        except Exception as exc:  # retried on the next pass; posts wait in the queue meanwhile
            code = exc.code if isinstance(exc, _slack.SlackError) else type(exc).__name__
            log.warning("slack.connect_failed", code=code)
            self._socket = None
            self._connect_failed(code)
            return False
        self._connect_ok()
        self._mark_connected()
        return True

    def _connect_failed(self, code: str) -> None:
        """Record why Socket Mode is down; alert when Slack refuses the app-level token, or after
        15 minutes of failures while the network is up (V1.2 review, 2026-09-30)."""
        now = self._clock.now()
        self._down_since = self._down_since or now
        self.status["connect_error"] = code
        if code in slack_out.FATAL:
            detail = (f"Slack refused the app-level token ({code}); buttons in Slack don't reach"
                      " ecf. Fix: ecf slack set-tokens")  # fmt: skip
        elif now - self._down_since >= CONNECT_ALERT_AFTER and health.resolves("slack.com"):
            minutes = int((now - self._down_since).total_seconds() // 60)
            detail = (f"ecf can't connect to Slack for {minutes} minutes while the network is"
                      f" up ({code}); buttons in Slack don't reach ecf")  # fmt: skip
        else:
            return
        conn = self._connect()
        try:
            health.open_alert(conn, self._clock, self._notifier, CONNECTION, None, detail)
        finally:
            conn.close()

    def _connect_ok(self) -> None:
        self.status["connect_error"] = None
        if self._down_since is None:
            return
        self._down_since = None
        conn = self._connect()
        try:
            health.resolve_alert(conn, self._clock, self._notifier, CONNECTION, None)
        finally:
            conn.close()

    def run(self, stop: threading.Event) -> None:
        last_try = -RETRY_S
        last_prune = 0.0
        waited = 0.0
        while not stop.is_set():
            try:
                if self._reload.is_set():
                    self._reload.clear()
                    self.close()
                    self._sender = None
                    last_try = waited - RETRY_S  # connect now
                if self._socket is None and waited - last_try >= RETRY_S:
                    last_try = waited
                    self.start()
                did = self.run_once()
                if waited - last_prune >= PRUNE_EVERY_S:
                    last_prune = waited
                    self._prune()
                self._pass_ok()
            except Exception as exc:  # never let one bad pass end Slack for the process
                self._pass_failed(exc)
                stop.wait(RETRY_S)
                waited += RETRY_S
                continue
            if not did:
                stop.wait(IDLE_S)
                waited += IDLE_S

    def _pass_ok(self) -> None:
        if self._failures:
            log.info("slack.loop_recovered", after=self._failures)
        self._failures = 0
        self.status["error"] = None

    def _pass_failed(self, exc: Exception) -> None:
        """Log, show it in `ecf status` and doctor, and tell the desktop once it keeps failing
        (the thread used to end silently; V1.2 review, 2026-09-30)."""
        self._failures += 1
        name = type(exc).__name__
        log.error("slack.loop_failed", error_type=name, failures=self._failures)
        self.status["error"] = name
        if self._failures == LOOP_ALERT_AFTER:
            self._notifier.notify(alerts.title("system_error"),
                                  f"ecf's Slack work keeps failing ({name}); posts and clicks"
                                  " wait. Details: ecf logs")  # fmt: skip

    def run_once(self) -> bool:
        """One output job and one click, if any; True if either did something."""
        if self._sender is None:
            return False
        self._mark_connected()
        conn = self._connect()
        try:
            self._periodic(conn)
            escalations.sweep(conn, self._clock)
            sent = self._sender.run_once(conn)
            handled = self._receiver.run_once(conn)
        finally:
            conn.close()
        return sent or handled

    def _periodic(self, conn: sqlite3.Connection) -> None:
        """Every ROUTES_EVERY_S: channels, then "Needs you" and the dead-man's switch."""
        now = self._clock.monotonic()
        if now - self._routes_at < ROUTES_EVERY_S or self._web is None:
            return
        self._routes_at = now
        self._channels(conn, now)
        chat = SlackChat(self._web)
        steps: tuple[tuple[str, Callable[[], object]], ...] = (
            ("needs_you", lambda: needs_you.refresh(conn, self._clock, computer=self._computer)),
            ("deadman", lambda: deadman.keep_armed(conn, self._clock, self._web,
                                                   computer=self._computer)),
            ("alerts", lambda: alerts.sweep(conn, self._clock)),
            ("digests", lambda: digests.run(conn, self._clock)),
            ("review posts", lambda: review.run(conn, self._clock)),
            ("members", lambda: daily.check_members(conn, self._clock, chat, self._notifier)),
            ("daily", lambda: daily.run(conn, self._clock)),
        )  # fmt: skip
        for name, step in steps:  # each on its own: one failing never skips the rest
            try:
                step()
            except (_slack.SlackError, _slack.SlackNetworkError) as exc:  # retried next time
                log.warning(
                    "slack.housekeeping_failed",
                    step=name,
                    error_type=type(exc).__name__,
                    code=getattr(exc, "code", None),
                )

    def _channels(self, conn: sqlite3.Connection, now: float) -> None:
        """Create, record and invite whatever channel is missing."""
        recheck = now - self._recheck_at >= RECHECK_EVERY_S
        try:
            for change in slack_routes.ensure(conn, self._clock, SlackChat(self._web),
                                              self._install, recheck=recheck,
                                              notifier=self._notifier):  # fmt: skip
                log.info("slack.channels", change=change)
            problem = ""
            if recheck:
                self._recheck_at = now
        except slack_routes.ChannelProblemError as exc:
            problem = str(exc)
        except (_slack.SlackError, _slack.SlackNetworkError) as exc:
            log.warning("slack.channels_failed", error_type=type(exc).__name__,
                        code=getattr(exc, "code", None))  # fmt: skip
            return  # retried at the next check
        if problem and problem != self._problem:  # tell the person once per new problem
            self._notifier.notify(alerts.title("operator_input", "a Slack channel"), problem)
        self._problem = problem
        self.status["channels"] = problem or None

    def _remember_bot_user(self) -> None:
        """Installs from before step 8b didn't record the bot's own member ID (the member check
        leaves it out); ask Slack once."""
        conn = self._connect()
        try:
            if slack_admin.setting(conn, slack_admin.BOT_USER):
                return
            who = self._web.call("auth.test")
            with write_tx(conn):
                slack_admin.put_setting(conn, slack_admin.BOT_USER, str(who.get("user_id", "")),
                                        to_ts(self._clock.now()), actor="service")  # fmt: skip
        except (_slack.SlackError, _slack.SlackNetworkError) as exc:  # tried again next start
            log.warning("slack.bot_user_unknown", error_type=type(exc).__name__)
        finally:
            conn.close()

    def reload(self) -> None:
        """Called from the API thread: reconnect with the stored tokens on the next pass."""
        self._reload.set()

    def close(self, *, clean_stop: bool = False) -> None:
        """`clean_stop` (the service stopping on purpose) also deletes the dead-man's message."""
        if clean_stop and self._web is not None:
            conn = self._connect()
            try:
                deadman.disarm(conn, self._web)
            except (_slack.SlackError, _slack.SlackNetworkError) as exc:
                log.warning("slack.deadman_disarm_failed", error_type=type(exc).__name__)
            finally:
                conn.close()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self.status["connected"] = False

    def _mark_connected(self) -> None:
        connected = bool(self._socket is not None and self._socket.is_connected())
        self.status["connected"] = connected
        now = self._clock.now()
        if connected:
            self.status["last_connected_at"] = to_ts(now)
        if connected != self._was_connected:
            self._connection_changed(connected, now)
        self._was_connected = connected

    def _connection_changed(self, connected: bool, now: datetime) -> None:
        """Audit a Socket Mode connection lost and restored, so gaps show in `ecf logs` (Slack's
        library only logs its retries; V1.2 shadow run, 2026-09-30). The first connect isn't one."""
        if connected and self._lost_at is None:
            return
        if connected:
            down = int((now - cast(datetime, self._lost_at)).total_seconds())
            event, data = "slack.reconnected", {"down_s": down}
            self._lost_at = None
            log.info("slack.reconnected", down_s=down)
        else:
            self._lost_at = now
            event, data = "slack.disconnected", {}
            log.warning("slack.disconnected")
        conn = self._connect()
        try:
            with write_tx(conn):
                conn.execute(
                    "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                    " VALUES (?, NULL, ?, 'service', 'ok', ?)",
                    (to_ts(now), event, json.dumps(data)),
                )
        finally:
            conn.close()

    def _ack(self, envelope_id: str) -> None:
        if self._socket is not None:
            self._socket.ack(envelope_id)

    def _open_view(self, trigger_id: str, view: dict[str, Any]) -> None:
        if self._web is not None:
            self._web.call("views.open", trigger_id=trigger_id, view=view)

    def _prune(self) -> None:
        conn = self._connect()
        try:
            slack_in.prune_dedupe(conn, self._clock)
        finally:
            conn.close()
