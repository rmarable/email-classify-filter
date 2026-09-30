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

import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from ecf_server import _slack, slack_admin, slack_in
from ecf_server.clock import Clock, to_ts
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.slack_admin import APP_SECRET, BOT_SECRET
from ecf_server.slack_chat import SlackChat
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.slack_out import SlackSender

RETRY_S = 60.0
IDLE_S = 0.5
PRUNE_EVERY_S = 3600.0

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
    ) -> None:
        self._clock, self._notifier = clock, notifier
        self._connect, self._secrets = connect, secrets
        self._make_web, self._make_socket = make_web, make_socket
        self._socket: Any = None
        self._sender: SlackSender | None = None
        self._receiver = SlackReceiver(clock)
        self._web: Any = None
        self._reload = threading.Event()
        self.status: dict[str, Any] = {"installed": False, "connected": False,
                                       "last_connected_at": None}  # fmt: skip

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
        self._sender = SlackSender(SlackChat(self._web), self._clock, self._notifier)
        inbound = Inbound(slack_admin.identity, self._clock, self._connect, self._ack,
                          self._open_view)  # fmt: skip
        self._socket = self._make_socket(app, self._web, inbound.on_envelope)
        try:
            self._socket.connect()
        except Exception as exc:  # retried on the next pass; posts wait in the queue meanwhile
            log.warning("slack.connect_failed", error_type=type(exc).__name__)
            self._socket = None
            return False
        self._mark_connected()
        return True

    def run(self, stop: threading.Event) -> None:
        last_try = -RETRY_S
        last_prune = 0.0
        waited = 0.0
        while not stop.is_set():
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
            if not did:
                stop.wait(IDLE_S)
                waited += IDLE_S

    def run_once(self) -> bool:
        """One output job and one click, if any; True if either did something."""
        if self._sender is None:
            return False
        self._mark_connected()
        conn = self._connect()
        try:
            sent = self._sender.run_once(conn)
            handled = self._receiver.run_once(conn)
        finally:
            conn.close()
        return sent or handled

    def reload(self) -> None:
        """Called from the API thread: reconnect with the stored tokens on the next pass."""
        self._reload.set()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self.status["connected"] = False

    def _mark_connected(self) -> None:
        connected = bool(self._socket is not None and self._socket.is_connected())
        self.status["connected"] = connected
        if connected:
            self.status["last_connected_at"] = to_ts(self._clock.now())

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
