"""Slack output (V1.2 step 3a): rendering, the Slack adapter and the paced queue, against a fake
Web API. Nothing here talks to Slack."""

from __future__ import annotations

import sqlite3
from typing import Any, cast

import pytest

from ecf_server import jobs, slack_out
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.chat import Button, Card, Identity, RouteGoneError, RouteRef
from ecf_server.clock import FakeClock
from ecf_server.notify import FakeNotifier
from ecf_server.slack_chat import SlackChat
from ecf_server.slack_out import SlackSender
from ecf_server.slack_render import blocks, clean, fallback

# ---- rendering ------------------------------------------------------------------------------


def _texts(node: Any) -> list[dict[str, Any]]:
    """Every text object anywhere in a Block Kit structure."""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        d = cast("dict[str, Any]", node)
        if d.get("type") in ("plain_text", "mrkdwn"):
            out.append(d)
        for v in d.values():
            out += _texts(v)
    elif isinstance(node, list):
        for v in node:  # pyright: ignore[reportUnknownVariableType]
            out += _texts(v)
    return out


def test_clean_strips_hidden_characters_and_defangs_links() -> None:
    sender = "Vendor A \u202egpj.exe\u202c <billing@vendor-a.example>"
    assert "\u202e" not in clean(sender, 200) and "\u200b" not in clean("pa\u200by", 200)
    assert clean("line1\nline2\tx", 200) == "line1\nline2\tx"
    assert clean("see https://evil.test/login", 200) == "see https[:]//evil.test/login"
    assert len(clean("x" * 500, 150)) == 150


def test_fallback_is_escaped() -> None:
    assert fallback("<!channel> *bold* <https://evil.test|click> & co") == (
        "&lt;!channel&gt; *bold* &lt;https[:]//evil.test|click&gt; &amp; co"
    )


def test_every_text_object_is_plain_text() -> None:
    card = Card(
        "Possible fraud: <!channel>",
        fields=tuple((f"F{i}", "<@U1> *x*") for i in range(13)),
        text="Subject: <https://evil.test|click>",
        buttons=(
            Button("approve", "Approve: archive email", "g-1", "primary"),
            Button("approve", "x" * 200, "g-2"),
        ),
        note="This acts on the email only. ecf never pays anything.",
    )
    b = blocks(card)
    texts = _texts(b)
    assert texts and all(t["type"] == "plain_text" for t in texts)
    assert sum(len(s.get("fields", [])) for s in b if s["type"] == "section") == 13
    assert all(len(s.get("fields", [])) <= 10 for s in b if s["type"] == "section")
    actions = next(s for s in b if s["type"] == "actions")["elements"]
    assert [a["action_id"] for a in actions] == ["approve#0", "approve#1"]  # unique in the block
    assert [a["value"] for a in actions] == ["g-1", "g-2"] and len(actions[1]["text"]["text"]) <= 75
    assert len(b[0]["text"]["text"]) <= 150


# ---- the adapter ----------------------------------------------------------------------------


class FakeWeb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, list[Exception]] = {}
        self._ts = 1000

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.calls.append((method, params))
        if self.fail.get(method):
            raise self.fail[method].pop(0)
        self._ts += 1
        if method == "chat.postMessage":
            return {"ok": True, "channel": params["channel"], "ts": f"{self._ts}.0001"}
        if method == "conversations.create":
            return {"ok": True, "channel": {"id": "C" + params["name"][-4:].upper()}}
        if method == "conversations.list":
            return {"ok": True, "channels": [{"id": "CTAKEN", "name": "ecf-default-ap"}]}
        return {"ok": True}

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def test_posts_are_plain_quiet_and_named() -> None:
    web = FakeWeb()
    chat = SlackChat(web)
    ref = chat.post(
        RouteRef("C1"), Card("<!channel> hi"), identity=Identity("AP <b>", ":inbox_tray:")
    )
    _, p = web.calls[0]
    assert p["mrkdwn"] is False and p["unfurl_links"] is False and p["unfurl_media"] is False
    assert p["text"] == "&lt;!channel&gt; hi" and p["username"] == "AP <b>"
    assert ref.route.channel == "C1"
    chat.post(RouteRef("D123"), Card("dm"), identity=Identity("AP", ":x:"))
    assert "username" not in web.calls[1][1]  # never customized on DMs


def test_channel_name_taken_is_reused_and_harmless_errors_pass() -> None:
    web = FakeWeb()
    web.fail["conversations.create"] = [SlackError("conversations.create", "name_taken")]
    web.fail["conversations.invite"] = [SlackError("conversations.invite", "already_in_channel")]
    chat = SlackChat(web)
    assert chat.create_route("ecf-default-ap") == RouteRef("CTAKEN")
    chat.invite(RouteRef("CTAKEN"), "U1")
    web.fail["conversations.invite"] = [SlackError("conversations.invite", "channel_not_found")]
    with pytest.raises(RouteGoneError):  # archived or deleted in Slack: ecf makes a new one
        chat.invite(RouteRef("CGONE"), "U1")


# ---- the queue ------------------------------------------------------------------------------


class Timer:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def mono(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(round(s, 3))
        self.t += s


def _sender(web: FakeWeb, clock: FakeClock, n: FakeNotifier, up: bool = True) -> SlackSender:
    t = Timer()
    return SlackSender(SlackChat(web), clock, n, resolve=lambda _h: up, sleep=t.sleep,
                       monotonic=t.mono)  # fmt: skip


C1 = RouteRef("C1")


def test_posts_once_then_edits_and_threads(conn: sqlite3.Connection, clock: FakeClock) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, n)
    slack_out.enqueue_post(conn, clock, key="item:1", route=C1, card=Card("new"), pin=True)
    slack_out.enqueue_post(conn, clock, key="item:1:reply", route=C1, card=Card("reply"),
                           thread_key="item:1")  # fmt: skip
    slack_out.enqueue_post(conn, clock, key="item:1", route=C1, card=Card("resolved"))
    while s.run_once(conn):
        pass
    assert web.methods() == ["chat.postMessage", "pins.add", "chat.postMessage", "chat.update"]
    top = slack_out.message_ref(conn, "item:1")
    assert top is not None and web.calls[2][1]["thread_ts"] == top.ts
    assert web.calls[3][1]["ts"] == top.ts  # edited by the stored ts, never a second post


def test_pacing_one_post_per_second_per_channel(conn: sqlite3.Connection, clock: FakeClock) -> None:
    web = FakeWeb()
    t = Timer()
    s = SlackSender(SlackChat(web), clock, FakeNotifier(), sleep=t.sleep, monotonic=t.mono)
    for i in range(3):
        slack_out.enqueue_post(conn, clock, key=f"k{i}", route=C1, card=Card(str(i)))
    slack_out.enqueue_post(conn, clock, key="other", route=RouteRef("C2"), card=Card("o"))
    while s.run_once(conn):
        pass
    assert t.slept == [1.0, 1.0]  # two waits on C1; C2 never waited


def test_network_errors_hold_never_dead_letter_and_alert_after_15_minutes(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, n)
    slack_out.enqueue_post(conn, clock, key="k", route=C1, card=Card("x"))
    for _ in range(20):  # far beyond max_attempts
        web.fail["chat.postMessage"] = [SlackNetworkError("chat.postMessage: timeout")]
        assert s.run_once(conn)
        clock.advance(slack_out.NETWORK_HOLD_S)
    row = conn.execute("SELECT state, attempts FROM jobs").fetchone()
    assert row["state"] == "queued" and row["attempts"] == 0  # held, not counted
    assert (
        n.sent and n.sent[0][0] == "[ecf-alert] Slack Delivery Failed" and "15" not in n.sent[0][0]
    )
    assert s.run_once(conn)  # the network is back
    assert web.methods()[-1] == "chat.postMessage"
    assert n.sent[-1][0] == "[ecf-alert] Resolved: Slack Delivery Failed"


def test_no_alert_when_the_whole_network_is_down(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, n, up=False)
    slack_out.enqueue_post(conn, clock, key="k", route=C1, card=Card("x"))
    for _ in range(20):
        web.fail["chat.postMessage"] = [SlackNetworkError("x")]
        s.run_once(conn)
        clock.advance(slack_out.NETWORK_HOLD_S)
    assert n.sent == []


def test_a_revoked_token_alerts_at_once_and_holds(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, n)
    slack_out.enqueue_post(conn, clock, key="k", route=C1, card=Card("x"))
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "token_revoked")]
    s.run_once(conn)
    assert n.sent[0][0] == "[ecf-alert] Slack Delivery Failed" and "set-tokens" in n.sent[0][1]
    assert conn.execute("SELECT state FROM jobs").fetchone()["state"] == "queued"


def test_other_errors_retry_then_dead_letter_without_text(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, n)
    slack_out.enqueue_post(conn, clock, key="item:9", route=C1, card=Card("Invoice 42 subject"))
    for _ in range(jobs.DEFAULT_MAX_ATTEMPTS):
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "channel_not_found")]
        s.run_once(conn)
        clock.advance(3600)
    dead = slack_out.dead_posts(conn)
    assert [d["key"] for d in dead] == ["item:9"] and "Invoice" not in str(dead)
    assert n.sent == []  # not a delivery failure alert: a problem with one post
