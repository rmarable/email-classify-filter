"""Slack install, tokens and member ID (SPEC §10.1; V1.2 step 4).

- `create_app`: `ecf slack install` sends a one-time configuration token; the service calls
  `apps.manifest.create` with ecf's manifest and keeps only the app ID. The configuration token
  and the credentials Slack returns (client secret, signing secret, verification token) are
  never stored: Socket Mode needs none of them.
- `install`: the bot and app-level tokens, typed into hidden prompts, come over the socket. The
  service checks them with Slack (`auth.test` gives the workspace and bot, `bots.info` the app;
  `apps.connections.open` checks the app-level token), stores them in the secret store, and
  records the app and workspace IDs.
- Your member ID is accepted only after a click on a Confirm button that ecf DMs to that ID; until
  then no click is accepted. A member-ID change (`set_member`) and replacing the tokens
  (`set_tokens`) need step-up and send a Security Notice; the new member ID needs its own click.
- `reauthorize`: `apps.manifest.update` with a fresh configuration token, then `refresh` edits
  every card ecf posted, re-posting any whose message is gone.

Slack API shapes (verified 2026-09-29, docs.slack.dev): `apps.manifest.create` returns `app_id`,
`credentials` and `oauth_authorize_url`; `auth.test` with a bot token returns `team_id`, `user_id`
and `bot_id`; `bots.info` (scope `users:read`) returns `bot.app_id`; `apps.manifest.update` returns
`permissions_updated`. Slack's docs don't say whether an app-level token names its app, so a
token from another app is caught only when its clicks arrive (refused as `wrong_app`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, NotFoundError, ServiceUnavailableError
from ecf_server import slack_in, slack_out, stepup
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.chat import Button, Card, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.slack_chat import SlackChat, WebLike
from ecf_server.slack_in import CONFIRM_ACTION, Click, SlackIdentity

BOT_SECRET = "slack/bot"  # noqa: S105 - the secret store's entry name, not a secret
APP_SECRET = "slack/app"  # noqa: S105 - the secret store's entry name, not a secret
APP_ID, TEAM_ID, MEMBER = "slack_app_id", "slack_team_id", "slack_member_id"
BOT_USER = "slack_bot_user"  # the bot's own member ID (left out of channel-member checks)
PENDING_APP = "slack_app_pending"  # created by `create_app`, not yet installed
PENDING_MEMBER, CONFIRM_NONCE = "slack_member_pending", "slack_member_nonce"
# a hash of the stored tokens, so a step-up binds to what it replaces
TOKEN_FP = "slack_token_fp"  # noqa: S105 - a settings key, not a secret
SUMMARY_CHANNEL = "slack_summary_channel"  # recorded by `slack_routes.ensure`
# §10.1; all confirmed in real-service test 0a, 2026-09-29
BOT_SCOPES = (
    "chat:write", "chat:write.customize", "groups:write", "groups:read", "users:read",
    "im:write", "pins:write",
)  # fmt: skip
_MEMBER = re.compile(r"[UW][A-Z0-9]{2,20}")
NAME_MAX = 35  # Slack's limit for an app name
ACTOR = "os_user"

WebFactory = Callable[[str], WebLike]


def manifest(install: str) -> dict[str, Any]:
    name = f"ecf-{install}"[:NAME_MAX]
    return {
        "display_information": {
            "name": name,
            "description": "email-classify-filter: mailbox alerts and approvals",
        },
        "features": {
            "app_home": {
                "home_tab_enabled": False,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": True,
            },
            "bot_user": {"display_name": name, "always_online": False},
        },
        "oauth_config": {"scopes": {"bot": list(BOT_SCOPES)}},
        "settings": {
            "interactivity": {"is_enabled": True},
            "socket_mode_enabled": True,
            "org_deploy_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def identity(conn: sqlite3.Connection) -> SlackIdentity | None:
    """The installed app and workspace, your confirmed member ID ("" until confirmed) and any
    member ID waiting for its confirmation click; None when Slack isn't installed."""
    s = _settings(conn, APP_ID, TEAM_ID, MEMBER, PENDING_MEMBER)
    if not s.get(APP_ID) or not s.get(TEAM_ID):
        return None
    return SlackIdentity(s[APP_ID], s[TEAM_ID], s.get(MEMBER, ""), s.get(PENDING_MEMBER, ""))


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    s = _settings(conn, APP_ID, TEAM_ID, MEMBER, PENDING_MEMBER, PENDING_APP)
    return {
        "app_id": s.get(APP_ID) or None,
        "team_id": s.get(TEAM_ID) or None,
        "member": s.get(MEMBER) or None,
        "pending_member": s.get(PENDING_MEMBER) or None,
        "pending_app_id": s.get(PENDING_APP) or None,
    }


# ---- install ------------------------------------------------------------------------------------


def create_app(
    conn: sqlite3.Connection, clock: Clock, make_web: WebFactory, config_token: str, install: str
) -> dict[str, Any]:
    if identity(conn) is not None:
        raise ConflictError("Slack is already installed; use `ecf slack reauthorize`")
    r = _call(make_web(config_token), "apps.manifest.create",
              manifest=json.dumps(manifest(install)))  # fmt: skip
    app_id = str(r.get("app_id", ""))  # the returned credentials are dropped here, unread
    if not app_id:
        raise ServiceUnavailableError("Slack didn't return an app ID")
    now = to_ts(clock.now())
    with write_tx(conn):
        _set(conn, PENDING_APP, app_id, now)
        _audit(conn, now, "slack.app_created", {"app_id": app_id})
    return {"app_id": app_id, "settings_url": settings_url(app_id)}


def settings_url(app_id: str) -> str:
    return f"https://api.slack.com/apps/{app_id}"


def install(
    conn: sqlite3.Connection,
    clock: Clock,
    store: SecretStore,
    make_web: WebFactory,
    *,
    bot_token: str,
    app_token: str,
    member: str,
) -> dict[str, Any]:
    if identity(conn) is not None or store.get(BOT_SECRET):
        raise ConflictError("Slack is already installed; use `ecf slack set-tokens`")
    _check_member(member)
    team_id, app_id, bot_user = check_tokens(make_web, bot_token, app_token)
    pending = _settings(conn, PENDING_APP).get(PENDING_APP)
    if pending and pending != app_id:
        raise InvalidInputError(
            f"that bot token belongs to app {app_id}, not the app ecf created ({pending})"
        )
    nonce = _send_confirmation(make_web(bot_token), member)  # a bad member ID fails here
    store.set(BOT_SECRET, bot_token)
    store.set(APP_SECRET, app_token)
    now = to_ts(clock.now())
    with write_tx(conn):
        _set(conn, APP_ID, app_id, now)
        _set(conn, TEAM_ID, team_id, now)
        _set(conn, TOKEN_FP, fingerprint(bot_token, app_token), now)
        _set(conn, BOT_USER, bot_user, now)
        _unset(conn, PENDING_APP)
        _audit(conn, now, "slack.installed", {"app_id": app_id, "team_id": team_id})
        _record_pending(conn, now, member, nonce)
    return status(conn)


def check_tokens(make_web: WebFactory, bot_token: str, app_token: str) -> tuple[str, str, str]:
    """Ask Slack about both tokens; returns (team_id, app_id, the bot's own user ID)."""
    if not bot_token.startswith("xoxb-"):
        raise InvalidInputError("the bot token starts with xoxb- (OAuth & Permissions page)")
    if not app_token.startswith("xapp-"):
        raise InvalidInputError("the app-level token starts with xapp- (Basic Information page)")
    web = make_web(bot_token)
    who = _call(web, "auth.test", what="bot token")
    bot = _call(web, "bots.info", what="bot token", bot=str(who.get("bot_id", "")))
    team_id = str(who.get("team_id", ""))
    info: dict[str, Any] = bot.get("bot") or {}
    app_id = str(info.get("app_id", ""))
    if not team_id or not app_id:
        raise InvalidInputError("Slack didn't identify the workspace and app for that bot token")
    # slack_sdk sends the app-level token for this one method from its `app_token` argument
    _call(web, "apps.connections.open", what="app-level token", app_token=app_token)
    return team_id, app_id, str(who.get("user_id", ""))


def fingerprint(bot_token: str, app_token: str) -> str:
    return hashlib.sha256(f"{bot_token}\n{app_token}".encode()).hexdigest()[:16]


# ---- member ID ----------------------------------------------------------------------------------


def ask_confirmation(conn: sqlite3.Connection, clock: Clock, web: WebLike, member: str) -> None:
    """DM `member` a Confirm button; its click (from that member only) makes it your member ID."""
    nonce = _send_confirmation(web, member)
    now = to_ts(clock.now())
    with write_tx(conn):
        _record_pending(conn, now, member, nonce)


def _send_confirmation(web: WebLike, member: str) -> str:
    nonce = secrets.token_urlsafe(16)
    card = Card(
        "Confirm this is you",
        text=(
            f"Click Confirm so ecf accepts your clicks (member {member}). If you didn't run "
            "`ecf slack install` or `ecf slack set-member`, don't click; tell whoever runs ecf."
        ),
        buttons=(Button(CONFIRM_ACTION, "Confirm: this is me", nonce, "primary"),),
    )
    try:
        SlackChat(web).post(RouteRef(member), card)
    except SlackError as exc:
        raise InvalidInputError(f"Slack couldn't DM member {member} ({exc.code})") from None
    except SlackNetworkError as exc:
        raise ServiceUnavailableError("Slack couldn't be reached; try again") from exc
    return nonce


def _record_pending(conn: sqlite3.Connection, now: str, member: str, nonce: str) -> None:
    _set(conn, PENDING_MEMBER, member, now)
    _set(conn, CONFIRM_NONCE, nonce, now)
    _audit(conn, now, "slack.member_confirm_sent", {"member": member})


@slack_in.handles(CONFIRM_ACTION)
def _confirmed(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    s = _settings(conn, PENDING_MEMBER, CONFIRM_NONCE, MEMBER)
    pending, nonce, old = s.get(PENDING_MEMBER, ""), s.get(CONFIRM_NONCE, ""), s.get(MEMBER, "")
    if not pending or click.user != pending or not hmac.compare_digest(click.ref, nonce):
        raise ConflictError("no confirmation waiting for this click")
    now = to_ts(clock.now())
    with write_tx(conn):
        _set(conn, MEMBER, pending, now, actor="slack")
        _unset(conn, PENDING_MEMBER)
        _unset(conn, CONFIRM_NONCE)
        _audit(conn, now, "slack.member_confirmed", {"member": pending, "previous": old or None},
               actor="slack")  # fmt: skip
    slack_out.enqueue_post(conn, clock, key=f"member-confirmed:{nonce}",
                           route=RouteRef(click.channel or pending),
                           card=Card("Confirmed: ecf accepts your clicks"))  # fmt: skip


@stepup.purpose("slack_member")
def _describe_member(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    new = str(target.get("member", ""))
    old = _settings(conn, MEMBER).get(MEMBER, "")
    prompt = f"ecf: accept Slack clicks from member {new} instead of {old or 'nobody'}"
    return stepup.Bound(stepup.digest("slack_member", new, old), prompt)


def set_member(
    conn: sqlite3.Connection,
    clock: Clock,
    store: SecretStore,
    make_web: WebFactory,
    notifier: Notifier,
    member: str,
    nonce: str | None,
) -> dict[str, Any]:
    ident = _installed(conn)
    _check_member(member)
    if member == ident.member:
        raise InvalidInputError(f"{member} is already your member ID")
    stepup.consume(conn, clock, "slack_member", {"member": member}, nonce)
    bot = store.get(BOT_SECRET)
    if not bot:
        raise ServiceUnavailableError("the Slack bot token is missing; run `ecf slack set-tokens`")
    ask_confirmation(conn, clock, make_web(bot), member)
    notice(conn, clock, notifier,
           f"Someone at this computer asked ecf to accept Slack clicks from member {member} "
           f"instead of {ident.member or 'nobody'}. It takes effect when {member} clicks Confirm "
           "in a DM from ecf. If this wasn't you, check `ecf slack status` and change it back "
           "with `ecf slack set-member`.",
           dms=[m for m in (ident.member,) if m])  # fmt: skip
    return status(conn)


# ---- tokens -------------------------------------------------------------------------------------


@stepup.purpose("slack_tokens")
def _describe_tokens(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    current = _settings(conn, TOKEN_FP).get(TOKEN_FP, "")
    prompt = (
        f"ecf: replace the Slack tokens for workspace {target.get('team_id')} "
        f"(app {target.get('app_id')})"
    )
    return stepup.Bound(stepup.digest("slack_tokens", target, current), prompt)


def set_tokens(
    conn: sqlite3.Connection,
    clock: Clock,
    store: SecretStore,
    make_web: WebFactory,
    notifier: Notifier,
    *,
    bot_token: str,
    app_token: str,
    nonce: str | None,
) -> dict[str, Any]:
    ident = _installed(conn)
    team_id, app_id, bot_user = check_tokens(make_web, bot_token, app_token)
    if (team_id, app_id) != (ident.team_id, ident.app_id):
        raise InvalidInputError(
            f"these tokens are for workspace {team_id}, app {app_id}; ecf is installed in "
            f"workspace {ident.team_id}, app {ident.app_id}"
        )
    fp = fingerprint(bot_token, app_token)
    target = {"team_id": team_id, "app_id": app_id, "tokens": fp}
    stepup.consume(conn, clock, "slack_tokens", target, nonce)
    store.set(BOT_SECRET, bot_token)
    store.set(APP_SECRET, app_token)
    now = to_ts(clock.now())
    with write_tx(conn):
        _set(conn, TOKEN_FP, fp, now)
        _set(conn, BOT_USER, bot_user, now)
        _audit(conn, now, "slack.tokens_replaced", {"app_id": app_id, "team_id": team_id})
    notice(conn, clock, notifier,
           "ecf's Slack tokens were replaced at this computer (`ecf slack set-tokens`). "
           "If this wasn't you, revoke them in the Slack app's settings.",
           dms=[m for m in (ident.member,) if m])  # fmt: skip
    return status(conn)


# ---- reauthorize --------------------------------------------------------------------------------


def reauthorize(
    conn: sqlite3.Connection, clock: Clock, make_web: WebFactory, config_token: str, install: str
) -> dict[str, Any]:
    ident = _installed(conn)
    r = _call(make_web(config_token), "apps.manifest.update", app_id=ident.app_id,
              manifest=json.dumps(manifest(install)))  # fmt: skip
    updated = bool(r.get("permissions_updated"))
    now = to_ts(clock.now())
    with write_tx(conn):
        _audit(conn, now, "slack.reauthorized", {"permissions_updated": updated})
    return {"permissions_updated": updated, "settings_url": settings_url(ident.app_id)}


def refresh(conn: sqlite3.Connection, clock: Clock) -> int:
    """Queue an edit of every card ecf posted (re-posted where the message is gone)."""
    _installed(conn)
    return slack_out.repost_all(conn, clock)


# ---- Security Notice ----------------------------------------------------------------------------


def notice(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, text: str, *, dms: list[str]
) -> None:
    """A Security Notice (§13.3): a desktop notification, a DM to each member named and a post
    in the summary channel once it exists. Email copies arrive with email alerts (V1.5, OD-206)."""
    notifier.notify("ecf: Security Notice", text)
    card = Card("Security Notice", text=text)
    key = f"notice:{secrets.token_hex(8)}"
    for m in dms:
        slack_out.enqueue_post(conn, clock, key=f"{key}:{m}", route=RouteRef(m), card=card)
    summary = setting(conn, SUMMARY_CHANNEL)
    if summary:
        slack_out.enqueue_post(conn, clock, key=f"{key}:summary", route=RouteRef(summary),
                               card=card)  # fmt: skip
    with write_tx(conn):
        data = {"dms": len(dms), "summary": bool(summary)}
        _audit(conn, to_ts(clock.now()), "security.notice", data)


# ---- helpers ------------------------------------------------------------------------------------


def _installed(conn: sqlite3.Connection) -> SlackIdentity:
    ident = identity(conn)
    if ident is None:
        raise NotFoundError("Slack isn't installed; run `ecf slack install`")
    return ident


def _check_member(member: str) -> None:
    if not _MEMBER.fullmatch(member):
        raise InvalidInputError(
            "a Slack member ID looks like U0123ABCD (Slack: your profile, ⋮, Copy member ID)"
        )


def _call(web: WebLike, method: str, *, what: str = "token", **params: Any) -> dict[str, Any]:
    try:
        return web.call(method, **params)
    except SlackError as exc:
        raise InvalidInputError(f"Slack refused the {what} ({method}: {exc.code})") from None
    except SlackNetworkError as exc:
        raise ServiceUnavailableError("Slack couldn't be reached; try again") from exc


def setting(conn: sqlite3.Connection, key: str) -> str:
    """One Slack setting, or "" when unset."""
    return _settings(conn, key).get(key, "")


def put_setting(conn: sqlite3.Connection, key: str, value: str, now: str, actor: str) -> None:
    """Inside the caller's write transaction."""
    _set(conn, key, value, now, actor)


def _settings(conn: sqlite3.Connection, *keys: str) -> dict[str, str]:
    marks = ", ".join("?" * len(keys))
    rows = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys)  # noqa: S608
    return {r["key"]: str(json.loads(r["value"])) for r in rows}


def _set(conn: sqlite3.Connection, key: str, value: str, now: str, actor: str = ACTOR) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now, actor),
    )


def _unset(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _audit(
    conn: sqlite3.Connection, now: str, event: str, data: dict[str, Any], actor: str = ACTOR
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, ?, ?, 'ok', ?)",
        (now, event, actor, json.dumps(data)),
    )
