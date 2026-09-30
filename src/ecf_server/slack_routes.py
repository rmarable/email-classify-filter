"""Slack channels (SPEC §10.1; V1.2 step 5): one private channel per address,
`ecf-<install>-<address_id>`, plus `ecf-<install>-summary`.

`ensure` runs on the Slack thread about every half minute and does only what's missing: it waits
until your member ID is confirmed (so every channel includes you), then finds or creates each
channel, invites you, and records it. It finds before it creates, so a channel you made yourself
(when the workspace doesn't let apps create channels) is picked up once ecf is in it. A name that
leads to a channel already recorded for something else, or to one ecf can't use (archived, or one
it isn't in), gets a suffix (`-2` ... `-9`). After a member-ID change it invites the new ID to every
recorded channel. A recorded channel that was archived or deleted in Slack (or that ecf was removed
from) is forgotten when an invite finds it gone, and a new one is made at the next check.
`address remove` archives the address's recorded channel only (`archive`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ecf.ids import AddressId
from ecf_server import jobs, slack_admin, slack_out
from ecf_server.chat import (
    ChatSurface,
    Identity,
    RouteGoneError,
    RouteNameTakenError,
    RouteNotAllowedError,
    RouteRef,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier

SURFACE = "slack"
NAME_MAX = 80  # Slack: lowercase letters, digits, hyphens, underscores; 80 at most (verified
# 2026-09-29, docs.slack.dev conversations.create)
SUFFIXES = ("", *(f"-{i}" for i in range(2, 10)))
SUMMARY = slack_admin.SUMMARY_CHANNEL
SUMMARY_NAME, SUMMARY_INVITED = "slack_summary_name", "slack_summary_invited"
ICON = ":incoming_envelope:"


def channel_name(install: str, part: str, suffix: str = "") -> str:
    name = f"ecf-{install}-{part}"
    if len(name) + len(suffix) > NAME_MAX:
        tag = hashlib.sha256(name.encode()).hexdigest()[:8]
        name = f"{name[: NAME_MAX - len(suffix) - 9].rstrip('-')}-{tag}"
    return name + suffix


def identity(address_id: str) -> Identity:
    """The name and icon an address's posts show (never used on DMs)."""
    return Identity(f"ecf {address_id}", ICON)


def route_for(conn: sqlite3.Connection, address_id: str) -> RouteRef | None:
    row = conn.execute(
        "SELECT route_ref FROM routes WHERE address_id = ? AND surface = ?", (address_id, SURFACE)
    ).fetchone()
    return None if row is None else RouteRef(row["route_ref"])


def summary_route(conn: sqlite3.Connection) -> RouteRef | None:
    ch = slack_admin.setting(conn, SUMMARY)
    return RouteRef(ch) if ch else None


def list_channels(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """The recorded channels, summary first (for `ecf slack status`)."""
    out: list[dict[str, str]] = []
    ch = slack_admin.setting(conn, SUMMARY)
    if ch:
        out.append({"for": "summary", "name": slack_admin.setting(conn, SUMMARY_NAME),
                    "channel": ch})  # fmt: skip
    rows = conn.execute(
        "SELECT address_id, name, route_ref FROM routes WHERE surface = ? ORDER BY address_id",
        (SURFACE,),
    )
    out += [{"for": r["address_id"], "name": r["name"], "channel": r["route_ref"]} for r in rows]
    return out


class ChannelProblemError(Exception):
    """Something a person must do in Slack; the text says what."""


def ensure(
    conn: sqlite3.Connection,
    clock: Clock,
    chat: ChatSurface,
    install: str,
    *,
    recheck: bool = False,
    notifier: Notifier | None = None,
) -> list[str]:
    """Create, record and invite what's missing. With `recheck`, invite you to every recorded
    channel again: Slack ignores an invite for a member already there, so this finds channels
    that are gone and puts you back in any you left. Returns what changed (for the log); raises
    ChannelProblemError when a person must act."""
    ident = slack_admin.identity(conn)
    if ident is None or not ident.member:
        return []
    member, done = ident.member, list[str]()
    taken = _recorded(conn)
    ch = slack_admin.setting(conn, SUMMARY)
    if not ch:
        name, route, found = _find_or_create(chat, install, "summary", taken)
        if found:
            _adopted(conn, clock, chat, notifier, name, route, member)
        _set(conn, clock, {SUMMARY: route.channel, SUMMARY_NAME: name})
        ch, taken = route.channel, taken | {route.channel}
        done.append(f"created {name}")
    if recheck or slack_admin.setting(conn, SUMMARY_INVITED) != member:
        try:
            chat.invite(RouteRef(ch), member)
        except RouteGoneError:  # archived or deleted in Slack: a new one next time
            _set(conn, clock, {SUMMARY: "", SUMMARY_INVITED: ""})
            return [*done, "summary channel gone"]
        _set(conn, clock, {SUMMARY_INVITED: member})
    for row in conn.execute(
        "SELECT a.address_id, r.route_ref, r.invited FROM addresses a LEFT JOIN routes r"
        " ON r.address_id = a.address_id AND r.surface = ? WHERE a.removed_at IS NULL"
        " ORDER BY a.address_id",
        (SURFACE,),
    ).fetchall():
        aid, channel = row["address_id"], row["route_ref"]
        if channel is None:
            name, route, found = _find_or_create(chat, install, aid, taken)
            if found:
                _adopted(conn, clock, chat, notifier, name, route, member)
            _record(conn, clock, aid, route, name)
            channel, taken = route.channel, taken | {route.channel}
            done.append(f"created {name}")
        if recheck or row["invited"] != member:
            try:
                chat.invite(RouteRef(channel), member)
            except RouteGoneError:  # archived or deleted in Slack: a new one next time
                _forget(conn, clock, aid, channel)
                done.append(f"channel for {aid} gone")
                continue
            with write_tx(conn):
                conn.execute(
                    "UPDATE routes SET invited = ? WHERE address_id = ? AND surface = ?",
                    (member, aid, SURFACE),
                )
    return done


def archive(conn: sqlite3.Connection, clock: Clock, address_id: str) -> str | None:
    """Queue archiving the address's recorded channel and forget it; returns its name."""
    row = conn.execute(
        "SELECT route_ref, name FROM routes WHERE address_id = ? AND surface = ?",
        (address_id, SURFACE),
    ).fetchone()
    if row is None:
        return None
    jobs.enqueue(conn, clock, jobs.Queue.SLACK_OUT, AddressId(row["route_ref"]),
                 {"op": "archive", "channel": row["route_ref"]},
                 timeout_s=slack_out.TIMEOUT_S)  # fmt: skip
    with write_tx(conn):
        conn.execute(
            "DELETE FROM routes WHERE address_id = ? AND surface = ?", (address_id, SURFACE)
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'slack.channel_archived', 'service', 'ok', ?)",
            (to_ts(clock.now()), address_id, json.dumps({"channel": row["route_ref"]})),
        )
    return str(row["name"]) or None


def _adopted(
    conn: sqlite3.Connection,
    clock: Clock,
    chat: ChatSurface,
    notifier: Notifier | None,
    name: str,
    route: RouteRef,
    member: str,
) -> None:
    """ecf found an existing channel with its name (you made it, or someone else did): anyone in
    it besides you and the bot gets a Security Notice before ecf posts there (V1.2 review,
    2026-09-30). It is still used, since making it by hand is the way when apps can't."""
    bot = slack_admin.setting(conn, slack_admin.BOT_USER)
    others = sorted(set(chat.members(route)) - {member, bot})
    if others and notifier is not None:
        slack_admin.notice(conn, clock, notifier,
                           f"ecf is using the existing channel {name}, which already has"
                           f" {', '.join(others)} in it. They can see subjects, senders and"
                           " escalations there. If you didn't add them, remove them.",
                           dms=[member])  # fmt: skip


def _find_or_create(
    chat: ChatSurface, install: str, part: str, taken: set[str]
) -> tuple[str, RouteRef, bool]:
    """The channel's name, its route, and whether it already existed (found, not created)."""
    for suffix in SUFFIXES:
        name = channel_name(install, part, suffix)
        try:
            found = chat.find_route(name)
            route = found or chat.create_route(name)
        except RouteNameTakenError:
            continue
        except RouteNotAllowedError:
            raise ChannelProblemError(
                f"Slack doesn't let apps create channels in this workspace. Create a private "
                f"channel named {name}, add ecf's app to it (in the channel: /invite and the "
                "app's name), and ecf will use it within a minute."
            ) from None
        if route.channel not in taken:
            return name, route, found is not None
    raise ChannelProblemError(
        f"every name from {channel_name(install, part)} to "
        f"{channel_name(install, part, SUFFIXES[-1])} is taken; archive or rename one in Slack"
    )


def _forget(conn: sqlite3.Connection, clock: Clock, address_id: str, channel: str) -> None:
    with write_tx(conn):
        conn.execute(
            "DELETE FROM routes WHERE address_id = ? AND surface = ?", (address_id, SURFACE)
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'slack.channel_gone', 'service', 'ok', ?)",
            (to_ts(clock.now()), address_id, json.dumps({"channel": channel})),
        )


def _recorded(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT route_ref FROM routes WHERE surface = ?", (SURFACE,))
    out = {r["route_ref"] for r in rows}
    ch = slack_admin.setting(conn, SUMMARY)
    return out | {ch} if ch else out


def _record(
    conn: sqlite3.Connection, clock: Clock, address_id: str, route: RouteRef, name: str
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO routes (address_id, surface, route_ref, name) VALUES (?, ?, ?, ?)",
            (address_id, SURFACE, route.channel, name),
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'slack.channel_recorded', 'service', 'ok', ?)",
            (to_ts(clock.now()), address_id, json.dumps({"channel": route.channel, "name": name})),
        )
    log.info("slack.channel_recorded", address_id=address_id, channel=route.channel)


def _set(conn: sqlite3.Connection, clock: Clock, values: dict[str, Any]) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in values.items():
            slack_admin.put_setting(conn, k, str(v), now, actor="service")
