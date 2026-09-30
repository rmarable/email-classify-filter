"""Slack install, tokens and member ID (V1.2 step 4), against a fake Slack Web API. Nothing here
talks to Slack."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from ecf.errors import ConflictError, InvalidInputError, NotFoundError, StepupRequiredError
from ecf.log import configure_logging
from ecf_server import _slack, db, slack_admin, slack_out, stepup
from ecf_server._slack import Envelope, SlackError
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import FakeClock
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.slack_admin import APP_SECRET, BOT_SECRET, CONFIRM_NONCE
from ecf_server.slack_chat import SlackChat
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.slack_out import SlackSender
from ecf_server.stepper import FakeStepper

CONFIG = "xoxe.xoxp-1-config-token-0000"
BOT = "xoxb-1111-2222-bottokenvalue"
APP = "xapp-1-A1-3333-apptokenvalue"
SECRETS = ("client-secret-value", "signing-secret-value", "verification-token-value")


class FakeSlack:
    """One fake workspace: every Web API call is recorded with the token that made it."""

    def __init__(self, *, app_id: str = "A1", team_id: str = "T1") -> None:
        self.app_id, self.team_id = app_id, team_id
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: dict[str, SlackError] = {}
        self.permissions_updated = True

    def web(self, token: str) -> Any:
        slack = self

        class View:
            def call(self, method: str, **params: Any) -> dict[str, Any]:
                return slack.call(token, method, params)

        return View()

    def call(self, token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((token, method, params))
        if method in self.fail:
            raise self.fail[method]
        creds = dict(zip(("client_secret", "signing_secret", "verification_token"), SECRETS,
                         strict=True))  # fmt: skip
        replies: dict[str, dict[str, Any]] = {
            "apps.manifest.create": {"app_id": self.app_id, "credentials": creds,
                                     "oauth_authorize_url": "https://x"},
            "apps.manifest.update": {"app_id": self.app_id,
                                     "permissions_updated": self.permissions_updated},
            "auth.test": {"team_id": self.team_id, "user_id": "UBOT", "bot_id": "B1"},
            "bots.info": {"bot": {"id": "B1", "app_id": self.app_id}},
            "apps.connections.open": {"url": "wss://example.invalid/"},
        }  # fmt: skip
        if method == "chat.postMessage":
            return {"ok": True, "channel": "D" + params["channel"][1:], "ts": "1.0001"}
        return {"ok": True, **replies.get(method, {})}

    def methods(self) -> list[str]:
        return [m for _, m, _ in self.calls]

    def posts(self) -> list[dict[str, Any]]:
        return [p for _, m, p in self.calls if m == "chat.postMessage"]


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()


@pytest.fixture
def store() -> MemorySecretStore:
    return MemorySecretStore()


def _install(conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore,
             slack: FakeSlack, member: str = "U0ME1") -> None:  # fmt: skip
    slack_admin.create_app(conn, clock, slack.web, CONFIG, "default")
    slack_admin.install(conn, clock, store, slack.web, bot_token=BOT, app_token=APP,
                        member=member)  # fmt: skip


def _dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def _click(user: str, action_id: str, value: str, eid: str) -> Envelope:
    payload = {
        "type": "block_actions",
        "api_app_id": "A1",
        "team": {"id": "T1"},
        "user": {"id": user},
        "channel": {"id": "D1"},
        "actions": [{"action_id": action_id, "value": value}],
    }
    return Envelope(eid, "interactive", payload, None, None)


def _audit_events(conn: sqlite3.Connection) -> list[str]:
    return [r["event"] for r in conn.execute("SELECT event FROM audit ORDER BY id")]


# ---- install --------------------------------------------------------------------------------


def test_the_manifest_asks_for_socket_mode_and_the_tested_scopes() -> None:
    m = slack_admin.manifest("default")
    assert m["settings"]["socket_mode_enabled"] is True
    assert m["oauth_config"]["scopes"]["bot"] == list(slack_admin.BOT_SCOPES)
    assert "groups:history" not in m["oauth_config"]["scopes"]["bot"]  # §10.1: never read
    assert len(slack_admin.manifest("x" * 40)["display_information"]["name"]) <= 35


def test_create_app_keeps_only_the_app_id(
    conn: sqlite3.Connection, clock: FakeClock, slack: FakeSlack
) -> None:
    r = slack_admin.create_app(conn, clock, slack.web, CONFIG, "default")
    assert r == {"app_id": "A1", "settings_url": "https://api.slack.com/apps/A1"}
    assert slack.calls[0][0] == CONFIG
    dump = _dump(conn)
    assert CONFIG not in dump and not any(s in dump for s in SECRETS)
    assert slack_admin.status(conn)["pending_app_id"] == "A1"


def test_install_stores_tokens_and_waits_for_the_member_click(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    _install(conn, clock, store, slack)
    assert store.get(BOT_SECRET) == BOT and store.get(APP_SECRET) == APP
    dump = _dump(conn)
    assert BOT not in dump and APP not in dump
    ident = slack_admin.identity(conn)
    assert ident is not None and (ident.member, ident.pending) == ("", "U0ME1")
    [dm] = slack.posts()
    assert dm["channel"] == "U0ME1" and "username" not in dm  # a DM: never customized
    button = dm["blocks"][-1]["elements"][0]
    assert button["action_id"].startswith("confirm_member") and button["value"]
    assert slack.methods()[:5] == ["apps.manifest.create", "auth.test", "bots.info",
                                   "apps.connections.open", "chat.postMessage"]  # fmt: skip
    assert "slack.installed" in _audit_events(conn)


@pytest.mark.parametrize(
    ("bot", "app", "member", "why"),
    [
        ("xoxp-user-token", APP, "U0ME1", "xoxb-"),
        (BOT, "xoxb-not-app", "U0ME1", "xapp-"),
        (BOT, APP, "not-a-member", "member ID"),
    ],
)
def test_install_refuses_bad_input(
    conn: sqlite3.Connection,
    clock: FakeClock,
    store: MemorySecretStore,
    slack: FakeSlack,
    bot: str,
    app: str,
    member: str,
    why: str,
) -> None:
    with pytest.raises(InvalidInputError, match=why):
        slack_admin.install(conn, clock, store, slack.web, bot_token=bot, app_token=app,
                            member=member)  # fmt: skip
    assert store.get(BOT_SECRET) is None and slack_admin.identity(conn) is None


def test_install_refuses_a_token_from_another_app(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    slack_admin.create_app(conn, clock, slack.web, CONFIG, "default")
    slack.app_id = "A9"  # the bot token belongs to some other app
    with pytest.raises(InvalidInputError, match="A9"):
        slack_admin.install(conn, clock, store, slack.web, bot_token=BOT, app_token=APP,
                            member="U0ME1")  # fmt: skip
    assert store.get(BOT_SECRET) is None


def test_a_rejected_token_or_failed_dm_stores_nothing(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    slack.fail["auth.test"] = SlackError("auth.test", "invalid_auth")
    with pytest.raises(InvalidInputError, match="invalid_auth"):
        slack_admin.install(conn, clock, store, slack.web, bot_token=BOT, app_token=APP,
                            member="U0ME1")  # fmt: skip
    del slack.fail["auth.test"]
    slack.fail["chat.postMessage"] = SlackError("chat.postMessage", "channel_not_found")
    with pytest.raises(InvalidInputError, match="couldn't DM"):
        slack_admin.install(conn, clock, store, slack.web, bot_token=BOT, app_token=APP,
                            member="U0404")  # fmt: skip
    assert store.get(BOT_SECRET) is None and slack_admin.identity(conn) is None


def test_install_twice_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    _install(conn, clock, store, slack)
    with pytest.raises(ConflictError):
        slack_admin.create_app(conn, clock, slack.web, CONFIG, "default")
    with pytest.raises(ConflictError, match="set-tokens"):
        slack_admin.install(conn, clock, store, slack.web, bot_token=BOT, app_token=APP,
                            member="U0ME1")  # fmt: skip


# ---- member confirmation --------------------------------------------------------------------


class Clicks:
    """Clicks through the real listener and click queue."""

    def __init__(self, db_path: Path, clock: FakeClock) -> None:
        self.clock = clock
        self.inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                               lambda _e: None, lambda _t, _v: None)  # fmt: skip
        self.n = 0

    def click(self, conn: sqlite3.Connection, user: str, action: str, value: str) -> None:
        self.n += 1
        self.inbound.on_envelope(_click(user, f"{action}#0", value, f"e{self.n}-{value}"))
        SlackReceiver(self.clock).run_once(conn)


def _nonce(conn: sqlite3.Connection) -> str:
    return slack_admin._settings(conn, CONFIRM_NONCE)[CONFIRM_NONCE]  # pyright: ignore[reportPrivateUsage]


def test_only_the_pending_member_confirms_and_only_with_the_nonce(
    conn: sqlite3.Connection,
    db_path: Path,
    clock: FakeClock,
    store: MemorySecretStore,
    slack: FakeSlack,
) -> None:
    _install(conn, clock, store, slack)
    nonce, clicks = _nonce(conn), Clicks(db_path, clock)
    clicks.click(conn, "U0ME1", "approve", "g-1")  # not yet confirmed: no other click counts
    clicks.click(conn, "U0XX9", "confirm_member", nonce)  # someone else
    clicks.click(conn, "U0ME1", "confirm_member", "wrong-nonce")
    ident = slack_admin.identity(conn)
    assert ident is not None and ident.member == ""
    clicks.click(conn, "U0ME1", "confirm_member", nonce)
    ident = slack_admin.identity(conn)
    assert ident is not None and (ident.member, ident.pending) == ("U0ME1", "")
    events = _audit_events(conn)
    assert events.count("slack.click_refused") == 2 and "slack.click_failed" in events
    assert "slack.member_confirmed" in events
    clicks.click(conn, "U0ME1", "confirm_member", nonce)  # a replay after confirmation
    assert _audit_events(conn).count("slack.click_failed") == 2


# ---- step-up changes ------------------------------------------------------------------------


def _verified(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    extra = exc.extra
    issued = stepup.issue(conn, clock, FakeStepper(), extra["purpose"], extra["target"])
    assert stepup.verify(conn, clock, FakeStepper(), issued.nonce_id) == "verified"
    return issued.nonce_id


def _confirmed(conn: sqlite3.Connection, db_path: Path, clock: FakeClock, member: str) -> None:
    Clicks(db_path, clock).click(conn, member, "confirm_member", _nonce(conn))


def test_set_tokens_needs_step_up_bound_to_the_new_tokens(
    conn: sqlite3.Connection,
    db_path: Path,
    clock: FakeClock,
    store: MemorySecretStore,
    slack: FakeSlack,
) -> None:
    _install(conn, clock, store, slack)
    _confirmed(conn, db_path, clock, "U0ME1")
    new_bot, new_app = BOT + "-new", APP + "-new"
    n = FakeNotifier()

    def call(nonce: str | None, bot: str = new_bot) -> dict[str, Any]:
        return slack_admin.set_tokens(conn, clock, store, slack.web, n, bot_token=bot,
                                      app_token=new_app, nonce=nonce)  # fmt: skip

    with pytest.raises(StepupRequiredError) as ei:
        call(None)
    assert ei.value.extra["purpose"] == "slack_tokens"
    assert new_bot not in json.dumps(ei.value.extra)  # the target holds a hash, never a token
    nonce = _verified(conn, clock, ei.value)
    with pytest.raises(StepupRequiredError):  # a step-up for these tokens doesn't cover others
        call(nonce, bot=BOT + "-other")
    call(nonce)
    assert store.get(BOT_SECRET) == new_bot and store.get(APP_SECRET) == new_app
    assert n.sent and n.sent[0][0] == "[ecf-alert] Security Notice"
    posts = [json.loads(r["payload"]) for r in
             conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out'")]  # fmt: skip
    assert any(p["key"].startswith("notice:") and p["channel"] == "U0ME1" for p in posts)
    assert new_bot not in _dump(conn)


def test_set_tokens_refuses_another_workspace(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    _install(conn, clock, store, slack)
    slack.team_id = "T9"
    with pytest.raises(InvalidInputError, match="T9"):
        slack_admin.set_tokens(conn, clock, store, slack.web, FakeNotifier(), bot_token=BOT,
                               app_token=APP, nonce=None)  # fmt: skip


def test_set_member_keeps_the_old_member_until_the_new_one_confirms(
    conn: sqlite3.Connection,
    db_path: Path,
    clock: FakeClock,
    store: MemorySecretStore,
    slack: FakeSlack,
) -> None:
    _install(conn, clock, store, slack)
    _confirmed(conn, db_path, clock, "U0ME1")
    n = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        slack_admin.set_member(conn, clock, store, slack.web, n, "U0ME2", None)
    assert "U0ME2" in stepup.issue(conn, clock, FakeStepper(), "slack_member",
                                ei.value.extra["target"]).prompt  # fmt: skip
    slack_admin.set_member(
        conn, clock, store, slack.web, n, "U0ME2", _verified(conn, clock, ei.value)
    )
    ident = slack_admin.identity(conn)
    assert ident is not None and (ident.member, ident.pending) == ("U0ME1", "U0ME2")
    assert slack.posts()[-1]["channel"] == "U0ME2" and n.sent  # the new ID's DM; a notice
    notices = [json.loads(r["payload"]) for r in
               conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out'")]  # fmt: skip
    assert any(p["channel"] == "U0ME1" and "U0ME2" in p["card"]["text"] for p in notices)
    _confirmed(conn, db_path, clock, "U0ME2")
    ident = slack_admin.identity(conn)
    assert ident is not None and ident.member == "U0ME2"


def test_changes_need_slack_installed(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    with pytest.raises(NotFoundError):
        slack_admin.set_member(conn, clock, store, slack.web, FakeNotifier(), "U0ME2", None)
    with pytest.raises(NotFoundError):
        slack_admin.reauthorize(conn, clock, slack.web, CONFIG, "default")


# ---- reauthorize ----------------------------------------------------------------------------


def test_reauthorize_updates_the_manifest_then_edits_or_reposts_every_card(
    conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore, slack: FakeSlack
) -> None:
    _install(conn, clock, store, slack)
    r = slack_admin.reauthorize(conn, clock, slack.web, CONFIG, "default")
    assert r["permissions_updated"] is True
    token, method, params = slack.calls[-1]
    assert (token, method, params["app_id"]) == (CONFIG, "apps.manifest.update", "A1")

    sender = SlackSender(SlackChat(slack.web(BOT)), clock, FakeNotifier(),
                         sleep=lambda _s: None)  # fmt: skip
    for key in ("item:1", "item:2"):
        slack_out.enqueue_post(conn, clock, key=key, route=RouteRef("C1"), card=Card(key))
    while sender.run_once(conn):
        pass
    slack.calls.clear()
    slack.fail["chat.update"] = SlackError("chat.update", "message_not_found")
    assert slack_admin.refresh(conn, clock) == 2
    sender.run_once(conn)  # item:1 was deleted in Slack: posted again
    del slack.fail["chat.update"]
    sender.run_once(conn)  # item:2 still exists: edited in place
    assert slack.methods() == ["chat.update", "chat.postMessage", "chat.update"]
    assert slack.calls[1][2]["text"] == "item:1"


# ---- through the API ------------------------------------------------------------------------


def test_install_route_stores_tokens_and_reconnects(
    conn: sqlite3.Connection, db_path: Path, store: MemorySecretStore, slack: FakeSlack
) -> None:
    import anyio  # noqa: PLC0415
    import httpx  # noqa: PLC0415

    from ecf_server.api import ServiceState, create_app  # noqa: PLC0415

    reloads: list[bool] = []
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         db_path=db_path, secrets=store, slack_web=slack.web,
                         slack_reload=lambda: reloads.append(True))  # fmt: skip

    async def post(path: str, body: dict[str, Any]) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.post(path, json=body, headers={"Authorization": "Bearer tok"})

    r = anyio.run(post, "/v1/slack/app", {"config_token": CONFIG})
    assert r.status_code == 200 and r.json()["app_id"] == "A1"
    r = anyio.run(post, "/v1/slack/install",
                  {"bot_token": BOT, "app_token": APP, "member": "U0ME1"})  # fmt: skip
    assert r.status_code == 200 and reloads == [True]
    assert BOT not in r.text and APP not in r.text
    assert r.json()["pending_member"] == "U0ME1" and store.get(BOT_SECRET) == BOT


# ---- nothing secret reaches the log (the imaplib lesson, V1.1) ------------------------------


class _FakeSlackHTTP(BaseHTTPRequestHandler):
    """Stands in for slack.com/api: real SDK requests, real response shapes."""

    def do_POST(self) -> None:
        method = self.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode()
        params = {k: v[0] for k, v in parse_qs(body).items()}
        if "json" in self.headers.get("content-type", ""):
            params = json.loads(body or "{}")
        data = FakeSlack().call("-", method, params)
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args  # quiet


@pytest.fixture
def fake_slack_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSlackHTTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def debug_log(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    path = tmp_path / "logs" / "ecf.log"
    configure_logging("service", log_file=path, level=logging.DEBUG)
    yield path
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def test_real_sdk_traffic_leaves_no_token_or_payload_in_the_log(
    conn: sqlite3.Connection,
    db_path: Path,
    clock: FakeClock,
    store: MemorySecretStore,
    fake_slack_url: str,
    debug_log: Path,
) -> None:
    """The real slack_sdk client, a real manifest response (with credentials) and a real button
    payload, with the service logging at DEBUG: no token, credential, card text or button value
    reaches the log file (V1.2 plan, step 4)."""

    def make_web(token: str) -> Any:
        return _slack.Web(token, base_url=fake_slack_url)

    slack_admin.create_app(conn, clock, make_web, CONFIG, "default")
    slack_admin.install(conn, clock, store, make_web, bot_token=BOT, app_token=APP,
                        member="U0ME1")  # fmt: skip
    nonce = _nonce(conn)
    payload = {
        "type": "block_actions",
        "token": "legacy-verification-token",
        "api_app_id": "A1",
        "team": {"id": "T1"},
        "user": {"id": "U0ME1"},
        "channel": {"id": "D0ME1"},
        "trigger_id": "trigger-id-value",
        "message": {"text": "Invoice 42: new bank details"},
        "actions": [{"action_id": "confirm_member#0", "value": nonce}],
    }
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    inbound.on_envelope(Envelope("env-1", "interactive", payload, None, None))
    SlackReceiver(clock).run_once(conn)
    sender = SlackSender(SlackChat(make_web(BOT)), clock, FakeNotifier(), sleep=lambda _s: None)
    assert sender.run_once(conn)  # "Confirmed", posted through the real client
    logging.getLogger("ecf.test").debug("marker line")  # the file is live at DEBUG

    for handler in logging.getLogger().handlers:
        handler.flush()
    text = debug_log.read_text(encoding="utf-8")
    assert "marker line" in text
    for secret in (CONFIG, BOT, APP, *SECRETS, nonce, "legacy-verification-token",
                   "trigger-id-value", "Invoice 42", "Confirm this is you"):  # fmt: skip
        assert secret not in text, secret
    ident = slack_admin.identity(conn)
    assert ident is not None and ident.member == "U0ME1"
