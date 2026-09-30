"""Slack clicks (V1.2 step 3b): the listener, the click queue and the connection manager, against
fake Socket Mode and Web API clients. Nothing here talks to Slack."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError
from ecf.ids import AddressId
from ecf_server import db, jobs, slack_in
from ecf_server._slack import Envelope, SlackError
from ecf_server.clock import Clock, FakeClock, to_ts
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.slack_admin import APP_SECRET, BOT_SECRET
from ecf_server.slack_in import Click, Inbound, SlackIdentity, SlackReceiver
from ecf_server.slack_runtime import SlackRuntime
from tests.test_slack_out import FakeWeb

ME = SlackIdentity("A1", "T1", "U1")


def _click(
    action_id: str = "approve#0",
    value: str = "g-1",
    *,
    app: str = "A1",
    team: str = "T1",
    user: str = "U1",
) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "api_app_id": app,
        "team": {"id": team},
        "user": {"id": user},
        "channel": {"id": "C1"},
        "trigger_id": "trig-1",
        "actions": [{"action_id": action_id, "value": value}],
    }


def _env(payload: dict[str, Any], eid: str = "e1", kind: str = "interactive") -> Envelope:
    return Envelope(eid, kind, payload, None, None)


class Listener:
    """An Inbound wired to recorders for acks and opened forms."""

    def __init__(self, db_path: Path, clock: FakeClock) -> None:
        self.acked: list[str] = []
        self.views: list[tuple[str, dict[str, Any]]] = []
        self.inbound = Inbound(lambda _c: ME, clock, lambda: db.connect(db_path), self.acked.append,
                               lambda t, v: self.views.append((t, v)))  # fmt: skip


def _audit(conn: sqlite3.Connection) -> list[tuple[str, str, dict[str, Any]]]:
    rows = conn.execute("SELECT event, outcome, data FROM audit ORDER BY id").fetchall()
    return [(r["event"], r["outcome"], json.loads(r["data"])) for r in rows]


def _queued(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_in'").fetchall()
    return [json.loads(r["payload"]) for r in rows]


# ---- the listener ---------------------------------------------------------------------------


def test_a_click_is_acked_and_queued(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env(_click()))
    assert ln.acked == ["e1"]
    assert _queued(conn) == [{"kind": "button", "action": "approve", "ref": "g-1",
                              "channel": "C1", "user": "U1", "values": {}}]  # fmt: skip


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        (_click(app="A9"), "wrong_app"),
        (_click(team="T9"), "wrong_team"),
        (_click(user="U9"), "not_you"),
    ],
)
def test_clicks_from_anyone_else_are_refused_and_audited_by_member_only(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, payload: dict[str, Any], why: str
) -> None:
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env(payload))
    assert ln.acked == ["e1"]  # acked anyway, so Slack doesn't retry it
    assert _queued(conn) == []
    [(event, outcome, data)] = _audit(conn)
    assert (event, outcome) == ("slack.click_refused", "denied")
    assert data == {"reason": why, "member": payload["user"]["id"]}  # no payload, no value


def test_a_repeated_envelope_is_dropped(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env(_click()))
    ln.inbound.on_envelope(_env(_click()))  # Slack's retry of the same envelope
    assert ln.acked == ["e1", "e1"] and len(_queued(conn)) == 1


def test_non_interactive_envelopes_are_acked_and_ignored(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env({"event": {}}, kind="events_api"))
    assert ln.acked == ["e1"] and _queued(conn) == [] and _audit(conn) == []


def test_a_form_button_opens_its_form_at_once_and_queues_nothing(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build(_conn: sqlite3.Connection, c: Click) -> dict[str, Any]:
        return {"callback_id": "answer", "private_metadata": c.ref}

    monkeypatch.setitem(slack_in.FORMS, "answer", build)
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env(_click("answer#0", "item-7")))
    assert ln.views == [("trig-1", {"callback_id": "answer", "private_metadata": "item-7"})]
    assert _queued(conn) == []


def test_a_form_submission_is_queued_with_capped_values(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    fields = {f"b{i}": {"input": {"value": "x" * 5000}} for i in range(15)}
    payload = {
        "type": "view_submission",
        "api_app_id": "A1",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {"callback_id": "answer", "private_metadata": "item-7",
                 "state": {"values": fields}},
    }  # fmt: skip
    Listener(db_path, clock).inbound.on_envelope(_env(payload))
    [job] = _queued(conn)
    assert (job["kind"], job["action"], job["ref"]) == ("form", "answer", "item-7")
    assert len(job["values"]) == slack_in.FIELDS_MAX
    assert all(len(v) == slack_in.VALUE_MAX for v in job["values"].values())


def test_old_dedupe_rows_are_pruned(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    ln = Listener(db_path, clock)
    ln.inbound.on_envelope(_env(_click(), "old"))
    clock.advance(2 * 86400)
    ln.inbound.on_envelope(_env(_click(), "new"))
    assert slack_in.prune_dedupe(conn, clock) == 1
    ids = [r["payload_id"] for r in conn.execute("SELECT payload_id FROM slack_dedupe")]
    assert ids == ["new"]


# ---- the click queue ------------------------------------------------------------------------


def _enqueue(conn: sqlite3.Connection, clock: FakeClock, action: str) -> None:
    click: dict[str, Any] = {"kind": "button", "action": action, "ref": "g-1", "channel": "C1",
                             "user": "U1", "values": {}}  # fmt: skip
    jobs.enqueue(conn, clock, jobs.Queue.SLACK_IN, AddressId("U1"), click, timeout_s=60,
                 max_attempts=3)  # fmt: skip


def _state(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT state FROM jobs WHERE queue = 'slack_in'").fetchone()["state"])


def test_the_registered_handler_runs(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Click] = []

    def handler(_c: sqlite3.Connection, _clock: Clock, click: Click) -> None:
        seen.append(click)

    monkeypatch.setitem(slack_in.HANDLERS, "approve", handler)
    _enqueue(conn, clock, "approve")
    r = SlackReceiver(clock)
    assert r.run_once(conn) and not r.run_once(conn)
    assert [(c.action, c.ref, c.user) for c in seen] == [("approve", "g-1", "U1")]
    assert _state(conn) == "done"


def test_an_unknown_action_is_audited_and_done(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _enqueue(conn, clock, "no_such_action")
    assert SlackReceiver(clock).run_once(conn)
    assert _audit(conn) == [("slack.click_unknown", "ok", {"action": "no_such_action"})]
    assert _state(conn) == "done"


def test_a_refused_click_is_audited_not_retried(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_c: sqlite3.Connection, _clock: Clock, _click: Click) -> None:
        raise ConflictError("already decided")

    monkeypatch.setitem(slack_in.HANDLERS, "approve", handler)
    _enqueue(conn, clock, "approve")
    assert SlackReceiver(clock).run_once(conn)
    [(event, _, data)] = _audit(conn)
    assert event == "slack.click_failed" and data["action"] == "approve"
    assert _state(conn) == "done"


def test_a_crashing_handler_is_retried_then_dead(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_c: sqlite3.Connection, _clock: Clock, _click: Click) -> None:
        raise RuntimeError("bug")

    monkeypatch.setitem(slack_in.HANDLERS, "approve", handler)
    _enqueue(conn, clock, "approve")
    r = SlackReceiver(clock)
    for _ in range(3):
        assert r.run_once(conn)
        assert _state(conn) in ("queued", "dead")
        clock.advance(3600)
    assert _state(conn) == "dead"


# ---- the connection manager -----------------------------------------------------------------


class FakeSocket:
    instances: list[FakeSocket] = []  # noqa: RUF012 - test registry

    def __init__(self, app: str, web: Any, on_envelope: Callable[[Envelope], None]) -> None:
        self.app, self.web, self.on_envelope = app, web, on_envelope
        self.acked: list[str] = []
        self.connected = False
        self.fail = False
        FakeSocket.instances.append(self)

    def connect(self) -> None:
        if self.fail:
            raise OSError("no route")
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def ack(self, envelope_id: str) -> None:
        self.acked.append(envelope_id)


def _install(conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore) -> None:
    now = to_ts(clock.now())
    for key, value in (("slack_app_id", "A1"), ("slack_team_id", "T1"),
                       ("slack_member_id", "U1")):  # fmt: skip
        with db.write_tx(conn):
            conn.execute("INSERT INTO settings VALUES (?, ?, ?, 'test')",
                         (key, json.dumps(value), now))  # fmt: skip
    store.set(BOT_SECRET, "xoxb-test")
    store.set(APP_SECRET, "xapp-test")


def _runtime(
    db_path: Path,
    clock: FakeClock,
    store: MemorySecretStore,
    web: FakeWeb,
    *,
    fail: bool = False,
    notifier: FakeNotifier | None = None,
) -> SlackRuntime:
    FakeSocket.instances.clear()

    def make_socket(app: str, w: Any, on: Callable[[Envelope], None]) -> FakeSocket:
        s = FakeSocket(app, w, on)
        s.fail = fail
        return s

    return SlackRuntime(clock, notifier or FakeNotifier(), lambda: db.connect(db_path),
                        lambda: store, make_web=lambda _token: web,
                        make_socket=make_socket)  # fmt: skip


def test_not_installed_stays_idle(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    rt = _runtime(db_path, clock, MemorySecretStore(), FakeWeb())
    assert not rt.start() and not rt.run_once()
    assert rt.status == {"installed": False, "connected": False, "last_connected_at": None,
                         "channels": None}  # fmt: skip
    assert FakeSocket.instances == []


def test_installed_connects_acks_and_runs_both_queues(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, web = MemorySecretStore(), FakeWeb()
    _install(conn, clock, store)
    handled: list[str] = []
    monkeypatch.setitem(slack_in.HANDLERS, "approve", lambda _c, _k, c: handled.append(c.ref))
    rt = _runtime(db_path, clock, store, web)
    assert rt.start()
    [sock] = FakeSocket.instances
    assert sock.app == "xapp-test" and sock.web is web
    assert rt.status["installed"] and rt.status["connected"]
    assert rt.status["last_connected_at"] == to_ts(clock.now())

    sock.on_envelope(_env(_click()))
    assert sock.acked == ["e1"]
    from ecf_server import slack_out  # noqa: PLC0415 - only this test posts
    from ecf_server.chat import Card, RouteRef  # noqa: PLC0415

    slack_out.enqueue_post(conn, clock, key="k", route=RouteRef("C1"), card=Card("hi"))
    assert rt.run_once()
    assert handled == ["g-1"] and web.methods()[-1] == "chat.postMessage"
    assert "conversations.create" in web.methods()  # the first pass made the channels

    rt.close()
    assert not sock.connected and rt.status["connected"] is False


def test_a_form_opens_through_views_open(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, web = MemorySecretStore(), FakeWeb()
    _install(conn, clock, store)
    monkeypatch.setitem(slack_in.FORMS, "answer", lambda _conn, _c: {"callback_id": "answer"})
    rt = _runtime(db_path, clock, store, web)
    assert rt.start()
    FakeSocket.instances[0].on_envelope(_env(_click("answer#0")))
    assert web.calls[-1] == ("views.open", {"trigger_id": "trig-1",
                                            "view": {"callback_id": "answer"}})  # fmt: skip


def test_a_failed_connection_is_retried_later(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    store = MemorySecretStore()
    _install(conn, clock, store)
    rt = _runtime(db_path, clock, store, FakeWeb(), fail=True)
    assert not rt.start()
    assert rt.status["installed"] and not rt.status["connected"]
    assert rt.status["last_connected_at"] is None


def test_a_channel_problem_is_shown_and_notified_once(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    store, web, n = MemorySecretStore(), FakeWeb(), FakeNotifier()
    _install(conn, clock, store)
    rt = _runtime(db_path, clock, store, web, notifier=n)
    assert rt.start()
    for _ in range(3):  # the workspace doesn't let apps create channels
        web.fail["conversations.create"] = [SlackError("conversations.create", "restricted_action")]
        rt.run_once()
        clock.advance(31)
    assert rt.status["channels"] and "ecf-default-summary" in rt.status["channels"]
    assert [t for t, _ in n.sent] == ["ecf: Slack channel needs you"]  # once, not every pass
    rt.run_once()  # the person made the channel... here creation just works again
    assert rt.status["channels"] is None


def test_a_clean_stop_deletes_the_dead_mans_message_and_a_crash_leaves_it(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    class Web(FakeWeb):
        def call(self, method: str, **params: Any) -> dict[str, Any]:
            r = super().call(method, **params)
            return r | {"scheduled_message_id": "Q1"} if method == "chat.scheduleMessage" else r

    store = MemorySecretStore()
    _install(conn, clock, store)
    web = Web()
    rt = _runtime(db_path, clock, store, web)
    assert rt.start()
    rt.run_once()  # channels, "Needs you", and the dead-man's message
    assert "chat.scheduleMessage" in web.methods()
    rt.close()  # a crash or watchdog stop: the message stays scheduled, so it will post
    assert "chat.deleteScheduledMessage" not in web.methods()
    web2 = Web()
    rt2 = _runtime(db_path, clock, store, web2)
    assert rt2.start()
    rt2.run_once()  # still far enough ahead: kept, not scheduled again
    assert "chat.scheduleMessage" not in web2.methods()
    rt2.close(clean_stop=True)
    method, params = web2.calls[-1]
    assert (method, params["scheduled_message_id"]) == ("chat.deleteScheduledMessage", "Q1")
