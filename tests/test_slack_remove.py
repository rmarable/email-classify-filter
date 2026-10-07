"""`ecf slack remove` (SPEC §10.1; v1.0.0 release, 2026-10-07): the app deleted or its bot token
revoked through a fake Slack, an app or tokens already gone, settings, tokens, channels, message
records and Slack jobs forgotten, a fresh install afterwards, item cards after it, step-up, the
audit row, the Slack thread held idle, the route, and the CLI. Nothing here talks to Slack."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_slack
from ecf.cli import app
from ecf.errors import (
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
    StepupRequiredError,
)
from ecf.ids import AddressId, StableId
from ecf.paths import Paths
from ecf.prompts import NO_TERMINAL
from ecf.status import Status
from ecf_server import approvals, health, items, slack_admin, slack_out, slack_remove, stepup
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.actions import Planned
from ecf_server.api import ServiceState
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper
from tests.test_addresses import call, make_state
from tests.test_destroy import BOT, CONFIG, ME, Env, make_env
from tests.test_export_keys import ApiClient
from tests.test_slack_admin import APP, FakeSlack
from tests.test_slack_admin import BOT as REAL_BOT
from tests.test_slack_admin import CONFIG as REAL_CONFIG
from tests.test_slack_in import FakeSocket, _runtime  # pyright: ignore[reportPrivateUsage]
from tests.test_slack_out import FakeWeb


@pytest.fixture
def env(conn: sqlite3.Connection, tmp_path: Path) -> Env:
    """Slack installed with a summary and two address channels, the dead-man's message, alert
    email and secrets (as for `ecf destroy`), plus a posted card, a queued post and an open
    Slack Delivery Failed alert."""
    e = make_env(conn, tmp_path / "root")
    now = to_ts(e.clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO slack_messages (key, channel, ts, created_at, updated_at)"
                     " VALUES ('needs-you', 'CSUM', '1.0001', ?, ?)", (now, now))  # fmt: skip
    slack_out.enqueue_post(conn, e.clock, key="digest:x", route=RouteRef("CAP"),
                           card=Card("a digest"))  # fmt: skip
    health.open_alert(conn, e.clock, e.notifier, slack_out.ALERT, None, "Slack refused ecf")
    return e


def _nonce(e: Env) -> str:
    issued = stepup.issue(e.conn, e.clock, FakeStepper(), "slack_remove", {})
    assert stepup.verify(e.conn, e.clock, FakeStepper(), issued.nonce_id) == "verified"
    return issued.nonce_id


def _remove(e: Env, *, token: str | None = None, typed: str = "t") -> dict[str, Any]:
    return slack_remove.run(e.conn, e.clock, e.store, e.web, e.notifier, install="t",
                            typed=typed, config_token=token, nonce=_nonce(e))  # fmt: skip


def _slack_settings(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT key FROM settings WHERE key LIKE 'slack%' ORDER BY key")
    return [r[0] for r in rows]


def _assert_forgotten(e: Env) -> None:
    c = e.conn
    assert _slack_settings(c) == []
    assert slack_admin.identity(c) is None
    assert slack_admin.status(c) == {"app_id": None, "team_id": None, "member": None,
                                     "pending_member": None, "pending_app_id": None}  # fmt: skip
    assert c.execute("SELECT count(*) FROM routes").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM slack_messages").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM jobs WHERE queue IN ('slack_out', 'slack_in')"
                     " AND state != 'done'").fetchone()[0] == 0  # fmt: skip
    assert e.store.get("slack/bot") is None and e.store.get("slack/app") is None


def _audit(conn: sqlite3.Connection, event: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT data FROM audit WHERE event = ? ORDER BY id", (event,))
    return [json.loads(r[0]) for r in rows]


# ---- the service's part -------------------------------------------------------------------------


def test_with_tokens_the_bot_token_is_revoked_and_everything_slack_is_forgotten(env: Env) -> None:
    r = _remove(env)
    # the notice (a DM and the summary channel), the dead-man's message, then the bot token;
    # channels are left as they are
    assert env.webs[BOT].methods() == ["chat.postMessage", "chat.postMessage",
                                       "chat.deleteScheduledMessage", "auth.revoke"]  # fmt: skip
    assert [p["channel"] for m, p in env.webs[BOT].calls if m == "chat.postMessage"] == [ME, "CSUM"]
    assert env.webs[CONFIG].calls == []
    _assert_forgotten(env)
    assert env.store.get("mailbox/ap") == "x" and env.store.get("export-signing-seed") == "x"
    assert env.conn.execute("SELECT count(*) FROM addresses").fetchone()[0] == 2  # kept
    assert r["slack_app"] == {"app_id": "A1", "result": "revoked"}
    assert r["forgot"]["channels"] == 3 and r["forgot"]["messages"] == 1
    assert r["forgot"]["jobs"] >= 1
    assert r["notice"] == {"slack_posts": 2, "email": True}
    assert r["left"] == ["Slack app A1 still exists (its bot token is revoked): delete it at"
                         " https://api.slack.com/apps/A1 (Settings, Basic Information,"
                         " Delete App)"]  # fmt: skip
    assert health.open_alerts(env.conn) == []  # Slack Delivery Failed resolved
    titles = [t for t, _ in env.notifier.sent]
    assert "[ecf-alert] Security Notice" in titles
    [row] = _audit(env.conn, "slack.removed")
    assert row["app_id"] == "A1" and row["team_id"] == "T1" and row["person"]
    dump = "\n".join(env.conn.iterdump())
    assert BOT not in dump and CONFIG not in dump  # no token in the audit or anywhere
    assert _audit(env.conn, "security.notice")


def test_a_config_token_deletes_the_app(env: Env) -> None:
    r = _remove(env, token=CONFIG)
    assert env.webs[CONFIG].calls == [("apps.manifest.delete", {"app_id": "A1"})]
    assert "auth.revoke" not in env.webs[BOT].methods()
    assert r["slack_app"] == {"app_id": "A1", "result": "deleted"} and r["left"] == []
    _assert_forgotten(env)


def test_an_app_already_deleted_in_slack_counts_as_done(env: Env) -> None:
    gone = SlackError("chat.postMessage", "invalid_auth")
    env.webs[BOT].fail["chat.postMessage"] = [gone, gone]
    env.webs[BOT].fail["chat.deleteScheduledMessage"] = [
        SlackError("chat.deleteScheduledMessage", "invalid_auth")
    ]
    env.webs[BOT].fail["auth.revoke"] = [SlackError("auth.revoke", "account_inactive")]
    env.webs[CONFIG].fail["apps.manifest.delete"] = [
        SlackError("apps.manifest.delete", "app_not_found")
    ]
    r = _remove(env, token=CONFIG)
    assert r["slack_app"] == {"app_id": "A1", "result": "deleted", "note": "app_not_found"}
    assert r["notice"]["slack_posts"] == 0 and r["notice"]["email"] is True
    _assert_forgotten(env)


def test_a_bot_token_slack_no_longer_accepts_counts_as_revoked(env: Env) -> None:
    env.webs[BOT].fail["auth.revoke"] = [SlackError("auth.revoke", "token_revoked")]
    r = _remove(env)
    assert r["slack_app"] == {"app_id": "A1", "result": "revoked", "note": "token_revoked"}
    _assert_forgotten(env)


def test_tokens_missing_asks_nothing_of_slack_and_says_what_is_left(env: Env) -> None:
    env.store.delete("slack/bot")
    env.store.delete("slack/app")
    r = _remove(env)
    assert env.webs[BOT].calls == [] and env.webs[CONFIG].calls == []
    assert r["slack_app"] == {"app_id": "A1", "result": "no_token"}
    assert r["deadman"] == {"result": "no_token"}
    assert "Slack app A1 still exists (its bot token may still work)" in r["left"][0]
    _assert_forgotten(env)


def test_a_refused_revoke_is_reported_and_the_run_goes_on(env: Env) -> None:
    env.webs[BOT].fail["auth.revoke"] = [SlackError("auth.revoke", "ratelimited")]
    r = _remove(env)
    assert r["slack_app"] == {"app_id": "A1", "result": "failed", "code": "ratelimited"}
    _assert_forgotten(env)


def test_slack_unreachable_forgets_nothing(env: Env) -> None:
    env.webs[BOT].fail["auth.revoke"] = [SlackNetworkError("auth.revoke")]
    with pytest.raises(ServiceUnavailableError, match="nothing was removed"):
        _remove(env)
    assert env.store.get("slack/bot") == BOT
    assert slack_admin.identity(env.conn) is not None
    assert env.conn.execute("SELECT count(*) FROM routes").fetchone()[0] == 2
    assert _audit(env.conn, "slack.removed") == []
    r = _remove(env)  # a retry finishes
    assert r["slack_app"]["result"] == "revoked"
    _assert_forgotten(env)


def test_refusals_change_nothing(env: Env) -> None:
    with pytest.raises(InvalidInputError, match="type the install name"):
        _remove(env, typed="prod")
    with pytest.raises(StepupRequiredError) as ei:
        slack_remove.run(env.conn, env.clock, env.store, env.web, env.notifier, install="t",
                         typed="t", config_token=None, nonce=None)  # fmt: skip
    assert ei.value.extra["purpose"] == "slack_remove"
    assert env.webs[BOT].calls == [] and env.store.get("slack/bot") == BOT
    assert slack_admin.identity(env.conn) is not None


def test_step_up_is_bound_to_the_recorded_app(env: Env) -> None:
    issued = stepup.issue(env.conn, env.clock, FakeStepper(), "slack_remove", {})
    assert "remove Slack from this install (app A1, workspace T1)" in issued.prompt
    stepup.verify(env.conn, env.clock, FakeStepper(), issued.nonce_id)
    with write_tx(env.conn):  # the recorded app changes after the step-up
        slack_admin.put_setting(env.conn, slack_admin.APP_ID, "A2", to_ts(env.clock.now()),
                                actor="test")  # fmt: skip
    with pytest.raises(StepupRequiredError):
        slack_remove.run(env.conn, env.clock, env.store, env.web, env.notifier, install="t",
                         typed="t", config_token=None, nonce=issued.nonce_id)  # fmt: skip
    assert env.webs[BOT].calls == []


def test_nothing_to_remove(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(NotFoundError, match="nothing to remove"):
        slack_remove.run(conn, clock, MemorySecretStore(), lambda _t: FakeWeb(), FakeNotifier(),
                         install="t", typed="t", config_token=None, nonce=None)  # fmt: skip


def test_a_pending_app_is_removed_too(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """An install that stopped after `apps.manifest.create`: the app is deleted with a token."""
    slack = FakeSlack()
    slack_admin.create_app(conn, clock, slack.web, REAL_CONFIG, "t")
    e = Env(conn, Path("/nonexistent"))
    e.clock = clock
    e.webs = {REAL_CONFIG: FakeWeb()}
    r = _remove(e, token=REAL_CONFIG)
    assert e.webs[REAL_CONFIG].calls == [("apps.manifest.delete", {"app_id": "A1"})]
    assert r["slack_app"]["result"] == "deleted"
    assert _slack_settings(conn) == []


# ---- afterwards ---------------------------------------------------------------------------------


def test_install_works_again_from_scratch(conn: sqlite3.Connection, clock: FakeClock) -> None:
    store = MemorySecretStore()
    slack = FakeSlack()
    slack_admin.create_app(conn, clock, slack.web, REAL_CONFIG, "t")
    slack_admin.install(conn, clock, store, slack.web, bot_token=REAL_BOT, app_token=APP,
                        member=ME)  # fmt: skip
    e = Env(conn, Path("/nonexistent"))
    e.clock = clock
    slack_remove.run(conn, clock, store, slack.web, e.notifier, install="t", typed="t",
                     config_token=None, nonce=_nonce(e))  # fmt: skip
    assert "auth.revoke" in slack.methods()
    assert slack_admin.status(conn)["app_id"] is None
    # a new app (another workspace even): `ecf slack install` runs as on a fresh install
    other = FakeSlack(app_id="A9", team_id="T9")
    assert slack_admin.create_app(conn, clock, other.web, REAL_CONFIG, "t")["app_id"] == "A9"
    s = slack_admin.install(conn, clock, store, other.web, bot_token=REAL_BOT + "9",
                            app_token=APP, member="U0ME9")  # fmt: skip
    assert s["app_id"] == "A9" and s["team_id"] == "T9" and s["pending_member"] == "U0ME9"
    assert store.get("slack/bot") == REAL_BOT + "9"


def _awaiting_approval(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live' WHERE address_id = 'ap'")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts="{}", subject="Invoice 42",
                      sender="billing@vendor-a.example")  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    approvals.request(conn, clock, sid, [Planned("archive")], member=ME)


def test_item_cards_after_removal_and_a_new_install(env: Env) -> None:
    """An old card's record goes with the install; editing it does nothing until Slack is back;
    a new install's channel gets the card again (`post_held_cards`)."""
    sid = "a" * 64
    _awaiting_approval(env.conn, env.clock, sid)
    now = to_ts(env.clock.now())
    with write_tx(env.conn):  # the card was posted to the old channel
        env.conn.execute("DELETE FROM jobs WHERE queue = 'slack_out'")
        env.conn.execute("INSERT INTO slack_messages (key, channel, ts, created_at,"
                         " updated_at) VALUES (?, 'CAP', '2.0001', ?, ?)",
                         (f"item:{sid}", now, now))  # fmt: skip
    _remove(env)
    approvals.edit_card(env.conn, env.clock, sid, "Archived")  # no Slack: nothing queued
    assert approvals.post_held_cards(env.conn, env.clock) >= 0
    assert env.conn.execute("SELECT count(*) FROM jobs WHERE queue = 'slack_out'"
                            " AND state != 'done'").fetchone()[0] == 0  # fmt: skip
    with write_tx(env.conn):  # a new install and its first channel pass
        for k, v in (("slack_app_id", "A9"), ("slack_team_id", "T9"), ("slack_member_id", ME)):
            slack_admin.put_setting(env.conn, k, v, now, actor="test")
        env.conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                         " VALUES ('ap', 'slack', 'CNEW', 'ecf-t-ap')")  # fmt: skip
    assert approvals.post_held_cards(env.conn, env.clock) == 1
    [p] = [json.loads(r[0]) for r in env.conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out' AND state = 'queued'")]  # fmt: skip
    assert p["key"] == f"item:{sid}" and p["channel"] == "CNEW"
    assert slack_out.message_ref(env.conn, f"item:{sid}") is None  # posted fresh, not edited


# ---- the Slack thread ---------------------------------------------------------------------------


def _wait(cond: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_held_keeps_the_thread_idle_and_reconnects_only_if_still_installed(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    store = MemorySecretStore()
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", "U1")):
            slack_admin.put_setting(conn, k, v, now, actor="test")
    store.set("slack/bot", "xoxb-test")
    store.set("slack/app", "xapp-test")
    rt = _runtime(db_path, clock, store, FakeWeb())
    stop = threading.Event()
    thread = threading.Thread(target=rt.run, args=(stop,), daemon=True)
    thread.start()
    try:
        assert _wait(lambda: bool(rt.status["connected"]))
        with rt.held():  # a removal that stops partway: still installed afterwards
            assert not FakeSocket.instances[-1].connected
            assert rt.status["installed"] is False and rt.status["connected"] is False
            first = len(FakeSocket.instances)
        assert _wait(lambda: bool(rt.status["connected"]))
        assert len(FakeSocket.instances) == first + 1
        with rt.held():  # a removal that finishes
            store.delete("slack/bot")
            store.delete("slack/app")
            with write_tx(conn):
                conn.execute("DELETE FROM settings WHERE key LIKE 'slack%'")
            count = len(FakeSocket.instances)
        time.sleep(1.5)  # a few idle passes
        assert len(FakeSocket.instances) == count and rt.status["installed"] is False
        assert rt.status["connected"] is False
    finally:
        stop.set()
        thread.join(10)


# ---- the route ----------------------------------------------------------------------------------


def test_route_needs_step_up_and_holds_the_slack_thread(env: Env, db_path: Path) -> None:
    st = make_state(db_path, env.store)
    st.clock, st.slack_web, st.notifier = env.clock, env.web, env.notifier
    held: list[str] = []

    class Hold:
        def __enter__(self) -> None:
            held.append("in")

        def __exit__(self, *_: object) -> None:
            held.append("out")

    st.slack_hold = Hold
    r = call(st, "POST", "/v1/slack/remove", {"install": "t"})
    assert r.status_code == 403, r.text  # step-up first
    assert env.store.get("slack/bot") == BOT
    body = {"install": "t", "config_token": f" {CONFIG}\n", "stepup_nonce": _nonce(env)}
    r = call(st, "POST", "/v1/slack/remove", body)
    assert r.status_code == 200, r.text
    assert r.json()["slack_app"]["result"] == "deleted"  # the pasted token, trimmed
    assert held == ["in", "out", "in", "out"]
    assert CONFIG not in r.text and BOT not in r.text
    s = call(st, "GET", "/v1/slack").json()
    assert s["app_id"] is None and s["channels"] == []


# ---- the CLI ------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(env: Env, db_path: Path, monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path) -> ServiceState:  # fmt: skip
    st = make_state(db_path, env.store)
    st.clock, st.slack_web, st.notifier = env.clock, env.web, env.notifier
    st.stepper = FakeStepper()

    def client(_p: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_slack, "LocalClient", client)
    monkeypatch.setattr(ecf.cli_slack, "require_terminal", lambda: None)

    def secret(_prompt: str) -> str:
        return CONFIG

    monkeypatch.setattr(ecf.cli_slack, "hidden", secret)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    return st


def test_cli_removes_after_the_typed_name_and_step_up(cli_env: ServiceState, env: Env) -> None:
    del cli_env
    r = CliRunner().invoke(app, ["--install", "t", "slack", "remove"], input="t\n")
    assert r.exit_code == 0, r.output
    out = r.output
    assert "This takes Slack off the install t:" in out
    assert "Slack app A1 (workspace T1): deleted with --config-token" in out
    assert "its 3 channel(s) are forgotten, not archived" in out
    assert "Step-up: ecf: remove Slack from this install (app A1, workspace T1)" in out
    assert "Revoked app A1's bot token." in out
    assert "Left to do: Slack app A1 still exists (its bot token is revoked)" in out
    assert "`ecf slack install` starts again from scratch" in out
    _assert_forgotten(env)
    s = CliRunner().invoke(app, ["--install", "t", "slack", "status"])
    assert s.output.strip() == "Slack: not installed"


def test_cli_config_token_deletes_the_app(cli_env: ServiceState, env: Env) -> None:
    del cli_env
    r = CliRunner().invoke(app, ["--install", "t", "slack", "remove", "--config-token"],
                           input="t\n")  # fmt: skip
    assert r.exit_code == 0, r.output
    assert env.webs[CONFIG].methods() == ["apps.manifest.delete"]
    assert "Deleted Slack app A1." in r.output and "Left to do" not in r.output


def test_cli_wrong_name_removes_nothing(cli_env: ServiceState, env: Env) -> None:
    del cli_env
    r = CliRunner().invoke(app, ["--install", "t", "slack", "remove"], input="prod\n")
    assert r.exit_code == 1
    assert "That isn't t; nothing was removed." in r.output
    assert "Step-up" not in r.output
    assert env.store.get("slack/bot") == BOT and slack_admin.identity(env.conn) is not None


def test_cli_needs_a_terminal() -> None:
    r = CliRunner().invoke(app, ["slack", "remove"])
    assert isinstance(r.exception, InvalidInputError)
    assert r.exception.detail == NO_TERMINAL
    assert "This takes Slack off" not in r.output


def test_help_marks_step_up() -> None:
    r = CliRunner().invoke(app, ["slack", "remove", "--help"])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", r.output)
    assert r.exit_code == 0
    assert "(step-up)" in plain and "--config-token" in plain
