"""`ecf doctor`'s Slack and step-up checks (SPEC §13.2; V1.2 step 12a).

They run in the service, which holds the tokens: the member-ID confirmation; the bot token
(`auth.test`, and that it still belongs to the recorded workspace); the Socket Mode connection;
that you are in every channel ecf recorded; that a DM to you can be opened (no message is sent:
`ecf alerts test` sends one); an open Slack Delivery Failed alert; and whether step-up can run
here. Each result is `{name, level, detail, fix}` with level ok, warn or FAIL, as the CLI's
doctor prints them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf_server import _slack, slack_admin, slack_out, slack_routes
from ecf_server.chat import RouteRef
from ecf_server.secretstore import SecretStore
from ecf_server.slack_chat import SlackChat
from ecf_server.stepper import Stepper

OK, WARN, FAIL = "ok", "warn", "FAIL"
SET_TOKENS = "ecf slack set-tokens (ecf slack reauthorize only for missing_scope)"


def _c(name: str, level: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "level": level, "detail": detail, "fix": fix}


def checks(
    conn: sqlite3.Connection,
    store: SecretStore | None,
    make_web: Callable[[str], Any],
    runtime: dict[str, Any],
    stepper: Stepper | None,
) -> list[dict[str, str]]:
    out = [_stepup(stepper)]
    s = slack_admin.status(conn)
    if s["app_id"] is None:
        return [*out, _c("slack", WARN, "not installed", "ecf slack install")]
    member = s["member"]
    if member is None:
        out.append(_c("slack member", FAIL, "your member ID isn't confirmed; buttons do nothing",
                      "click Confirm in ecf's DM, or ecf slack set-member"))  # fmt: skip
    else:
        out.append(_c("slack member", OK, member))
    bot = store.get(slack_admin.BOT_SECRET) if store is not None else None
    if not bot:
        return [*out, _c("slack token", FAIL, "no bot token stored", SET_TOKENS)]
    web = make_web(bot)
    try:
        who = web.call("auth.test")
    except _slack.SlackError as exc:
        return [*out, _c("slack token", FAIL, f"Slack refused the bot token ({exc.code})",
                         SET_TOKENS)]  # fmt: skip
    except _slack.SlackNetworkError:
        return [*out, _c("slack", WARN, "Slack didn't answer (network?)", "try again")]
    if who.get("team_id") != s["team_id"]:
        out.append(_c("slack token", FAIL, "the bot token belongs to another workspace",
                      SET_TOKENS))  # fmt: skip
    else:
        out.append(_c("slack token", OK, f"valid for workspace {s['team_id']}"))
    out.append(_socket(runtime))
    if runtime.get("channels"):  # a person must act in Slack (slack_routes.ChannelProblemError)
        out.append(_c("slack channels", FAIL, str(runtime["channels"]), "see the detail"))
    if member is not None:
        out += _channels(conn, SlackChat(web), member)
        out += _unrouted(conn)
        out.append(_dm(web, member))
    out += _delivery(conn)
    out += _notifications(conn)
    return out


def _unrouted(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Addresses with no channel yet: their escalations wait (V1.2 review, 2026-09-30)."""
    rows = conn.execute(
        "SELECT a.address_id FROM addresses a LEFT JOIN routes r ON r.address_id = a.address_id"
        " AND r.surface = 'slack' WHERE a.removed_at IS NULL AND r.route_ref IS NULL"
    ).fetchall()
    return [_c(f"channel for {r[0]}", FAIL, "none yet: its escalations wait",
               "ecf makes it within a minute; if not, see the slack channels line")
            for r in rows]  # fmt: skip


def _notifications(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """With desktop notifications off, Slack-delivery alerts reach nobody (§13.2; email V1.5)."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'notifications'").fetchone()
    if row is None or json.loads(row[0]) != "off":
        return []
    return [_c("notifications", WARN, "desktop notifications are off, so a Slack delivery"
               " failure reaches nobody (email alerts arrive in V1.5)",
               "ecf settings set notifications on")]  # fmt: skip


def _stepup(stepper: Stepper | None) -> dict[str, str]:
    if stepper is None:
        return _c("step-up", FAIL, "no way to confirm it's you here; steps that need it are"
                  " refused", "see the admin guide: step-up")  # fmt: skip
    return _c("step-up", OK, f"{stepper.name} (try it: ecf stepup test)")


def _socket(runtime: dict[str, Any]) -> dict[str, str]:
    if runtime.get("error"):
        return _c("slack connection", FAIL, f"ecf's Slack work is failing ({runtime['error']})",
                  "see ecf logs; ecf retries every minute")  # fmt: skip
    if runtime.get("connected"):
        return _c("slack connection", OK, "Socket Mode connected")
    last = runtime.get("last_connected_at") or "never"
    code = runtime.get("connect_error")
    if code in slack_out.FATAL:
        return _c("slack connection", FAIL, f"Slack refused the app-level token ({code}); buttons"
                  " in Slack do nothing", "ecf slack set-tokens")  # fmt: skip
    why = f" ({code})" if code else ""
    detail = f"not connected{why}, last connected {last}; buttons in Slack do nothing until it is"
    return _c("slack connection", FAIL, detail, "check the network; ecf reconnects by itself")


def _channels(conn: sqlite3.Connection, chat: SlackChat, member: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ch in slack_routes.list_channels(conn):
        name = f"#{ch['name'] or ch['channel']}"
        try:
            here = member in chat.members(RouteRef(ch["channel"]))
        except (_slack.SlackError, _slack.SlackNetworkError) as exc:
            code = getattr(exc, "code", "network")
            out.append(_c(f"channel {name}", WARN, f"couldn't list its members ({code})"))
            continue
        if here:
            out.append(_c(f"channel {name}", OK, "you're a member"))
        else:
            out.append(_c(f"channel {name}", FAIL, "you're not a member, so you won't see it",
                          "ecf invites you again within the hour, or join it"))  # fmt: skip
    return out


def _dm(web: Any, member: str) -> dict[str, str]:
    try:
        web.call("conversations.open", users=member)
    except (_slack.SlackError, _slack.SlackNetworkError) as exc:
        return _c("slack DM", FAIL, f"can't open a DM to you ({getattr(exc, 'code', 'network')})",
                  "ecf slack reauthorize")  # fmt: skip
    return _c("slack DM", OK, "ecf can DM you (ecf alerts test sends one)")


def _delivery(conn: sqlite3.Connection) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    row = conn.execute("SELECT detail, opened_at FROM alerts WHERE kind = 'slack_delivery_failed'"
                       " AND resolved_at IS NULL LIMIT 1").fetchone()  # fmt: skip
    if row is not None:
        fix = SET_TOKENS if "set-tokens" in row["detail"] else "see the detail; ecf retries"
        out.append(_c("slack delivery", FAIL, f"since {row['opened_at']}: {row['detail']}", fix))
    dead = slack_out.dead_posts(conn)
    if dead:
        out.append(_c("slack posts", WARN, f"{len(dead)} post(s) gave up; their emails are still"
                      " listed", "ecf inbox"))  # fmt: skip
    return out
