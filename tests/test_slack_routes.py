"""Slack channels (V1.2 step 5): one private channel per address plus the summary channel, found or
created, recorded, and with you invited; against the fake chat surface. Nothing here talks to
Slack."""

from __future__ import annotations

import sqlite3

import pytest

from ecf_server import slack_admin, slack_routes
from ecf_server.chat import FakeChat, RouteRef
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_out import SlackSender
from ecf_server.slack_routes import ChannelProblemError, channel_name, ensure


def _identity(conn: sqlite3.Connection, clock: FakeClock, member: str = "U0ME1") -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", member)):
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _address(conn: sqlite3.Connection, clock: FakeClock, aid: str) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
            " VALUES (?, ?, 'standard', 'A', ?)",
            (aid, f"{aid}@acme.example", to_ts(clock.now())),
        )


def _names(chat: FakeChat) -> dict[str, str]:
    return {r["name"]: ch for ch, r in chat.routes.items() if not r["archived"]}


def test_nothing_happens_until_your_member_id_is_confirmed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "ap")
    chat = FakeChat()
    assert ensure(conn, clock, chat, "default") == []
    _identity(conn, clock, member="")
    assert ensure(conn, clock, chat, "default") == []
    assert chat.routes == {}


def test_channels_are_created_recorded_and_you_are_invited_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _identity(conn, clock)
    for aid in ("ap", "billing"):
        _address(conn, clock, aid)
    chat = FakeChat()
    assert len(ensure(conn, clock, chat, "default")) == 3
    names = _names(chat)
    assert set(names) == {"ecf-default-summary", "ecf-default-ap", "ecf-default-billing"}
    assert all(r["members"] == ["U0ME1"] for r in chat.routes.values())
    assert slack_routes.route_for(conn, "ap") == RouteRef(names["ecf-default-ap"])
    assert slack_routes.summary_route(conn) == RouteRef(names["ecf-default-summary"])
    assert slack_admin.setting(conn, slack_admin.SUMMARY_CHANNEL) == names["ecf-default-summary"]
    assert ensure(conn, clock, chat, "default") == [] and len(chat.routes) == 3  # nothing twice
    assert [c["for"] for c in slack_routes.list_channels(conn)] == ["summary", "ap", "billing"]


def test_a_channel_you_made_is_found_before_creating(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _identity(conn, clock)
    _address(conn, clock, "ap")
    chat = FakeChat()
    mine = chat.create_route("ecf-default-ap")  # made by a person, with ecf's app added
    chat.creation_refused = True  # and the workspace doesn't let apps create channels
    with pytest.raises(ChannelProblemError, match="ecf-default-summary"):
        ensure(conn, clock, chat, "default")
    chat.creation_refused = False  # the person now makes the summary channel too
    summary = chat.create_route("ecf-default-summary")
    chat.creation_refused = True
    ensure(conn, clock, chat, "default")
    assert slack_routes.route_for(conn, "ap") == mine
    assert slack_routes.summary_route(conn) == summary


def test_a_name_already_used_or_archived_gets_a_suffix(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _identity(conn, clock)
    _address(conn, clock, "summary")  # its channel name is the summary channel's
    chat = FakeChat()
    old = chat.create_route("ecf-default-summary-2")
    chat.archive(old)  # an archived channel holds the next name
    ensure(conn, clock, chat, "default")
    assert set(_names(chat)) == {"ecf-default-summary", "ecf-default-summary-3"}
    assert slack_routes.route_for(conn, "summary") != slack_routes.summary_route(conn)


def test_long_names_are_cut_to_slacks_limit_and_stay_stable() -> None:
    name = channel_name("i" * 40, "a" * 40)
    assert len(name) <= 80 and name == channel_name("i" * 40, "a" * 40)
    assert len(channel_name("i" * 40, "a" * 40, "-9")) <= 80
    assert channel_name("default", "ap") == "ecf-default-ap"


def test_a_new_member_id_is_invited_everywhere(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _identity(conn, clock)
    _address(conn, clock, "ap")
    chat = FakeChat()
    ensure(conn, clock, chat, "default")
    _identity(conn, clock, member="U0ME2")
    ensure(conn, clock, chat, "default")
    assert all(r["members"] == ["U0ME1", "U0ME2"] for r in chat.routes.values())


def test_a_channel_archived_in_slack_is_replaced_on_the_hourly_recheck(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _identity(conn, clock)
    _address(conn, clock, "ap")
    chat = FakeChat()
    ensure(conn, clock, chat, "default")
    gone = slack_routes.route_for(conn, "ap")
    assert gone is not None
    chat.archive(gone)  # someone archived it in Slack
    assert ensure(conn, clock, chat, "default") == []  # nothing to do between rechecks
    assert "channel for ap gone" in ensure(conn, clock, chat, "default", recheck=True)
    ensure(conn, clock, chat, "default")
    new = slack_routes.route_for(conn, "ap")
    assert new is not None and new != gone
    assert chat.routes[new.channel]["name"] == "ecf-default-ap-2"


def test_removal_archives_only_the_recorded_channel(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _identity(conn, clock)
    _address(conn, clock, "ap")
    chat = FakeChat()
    other = chat.create_route("general-stuff")
    ensure(conn, clock, chat, "default")
    route = slack_routes.route_for(conn, "ap")
    assert route is not None
    assert slack_routes.archive(conn, clock, "ap") == "ecf-default-ap"
    assert slack_routes.route_for(conn, "ap") is None
    assert slack_routes.archive(conn, clock, "ap") is None  # nothing recorded: nothing archived
    sender = SlackSender(chat, clock, FakeNotifier(), sleep=lambda _s: None)
    while sender.run_once(conn):
        pass
    assert chat.routes[route.channel]["archived"] is True
    assert chat.routes[other.channel]["archived"] is False


def test_adopting_a_channel_with_someone_else_in_it_sends_a_security_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Someone in the workspace could make ecf's channel first (V1.2 review, 2026-09-30)."""
    _identity(conn, clock)
    _address(conn, clock, "ap")
    chat = FakeChat()
    chat.routes["CX"] = {"name": "ecf-default-ap", "members": ["U0EVE"], "archived": False}
    n = FakeNotifier()
    ensure(conn, clock, chat, "default", notifier=n)
    # still used, since making the channel by hand is the way when apps can't
    assert slack_routes.route_for(conn, "ap") == RouteRef("CX")
    [(title, text)] = n.sent
    assert title == "[ecf-alert] Security Notice"
    assert "ecf-default-ap" in text and "U0EVE" in text
