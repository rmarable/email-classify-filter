"""`ecf slack ...` (SPEC §10.1; V1.2 step 4). Tokens are typed into hidden prompts and sent over
the service's 0600 socket; the service checks them with Slack and stores them in the secret store.
The CLI never stores or prints them."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.prompts import hidden, require_terminal
from ecf.stepup import with_step_up

CONFIRM_WAIT_S = 600
POLL_S = 2.0
CONFIG_HELP = (
    "A configuration token: api.slack.com/apps, 'Your App Configuration Tokens', Generate "
    "(expires after 12 hours; used once, never stored)."
)


def make_app(paths: Callable[[], Paths]) -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, help="Connect ecf to Slack.")

    @app.command("install")
    def install(
        new_app: Annotated[
            bool, typer.Option("--new-app", help="Create another app instead of continuing.")
        ] = False,
    ) -> None:
        """Create ecf's Slack app, store its tokens, and confirm your member ID."""
        require_terminal()
        with LocalClient(paths()) as c:
            install_slack(c, new_app=new_app)

    @app.command("status")
    def status() -> None:
        """Show the Slack app, workspace, your member ID and the connection."""
        with LocalClient(paths()) as c:
            s = c.get("/v1/slack")
        for line in describe(s):
            typer.echo(line)

    @app.command("set-tokens")
    def set_tokens() -> None:
        """Replace the bot and app-level tokens (after a revoked token). (step-up)"""
        require_terminal()
        bot = hidden("Bot User OAuth Token, xoxb-... (hidden): ")
        app_token = hidden("App-level token, xapp-... (hidden): ")
        with LocalClient(paths()) as c:
            with_step_up(c, lambda n: c.request("POST", "/v1/slack/tokens",
                                                {"bot_token": bot, "app_token": app_token,
                                                 "stepup_nonce": n}),
                         echo=typer.echo)  # fmt: skip
        typer.echo("Stored the new tokens; ecf reconnects to Slack now.")
        typer.echo("Next, if cards failed while the token was revoked: ecf slack reauthorize")

    @app.command("set-member")
    def set_member(
        member: Annotated[str, typer.Argument(help="The new Slack member ID (U0123ABCD).")],
    ) -> None:
        """Accept clicks from a different Slack member ID. (step-up)"""
        with LocalClient(paths()) as c:
            with_step_up(c, lambda n: c.request("POST", "/v1/slack/member",
                                                {"member": member, "stepup_nonce": n}),
                         echo=typer.echo)  # fmt: skip
            typer.echo(f"ecf sent {member} a DM; clicks stay with the old ID until it's confirmed.")
            _wait_for_confirmation(c, member)

    @app.command("reauthorize")
    def reauthorize() -> None:
        """Update the app's permissions, then edit (or re-post) every card ecf posted."""
        require_terminal()
        with LocalClient(paths()) as c:
            _reauthorize(c)

    return app


def install_slack(c: LocalClient, *, new_app: bool = False) -> None:
    """The steps behind `ecf slack install` (also used by `ecf init`)."""
    s = c.get("/v1/slack")
    if s["app_id"]:
        raise typer.BadParameter("Slack is already installed; see `ecf slack status`")
    app_id = s["pending_app_id"]
    if app_id and not new_app:
        typer.echo(f"Continuing with app {app_id}, created earlier (--new-app: another).")
    else:
        typer.echo(CONFIG_HELP)
        token = hidden("Configuration token (hidden): ")
        app_id = c.request("POST", "/v1/slack/app", {"config_token": token})["app_id"]
        typer.echo(f"Created app {app_id}.")
    typer.echo(
        f"1. Open https://api.slack.com/apps/{app_id}: OAuth & Permissions, Install to "
        "Workspace, Allow."
    )
    bot = hidden("   Bot User OAuth Token, xoxb-... (hidden): ")
    typer.echo(
        "2. Basic Information, App-Level Tokens, Generate Token and Scopes: any name, "
        "scope connections:write, Generate."
    )
    app_token = hidden("   App-level token, xapp-... (hidden): ")
    member = typer.prompt("3. Your Slack member ID (your profile, the ⋮ menu, Copy member ID)")
    c.request("POST", "/v1/slack/install",
              {"bot_token": bot, "app_token": app_token, "member": member.strip()})  # fmt: skip
    typer.echo("Installed. ecf sent you a DM in Slack.")
    _wait_for_confirmation(c, member.strip())


def _reauthorize(c: LocalClient) -> None:
    typer.echo(CONFIG_HELP)
    token = hidden("Configuration token (hidden): ")
    r = c.request("POST", "/v1/slack/reauthorize", {"config_token": token})
    if r["permissions_updated"]:
        typer.echo(
            f"Slack changed the app's permissions. Open {r['settings_url']}: Install App, "
            "Reinstall to Workspace. If Slack then shows a different bot token, run "
            "`ecf slack set-tokens` afterwards."
        )
        typer.prompt("Press Enter when done", default="", show_default=False)
    n = c.request("POST", "/v1/slack/refresh")["queued"]
    typer.echo(f"Queued {n} card(s) to edit or re-post.")


def describe(s: dict[str, Any]) -> list[str]:
    if not s["app_id"]:
        pending = f" (app {s['pending_app_id']} created; run `ecf slack install`)"
        return ["Slack: not installed" + (pending if s["pending_app_id"] else "")]
    rt: dict[str, Any] = s.get("runtime") or {}
    conn = "connected" if rt.get("connected") else "not connected"
    last = rt.get("last_connected_at") or "never"
    lines = [
        f"app:        {s['app_id']} (workspace {s['team_id']})",
        f"member:     {s['member'] or 'none confirmed yet (no clicks accepted)'}",
        f"connection: {conn}; last connected {last}",
    ]
    if s["pending_member"]:
        lines.append(f"waiting:    {s['pending_member']} to click Confirm in ecf's DM")
    if rt.get("channels"):
        lines.append(f"CHANNELS:   {rt['channels']}")
    channels: list[dict[str, str]] = s.get("channels") or []
    for ch in channels:
        lines.append(f"channel:    {ch['name']} ({ch['for']})")
    if not channels and s["member"]:
        lines.append("channels:   none yet (created within a minute of connecting)")
    return lines


def _wait_for_confirmation(c: LocalClient, member: str) -> None:
    typer.echo(f"Click Confirm in that DM (waiting up to {CONFIRM_WAIT_S // 60} minutes)...")
    deadline = time.monotonic() + CONFIRM_WAIT_S
    try:
        while time.monotonic() < deadline:
            if c.get("/v1/slack")["member"] == member:
                typer.echo(f"Confirmed: ecf accepts clicks from {member}.")
                return
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    typer.echo("Not confirmed yet. Click Confirm any time; `ecf slack status` shows the state.")
