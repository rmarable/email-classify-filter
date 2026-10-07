"""`ecf slack remove` (SPEC §10.1; v1.0.0 release, 2026-10-07): takes Slack off this install, so
`ecf slack install` (and `--new-app`) starts again from scratch. For an app deleted in Slack itself,
tokens that are gone, or a move to another app or workspace; before it only `ecf destroy` cleared a
recorded install (found in the v1.0.0 Slack real-service run, 2026-10-07).

`POST /v1/slack/remove` takes the install name typed again and a step-up bound to this install and
the app it records. With the Slack thread held idle (`SlackRuntime.held`), in order:

1. a Security Notice: the desktop, a DM to your member ID and a post in the summary channel (sent
   straight away, not through the queue step 5 clears), and alert email when it is on;
2. deletes the dead-man's scheduled message;
3. removes the app on Slack's side as `ecf destroy` does (OD-385, `remove_app`):
   `apps.manifest.delete` with a configuration token when one is given; otherwise, or if that is
   refused, `auth.revoke` with the stored bot token. An app or token that is already gone counts as
   done; with neither token nothing is asked of Slack, and the reply says what is left to do;
4. deletes the bot and app-level tokens from the secret store;
5. forgets the rest in one transaction: every `slack_*` setting (app, workspace, member ID, the
   pending app and member, the summary channel, dead-man's and "Needs you" records), the recorded
   channels (`routes`), the message records (`slack_messages`), and the Slack jobs not yet done
   (posts, edits and clicks for the old app); then resolves Slack Delivery Failed and the
   connection alert, and audits `slack.removed` (IDs and outcomes, never a token).

Kept: items, their audit and escalation records, grants, and the channels in Slack (not archived:
their history stays readable there; a new install creates new channels, a name in use getting a
suffix, §10.1 step 5). After a new install the cards of items still awaiting approval are posted
again (`approvals.post_held_cards`) and "Needs you" is posted fresh; other items' cards aren't
(`ecf inbox` lists them). Slack being unreachable stops the run before any token or record is
deleted (a retry still has the bot token); a Slack refusal is reported and the run goes on.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import InvalidInputError, NotFoundError, ServiceUnavailableError
from ecf_server import (
    alert_mail,
    alerts,
    deadman,
    health,
    install_identity,
    slack_admin,
    slack_out,
    slack_routes,
    stepup,
)
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.slack_chat import SlackChat, WebLike

# the bot token is already unusable: nothing left to revoke
REVOKED = frozenset({"token_revoked", "invalid_auth", "account_inactive", "not_authed"})
APP_GONE = frozenset({"app_not_found", "invalid_app_id"})  # deleted in Slack already
CONNECTION_ALERT = "slack_connection"  # slack_runtime.CONNECTION
SECRETS = (slack_admin.BOT_SECRET, slack_admin.APP_SECRET)


def remove_app(
    app_id: str, make_web: Callable[[str], WebLike], bot: str | None, config_token: str | None
) -> dict[str, Any]:
    """Delete the Slack app with a configuration token, else revoke the bot token (OD-385). The
    result is `deleted`, `revoked`, `failed` (with Slack's code), `no_token` (an app but no token
    to act with) or `no_slack`; `note` names Slack's code when the app or token was already gone.
    A SlackNetworkError is the caller's: both `ecf destroy` and `ecf slack remove` stop on it."""
    out: dict[str, Any] = {"app_id": app_id or None}
    if app_id and config_token:
        try:
            make_web(config_token).call("apps.manifest.delete", app_id=app_id)
        except SlackError as exc:
            if exc.code in APP_GONE:
                return out | {"result": "deleted", "note": exc.code}
            out["delete_failed"] = exc.code  # e.g. an expired token: fall back to revoking
        else:
            return out | {"result": "deleted"}
    if not bot:
        return out | {"result": "no_token" if app_id else "no_slack"}
    try:
        make_web(bot).call("auth.revoke")
    except SlackError as exc:
        if exc.code not in REVOKED:
            return out | {"result": "failed", "code": exc.code}
        return out | {"result": "revoked", "note": exc.code}
    return out | {"result": "revoked"}


def app_left(app_id: str | None, result: str | None) -> str | None:
    """What to do by hand when the app wasn't deleted (None when nothing is left)."""
    if not app_id or result == "deleted":
        return None
    how = "its bot token is revoked" if result == "revoked" else "its bot token may still work"
    return (
        f"Slack app {app_id} still exists ({how}): delete it at"
        f" https://api.slack.com/apps/{app_id} (Settings, Basic Information, Delete App)"
    )


def recorded(conn: sqlite3.Connection, store: SecretStore) -> bool:
    """Anything of Slack's here: a setting, a channel, or a token."""
    if conn.execute("SELECT 1 FROM settings WHERE key LIKE 'slack\\_%' ESCAPE '\\'"
                    " LIMIT 1").fetchone():  # fmt: skip
        return True
    if conn.execute("SELECT 1 FROM routes WHERE surface = ? LIMIT 1",
                    (slack_routes.SURFACE,)).fetchone():  # fmt: skip
        return True
    return any(store.get(name) for name in SECRETS)


@stepup.purpose("slack_remove")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to this install and the app, workspace and member it records; the dialog text comes
    from those records only, never from the client's target."""
    del target
    keys = (slack_admin.APP_ID, slack_admin.TEAM_ID, slack_admin.PENDING_APP, slack_admin.MEMBER)
    s = {k: slack_admin.setting(conn, k) for k in keys}
    app = s[slack_admin.APP_ID] or s[slack_admin.PENDING_APP]
    where = f"app {app}" if app else "no app recorded"
    if s[slack_admin.TEAM_ID]:
        where += f", workspace {s[slack_admin.TEAM_ID]}"
    return stepup.Bound(stepup.digest("slack_remove", install_identity.install_id(conn), s),
                        f"ecf: remove Slack from this install ({where}): its tokens, channels"
                        " and member ID are forgotten")  # fmt: skip


def run(  # noqa: PLR0913 - collaborators, then keyword-only inputs
    conn: sqlite3.Connection,
    clock: Clock,
    store: SecretStore,
    make_web: Callable[[str], WebLike],
    notifier: Notifier,
    *,
    install: str,
    typed: str,
    config_token: str | None,
    nonce: str | None,
) -> dict[str, Any]:
    """The whole removal (see the module notes); returns what was done and what is left by hand.
    The caller holds the Slack thread idle around it."""
    if typed != install:
        raise InvalidInputError(f"type the install name exactly ({install}) to remove Slack")
    bot = store.get(slack_admin.BOT_SECRET)  # a locked store stops here: nothing changed yet
    if not recorded(conn, store):
        raise NotFoundError("Slack isn't installed here; nothing to remove")
    stepup.consume(conn, clock, "slack_remove", {}, nonce)
    app_id = (slack_admin.setting(conn, slack_admin.APP_ID)
              or slack_admin.setting(conn, slack_admin.PENDING_APP))  # fmt: skip
    team_id = slack_admin.setting(conn, slack_admin.TEAM_ID)
    try:
        notice = _notice(conn, clock, make_web, notifier, bot)
        dead = _deadman(conn, make_web, bot)
        app = remove_app(app_id, make_web, bot, config_token)
    except SlackNetworkError as exc:
        raise ServiceUnavailableError("Slack couldn't be reached; nothing was removed. Run"
                                      " `ecf slack remove` again") from exc  # fmt: skip
    for name in SECRETS:
        store.delete(name)
    forgot = _forget(conn)
    for kind in (slack_out.ALERT, CONNECTION_ALERT):
        health.resolve_alert(conn, clock, notifier, kind, None)
    done: dict[str, Any] = {"app_id": app_id or None, "team_id": team_id or None,
                            "slack_app": app, "deadman": dead, "notice": notice,
                            "forgot": forgot}  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'slack.removed', 'os_user', 'ok', ?)",
                     (to_ts(clock.now()),
                      json.dumps(done | {"person": stepup.person()})))  # fmt: skip
    left = app_left(app_id, app["result"])
    return done | {"left": [left] if left else []}


# ---- the steps ----------------------------------------------------------------------------------


def _notice(
    conn: sqlite3.Connection,
    clock: Clock,
    make_web: Callable[[str], WebLike],
    notifier: Notifier,
    bot: str | None,
) -> dict[str, Any]:
    head = alerts.title("security_notice")
    text = ("Slack is being removed from ecf at this computer (`ecf slack remove`): ecf asks"
            " Slack to delete its app or turn its bot off, and forgets its tokens, channels and"
            " member ID until `ecf slack install` is run again. If this wasn't you, check the"
            " computer ecf runs on.")  # fmt: skip
    notifier.notify(head, text)
    posted = 0
    if bot:
        chat = SlackChat(make_web(bot))
        ident = slack_admin.identity(conn)
        summary = slack_routes.summary_route(conn)
        for route in [*([RouteRef(ident.member)] if ident and ident.member else []),
                      *([summary] if summary else [])]:  # fmt: skip
            try:
                chat.post(route, Card(head, text=text))
                posted += 1
            except SlackError:
                continue  # the app or channel may be gone already: counted, the run goes on
    emailed = alert_mail.queue(conn, clock, head, text, slack_done=posted > 0) is not None
    out = {"slack_posts": posted, "email": emailed}
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'security.notice', 'os_user', 'ok', ?)",
                     (to_ts(clock.now()), json.dumps(out)))  # fmt: skip
    return out


def _deadman(
    conn: sqlite3.Connection, make_web: Callable[[str], WebLike], bot: str | None
) -> dict[str, Any]:
    if not bot:
        return {"result": "no_token"}
    try:
        deadman.disarm(conn, make_web(bot))  # a message already gone is skipped there
    except SlackError as exc:
        return {"result": "failed", "code": exc.code}
    return {"result": "ok"}


def _forget(conn: sqlite3.Connection) -> dict[str, int]:
    with write_tx(conn):
        routes = conn.execute("DELETE FROM routes WHERE surface = ?", (slack_routes.SURFACE,))
        messages = conn.execute("DELETE FROM slack_messages")
        jobs = conn.execute("DELETE FROM jobs WHERE queue IN ('slack_out', 'slack_in')"
                            " AND state != 'done'")  # fmt: skip
        summary = conn.execute("DELETE FROM settings WHERE key = ?",
                               (slack_admin.SUMMARY_CHANNEL,)).rowcount  # fmt: skip
        rest = conn.execute("DELETE FROM settings WHERE key LIKE 'slack\\_%' ESCAPE '\\'")
        counts = {"channels": routes.rowcount + summary, "messages": messages.rowcount,
                  "jobs": jobs.rowcount, "settings": summary + rest.rowcount}  # fmt: skip
    return counts
