"""The `ecf` command-line interface."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast
from urllib.parse import urlencode

import typer

from ecf import __version__, cli_history, service_unit, timerange, watch
from ecf.cli_admin import make_commands as make_admin_commands
from ecf.cli_corpus import make_corpus_app
from ecf.cli_destroy import make_commands as make_destroy_commands
from ecf.cli_export import make_commands as make_export_commands
from ecf.cli_import import make_commands as make_import_commands
from ecf.cli_init import make_commands as make_init_commands
from ecf.cli_items import make_commands as make_item_commands
from ecf.cli_models import make_models_app
from ecf.cli_outbound import make_commands as make_outbound_commands
from ecf.cli_slack import make_app as make_slack_app
from ecf.cli_stats import make_stats_command
from ecf.cli_upgrade import make_commands as make_upgrade_commands
from ecf.client import LocalClient
from ecf.doctor import Level, outbound_line, run_checks
from ecf.errors import EcfError, InvalidInputError
from ecf.ids import SLUG_PATTERN
from ecf.log import configure_logging
from ecf.paths import Paths, paths_for
from ecf.prompts import hidden, require_terminal
from ecf.service_unit import manager_for
from ecf.status import CHECK_FAILED
from ecf.stepup import step_up, with_step_up

if TYPE_CHECKING:
    from ecf.eval.results import ResultFile

app = typer.Typer(no_args_is_help=True, add_completion=False, help="email-classify-filter")
service_app = typer.Typer(no_args_is_help=True, help="Install and control the background service.")
app.add_typer(service_app, name="service")
eval_app = typer.Typer(no_args_is_help=True, help="Synthetic eval set and results.")
app.add_typer(eval_app, name="eval")
address_app = typer.Typer(no_args_is_help=True, help="Monitored mailboxes.")
app.add_typer(address_app, name="address")
stepup_app = typer.Typer(
    no_args_is_help=True, help="Confirm actions with Touch ID or your password."
)
app.add_typer(stepup_app, name="stepup")
EVAL_ROOT = Path("tests/eval/synthetic")


class Ctx:
    install: str = "default"


STATE = Ctx()


@app.callback()
def root(
    install: Annotated[
        str,
        typer.Option("--install", help="Which install to use.", show_default=True),
    ] = "default",
) -> None:
    """email-classify-filter: watch mailboxes, flag fraud, approve actions in Slack."""

    if not re.fullmatch(SLUG_PATTERN, install):
        raise typer.BadParameter(
            "lowercase letters, digits and hyphens, at most 40", param_hint="--install"
        )
    STATE.install = install


def _paths() -> Paths:
    return paths_for(STATE.install)


app.add_typer(make_slack_app(_paths), name="slack")
app.add_typer(make_item_commands(app, _paths), name="item")
make_admin_commands(app, _paths)
make_outbound_commands(app, _paths)
make_export_commands(app, _paths)
make_import_commands(app, _paths)
make_upgrade_commands(app, _paths)
make_destroy_commands(app, _paths)
app.add_typer(make_models_app(_paths), name="models")
app.add_typer(make_corpus_app(_paths), name="corpus")
make_stats_command(app, _paths)
cli_history.make_reprocess_command(app, _paths)
alerts_app = typer.Typer(no_args_is_help=True, help="Where alerts go.")
app.add_typer(alerts_app, name="alerts")


@alerts_app.command("show")
def alerts_show() -> None:
    """Where each class of alert goes."""
    with LocalClient(_paths()) as c:
        r = c.get("/v1/alerts")
    typer.echo(f"default: {', '.join(r['default'])} (desktop notifications: {r['desktop']})")
    for cls, routes in r["classes"].items():
        typer.echo(f"{cls:<9} {', '.join(routes) or 'desktop only'}")
    mail = r.get("email")
    typer.echo("email:    off (ecf alerts email set --from <address> --to <destination>)"
               if mail is None else
               f"email:    from {mail['from_email']} ({mail['from']}) to {mail['to']}")  # fmt: skip


@alerts_app.command("set")
def alerts_set(
    to: Annotated[str, typer.Option("--to", help="slack, email or slack,email.")],
    cls: Annotated[
        str | None,
        typer.Argument(help="mail, system, operator or slack (all when left out)."),
    ] = None,
) -> None:
    """Change where alerts go. (step-up)"""
    body: dict[str, Any] = {"class": cls, "to": [t for t in to.split(",") if t.strip()]}
    with LocalClient(_paths()) as c:
        with_step_up(c, lambda n: c.request("POST", "/v1/alerts", body | {"nonce_id": n}),
                     echo=typer.echo)  # fmt: skip
    typer.echo("changed; a Security Notice says so in Slack")


alerts_email_app = typer.Typer(no_args_is_help=True, help="Alert email.")
alerts_app.add_typer(alerts_email_app, name="email")


@alerts_email_app.command("set")
def alerts_email_set(
    frm: Annotated[str, typer.Option("--from", help="The watched address whose login sends.")],
    to: Annotated[str, typer.Option("--to", help="Where alerts go; not a watched address.")],
) -> None:
    """Send alerts by email, then a test. (step-up)"""
    body: dict[str, Any] = {"from": frm, "to": to}
    with LocalClient(_paths()) as c:
        r = with_step_up(c, lambda n: c.request("POST", "/v1/alerts/email", body | {"nonce_id": n}),
                         echo=typer.echo)  # fmt: skip
    mail = r["email"]
    typer.echo(f"alert email on: from {mail['from_email']} to {mail['to']}; a test email is on"
               f" its way. Routes: {', '.join(r['default'])} (change: ecf alerts set)")  # fmt: skip


@alerts_email_app.command("off")
def alerts_email_off() -> None:
    """Stop alert email; email leaves every route. (step-up)"""
    with LocalClient(_paths()) as c:
        r = with_step_up(c, lambda n: c.request("POST", "/v1/alerts/email/off", {"nonce_id": n}),
                         echo=typer.echo)  # fmt: skip
    typer.echo(f"alert email off; routes: {', '.join(r['default'])}")


@alerts_app.command("test")
def alerts_test() -> None:
    """Send a test alert on every route."""
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/alerts/test")
    queued = {"slack": "slack (queued: it posts within a minute)",
              "email": "email (queued: it goes within a minute)"}  # fmt: skip
    where = [queued.get(str(w), str(w)) for w in r["sent"]]
    typer.echo(f"sent to: {', '.join(where) or 'nowhere (desktop off, no Slack)'}")


@app.command()
def version() -> None:
    """Print the ecf version."""
    typer.echo(__version__)


@app.command()
def status() -> None:
    """Show the service's state."""
    paths = _paths()
    unit = manager_for(paths).status()
    typer.echo(f"install:   {paths.install}")
    m = watch.marker(paths)
    if m:
        typer.echo(f"watch:     `ecf watch` took over at {m['started_at']}"
                   + ("" if m["alive"] else "; it ended without restoring the background"
                      " service: ecf service start"))  # fmt: skip
    typer.echo(
        f"unit:      {'installed' if unit.installed else 'not installed'}, "
        f"{'running' if unit.running else 'not running'}"
    )
    with LocalClient(paths) as c:
        st = c.get("/v1/status")
    br, ss = st.get("breaker", {}), st.get("secret_store", {})
    typer.echo(f"service:   {st['version']} (pid {st['pid']}), started {st['started_at']}")
    typer.echo(f"last tick: {st.get('last_tick_at') or 'none yet'}")
    typer.echo(
        f"breaker:   {'TRIPPED' if br.get('tripped') else 'ok'} "
        f"({br.get('recent_crashes', 0)} recent crashes)"
    )
    typer.echo(
        f"secrets:   {ss.get('backend') or 'none usable'}"
        + (" (Python changed: run `ecf service regrant`)" if ss.get("interpreter_changed") else "")
    )
    sl = st.get("slack", {})
    if not sl.get("installed"):
        typer.echo("slack:     not installed (`ecf slack install`)")
    else:
        state = "connected" if sl.get("connected") else "NOT connected"
        typer.echo(f"slack:     {state}; last connected {sl.get('last_connected_at') or 'never'}")
    for a in st.get("addresses", []):
        last = a["last_finished_at"] or "never checked"
        line = f"{a['address_id']:<16} {a['stage']:<7} last check {last}"
        if a["last_status"]:
            line += f" ({a['last_status']})"
        if a["backlog"] or a["deferred"]:
            line += f", {a['backlog']} waiting, {a['deferred']} large deferred"
        if a["paused"]:
            line += ", PAUSED"
        typer.echo(line)
        typer.echo(f"{'':<16} {outbound_line(a)}")
        if a["last_error"] and a["last_status"] in CHECK_FAILED:
            typer.echo(f"{'':<16} last error: {a['last_error']}")
    for alert in st.get("alerts", []):
        typer.echo(f"ALERT      {alert['title']}: {alert['detail']}")


@app.command()
def doctor() -> None:
    """Check this install and say how to fix anything wrong."""
    checks = run_checks(_paths())
    width = max(len(c.name) for c in checks)
    for c in checks:
        typer.echo(f"{c.level.value:>4}  {c.name:<{width}}  {c.detail}")
        if c.fix and c.level is not Level.OK:
            typer.echo(f"{'':>4}  {'':<{width}}  fix: {c.fix}")
    if any(c.level is Level.FAIL for c in checks):
        raise typer.Exit(3)


@app.command()
def check(
    address: Annotated[str | None, typer.Argument(help="One address (default: all).")] = None,
) -> None:
    """Check mail now: fetch, verify senders and run the fraud and regulator checks. Exit 3 when a
    check failed."""
    body: dict[str, object] = {}
    if address:
        body["address_id"] = address
    failed = False
    with LocalClient(_paths()) as c:
        for r in c.stream("POST", "/v1/checks", body):
            if r.get("done"):
                break
            failed |= r["status"] in CHECK_FAILED
            typer.echo(_check_line(r))
    if failed:
        raise typer.Exit(3)


def _check_line(r: dict[str, Any]) -> str:
    who = f"{r['address_id']:<16}"
    if r["status"] == "first_run":
        return f"{who} first check: started from now (older mail: `ecf backfill`)"
    if r["status"] == "busy":
        return f"{who} skipped: another check holds this address"

    if r["error"]:
        return f"{who} {r['status'].replace('_', ' ')}: {r['error']}"
    parts = [f"{r['created']} new"]
    for key, label in (
        ("escalations", "to escalate"),
        ("digest", "for the digest"),
        ("duplicates", "repeat deliveries"),
        ("quarantined", "quarantined"),
        ("large_done", "large read"),
        ("deferred", "large deferred"),
        ("relocated", "re-found after a mailbox reset"),
        ("resolved_by_mailbox", "closed (left INBOX)"),
        ("remaining", "still waiting"),
        ("backfill_created", "backfilled"),
        ("backfill_remaining", "older still to backfill"),
    ):
        if r.get(key):
            parts.append(f"{r[key]} {label}")
    prefix = "mailbox reset recovered: " if r["status"] == "reset_recovered" else ""
    return f"{who} {prefix}" + ", ".join(parts)


@app.command()
def backfill(
    address: Annotated[str | None, typer.Argument(help="Address id or email.")] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="From: a date, a time (2026-09-29T08:00) or an age (3d)."),
    ] = None,
    until: Annotated[
        str | None, typer.Option("--until", help="Up to this time (default now); same forms.")
    ] = None,
    act: Annotated[
        bool,
        typer.Option("--act", help="Pre-check and classify as for new mail (step-up over 500)."),
    ] = False,
    stop: Annotated[bool, typer.Option("--stop", help="End a running backfill.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Don't ask before --act (step-up still applies).")
    ] = False,
) -> None:
    """Read older mail (records only unless --act); with no address, show each backfill."""
    with LocalClient(_paths()) as c:
        if address is None:
            _backfill_status(c.get("/v1/backfill")["running"])
            return
        if stop:
            r = c.request("POST", "/v1/backfill/stop", {"address_id": address})
            typer.echo(f"stopped the backfill of {r['address_id']}; {r['items']} read so far stay")
            return
        if since is None:
            raise typer.BadParameter("add --since, e.g. --since 2026-09-01 or --since 30d")
        r = cli_history.run_backfill(c, address, since, until, act=act, yes=yes)
    how = "classified as new mail, after it" if r["act"] else (
        "records only: nothing is done to the mailbox or escalated")  # fmt: skip
    typer.echo(f"backfill {r['run_id']} of {r['address_id']} started ({how}); it runs with the"
               " checks, new mail first, with one Slack summary. Progress: ecf backfill; to end"
               f" it: ecf backfill {r['address_id']} --stop")  # fmt: skip


def _backfill_status(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        how = "acting" if r["act"] else "records only"
        fraud = (f", {r['fraud']} with a fraud signal (ecf logs --address {r['address_id']}"
                 " --event precheck)") if r.get("fraud") else ""  # fmt: skip
        when = "" if r["outcome"] == "running" else f" at {r['at'][:16]}"
        until = f" until {str(r['until'])[:16]}" if r.get("until") else ""
        held = f", {r['held']} already held" if r.get("held") else ""
        typer.echo(f"{r['address_id']:<16} since {str(r['since'])[:16]}{until} ({how}):"
                   f" {r['outcome']}{when}, {r['items']} read{held}{fraud}")  # fmt: skip
    if not rows:
        typer.echo("no backfill yet")


@app.command()
def logs(
    address: Annotated[str | None, typer.Option("--address", help="Only this address.")] = None,
    event: Annotated[
        str | None, typer.Option("--event", help="Event name or prefix, e.g. check. or action.")
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="A time (2026-09-29T08:00) or an age (2h, 3d).")
    ] = None,
    until: Annotated[
        str | None, typer.Option("--until", help="Up to this time or age, in the same forms.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="How many of the newest events.")] = 50,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep showing new events.")
    ] = False,
) -> None:
    """Show the audit log: what the service did and decided, never message content."""
    params: dict[str, str] = {"limit": str(limit)}
    if address:
        params["address_id"] = address
    if event:
        params["event"] = event
    if since or until:  # the range parser (OD-507 §3.1)
        start, end = _range(since, until)
        params |= {k: timerange.stamp(v) for k, v in (("since", start), ("until", end)) if v}
    with LocalClient(_paths()) as c:
        last = 0
        while True:
            events = c.get("/v1/logs?" + urlencode(params))["events"]
            for e in events:
                typer.echo(_log_line(e))
                last = max(last, int(e["id"]))
            if not follow:
                return
            params["after_id"] = str(last)
            time.sleep(2)


def _range(since: str | None, until: str | None) -> tuple[datetime | None, datetime | None]:
    """`--since`/`--until` through the one parser (OD-507 §3.1), refused with its hint."""
    now = datetime.now(UTC)
    try:
        if since is None:
            return None, timerange.parse(until or "0s", now=now, what="--until")
        return timerange.resolve(since, until, now=now)
    except InvalidInputError as exc:
        raise typer.BadParameter(exc.detail) from None


def _log_line(e: dict[str, Any]) -> str:
    where = e.get("address_id") or "-"
    data = json.dumps(e["data"], sort_keys=True) if e["data"] else ""
    flag = "" if e["outcome"] == "ok" else f" [{e['outcome']}]"
    return f"{e['ts'][:19]}  {where:<12} {e['event']}{flag}  {data}".rstrip()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def claude(
    ctx: typer.Context,
    login: Annotated[
        bool, typer.Option("--login", help="Log in to Claude in ecf's configuration, then stop.")
    ] = False,
) -> None:
    """Open Claude Code in ecf's own configuration, with the ecf plugin; type /ecf-review there.

    Needs its own Claude login (it uses a separate configuration folder): `ecf claude --login`.
    Extra arguments are passed to `claude`.
    """
    from ecf import claude_setup, claude_wrapper  # noqa: PLC0415

    if login:
        if ctx.args:
            raise typer.BadParameter("--login takes no other arguments", param_hint="--login")
        raise typer.Exit(claude_setup.run_login(_paths(), echo=typer.echo))
    raise typer.Exit(claude_wrapper.run(_paths(), list(ctx.args), echo=typer.echo))


@service_app.command("install")
def service_install() -> None:
    """Install the service unit (launchd or systemd) and start it."""
    m = manager_for(_paths())
    m.install()
    typer.echo(f"installed {m.unit_path}")


def _stopping_on_purpose() -> None:
    """Tell the service this stop is deliberate, so it removes the dead-man's message; a shutdown
    or logout leaves it armed (OD-222). Best effort: a service that isn't answering has nothing
    to disarm now."""
    try:
        with LocalClient(_paths()) as c:
            c.request("POST", "/v1/service/stopping", {})
    except EcfError:
        pass


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop the service and remove its unit. Data is kept (use `ecf destroy` to remove it)."""
    m = manager_for(_paths())
    _stopping_on_purpose()
    m.uninstall()
    typer.echo(f"removed {m.unit_path}")


@service_app.command("start")
def service_start() -> None:
    """Start the service (also clears the crash-loop breaker)."""
    paths = _paths()
    m = watch.marker(paths)
    if m and m["alive"]:
        typer.echo(f"`ecf watch` is running the service (pid {m['pid']}); stop it first", err=True)
        raise typer.Exit(3)
    manager_for(paths).start()
    watch.clear(paths)
    typer.echo("started")


@app.command("replay", hidden=True)  # development only
def replay_command(
    folder: Annotated[Path, typer.Argument(help="A folder of .eml files.")],
    host: Annotated[str, typer.Option("--host", help="The test IMAP server.")],
    user: Annotated[str, typer.Option("--user")],
    port: Annotated[int, typer.Option("--port")] = 993,
    count: Annotated[
        int | None, typer.Option("--count", help="Append this many (cycling).")
    ] = None,
    cafile: Annotated[Path | None, typer.Option("--cafile", help="Trust this certificate.")] = None,
    keep_ids: Annotated[
        bool, typer.Option("--keep-ids", help="Keep the files' Message-IDs.")
    ] = False,
    via: Annotated[str, typer.Option("--via", help="append (smtp isn't built).")] = "append",
) -> None:
    """Development only: append .eml files into a test IMAP mailbox with fresh Message-IDs
    (the load test, end-to-end runs). Never point it at a real mailbox."""
    from ecf import replay  # noqa: PLC0415
    from ecf.prompts import hidden  # noqa: PLC0415

    if via != "append":
        raise typer.BadParameter("only --via append is built", param_hint="--via")
    password = hidden(f"Password for {user} on {host}: ")
    n = replay.replay(folder, host=host, port=port, user=user, password=password, count=count,
                      fresh_ids=not keep_ids, cafile=cafile)  # fmt: skip
    typer.echo(f"appended {n} message(s) to {user} on {host}")


@app.command("watch")
def watch_command() -> None:
    """Run the service in this terminal instead of the background (Ctrl-C to stop); the
    background service is stopped first and started again afterwards."""
    paths = _paths()
    typer.echo("stopping the background service, then running ecf here (Ctrl-C to stop)")
    code = watch.run(paths, manager_for(paths))
    typer.echo(f"ecf stopped (exit {code}); the background service is as it was before")
    raise typer.Exit(code)


@service_app.command("regrant")
def service_regrant() -> None:
    """After a Python change: let ecf read its Keychain items again (macOS; choose Always Allow
    at each dialog). Stops and restarts the service."""
    paths = _paths()
    m = watch.marker(paths)
    if m and m["alive"]:
        typer.echo(f"`ecf watch` is running the service (pid {m['pid']}); stop it first", err=True)
        raise typer.Exit(3)
    if sys.platform == "darwin":
        _stopping_on_purpose()
    code = service_unit.regrant(paths, manager_for(paths), echo=typer.echo)
    raise typer.Exit(code)


@service_app.command("stop")
def service_stop() -> None:
    """Stop the service until the next login."""
    _stopping_on_purpose()
    manager_for(_paths()).stop()
    typer.echo("stopped until your next login; use `ecf pause` to keep fraud checks running")


@service_app.command("restart")
def service_restart() -> None:
    """Restart the service (also clears the crash-loop breaker)."""
    manager_for(_paths()).restart()
    typer.echo("restarted")


@service_app.command("status")
def service_status() -> None:
    """Show whether the service unit is installed and running."""
    s = manager_for(_paths()).status()
    typer.echo(f"installed: {'yes' if s.installed else 'no'}")
    typer.echo(f"running:   {'yes' if s.running else 'no'}" + (f" (pid {s.pid})" if s.pid else ""))
    if s.last_exit is not None:
        typer.echo(f"last exit: {s.last_exit}")
    if not s.running:
        raise typer.Exit(3)


FINANCE_HINTS = (
    "ap",
    "payable",
    "invoice",
    "billing",
    "finance",
    "payroll",
    "treasury",
    "accounting",
    "remit",
)  # SPEC §9.4; the service holds the rules


def _suggest_sensitivity(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    words = set(re.split(r"[^a-z0-9]+", local))
    long_hit = any(h in local for h in FINANCE_HINTS if len(h) >= 4)
    return "high" if words & set(FINANCE_HINTS) or long_hit else "standard"


@address_app.command("add")
def address_add(
    email: Annotated[str, typer.Argument(help="The mailbox to watch, e.g. ap@example.com.")],
    imap_host: Annotated[
        str | None,
        typer.Option("--imap-host", help="IMAP server (port 993, TLS); not needed for Gmail."),
    ] = None,
    sensitivity: Annotated[
        str | None, typer.Option(help="standard, or high for finance mailboxes (extra checks).")
    ] = None,
    preset: Annotated[
        str | None, typer.Option(help="C all-Claude (A and B were removed in v3.0.0).")
    ] = None,
    address_id: Annotated[
        str | None, typer.Option("--id", help="Short name (default: from the local part).")
    ] = None,
    smtp_host: Annotated[
        str | None,
        typer.Option("--smtp-host", help="SMTP server for sending (default: imap. → smtp.)."),
    ] = None,
    smtp_port: Annotated[
        int | None, typer.Option("--smtp-port", help="465 (TLS, default) or 587 (STARTTLS).")
    ] = None,
) -> None:
    """Add a mailbox: checks the app password by logging in (IMAP, and SMTP without sending),
    then stores it in the OS secret store. It starts in shadow (watch only) with outbound off.
    A Gmail address (gmail.com or googlemail.com) needs no --imap-host and a Google app password,
    which needs 2-Step Verification on the account (docs/gmail-setup.md); it sends at most 100
    emails a day once outbound is on (change: ecf address set --max-sends-per-day). Needs a real
    terminal."""
    require_terminal()
    with LocalClient(_paths()) as c:
        add_address(c, email, imap_host, sensitivity, preset, address_id,
                    smtp_host=smtp_host, smtp_port=smtp_port)  # fmt: skip


PRESET_NOTES = {
    "C": "Preset C: every message waits for /ecf-review (`ecf claude`) and uses your Claude plan;"
    " nothing else classifies it, however long it waits.",
}


def add_address(
    c: LocalClient,
    email: str,
    imap_host: str | None,
    sensitivity: str | None,
    preset: str | None,
    address_id: str | None,
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
) -> dict[str, Any]:
    """The prompts and request behind `ecf address add` (also used by `ecf init`)."""
    current = c.get("/v1/addresses")
    domain = email.rsplit("@", 1)[-1].strip().lower()
    known: dict[str, str] = current.get("imap_defaults") or {}
    if not imap_host and domain not in known:
        imap_host = typer.prompt("Its IMAP server (port 993, TLS)").strip()
    if sensitivity is None:
        suggestion = _suggest_sensitivity(email)
        sensitivity = typer.prompt(
            "Sensitivity (standard, or high for finance mailboxes)", default=suggestion
        )
    if preset is None:
        preset = typer.prompt("Preset (C all-Claude)", default="C")
    body: dict[str, object] = {
        "email": email,
        "imap_host": imap_host or known[domain],
        "sensitivity": sensitivity,
        "preset": (preset or "").upper(),
    }
    if address_id:
        body["address_id"] = address_id
    if smtp_host:
        body["smtp_host"] = smtp_host
    if smtp_port is not None:
        body["smtp_port"] = smtp_port
    public = domain in (current.get("public_domains") or [])
    if not current["org_domains"] and not public:  # not needed for gmail.com and the like (OD-441)
        typer.echo(
            "Your organization's domains decide which senders count as internal "
            "(change them later with `ecf config apply`)."
        )
        answer = typer.prompt("Organization domains, comma-separated", default=domain)
        body["org_domains"] = [d.strip() for d in answer.split(",") if d.strip()]
    if body["preset"] in PRESET_NOTES:
        typer.echo(PRESET_NOTES[str(body["preset"])])
    body["app_password"] = hidden(f"App password for {email} (hidden): ")
    a: dict[str, Any] = c.request("POST", "/v1/addresses", body)
    typer.echo(
        f"added {a['email']} as {a['address_id']!r}: {a['sensitivity']}, preset "
        f"{a['preset']}, stage {a['stage']}, outbound off"
    )
    _echo_probe(a)
    if a.get("max_sends_per_day"):
        typer.echo(f"sends: at most {a['max_sends_per_day']} a day once outbound is on (change:"
                   f" ecf address set {a['address_id']} --max-sends-per-day <n>)")  # fmt: skip
    if public and not current.get("org_addresses"):
        typer.echo("People you work with: list their addresses and names in org_addresses"
                   " (ecf config apply) so mail pretending to be them is caught.")  # fmt: skip
    if a.get("slack_channel"):
        typer.echo(f"Slack: private channel {a['slack_channel']} is created within a minute")
    return a


def _mb(size: int) -> str:
    """ecf's MB is 1,048,576 bytes, as in its 64 MB and 16 MB limits."""
    return f"{size / 1_048_576:.1f} MB"


def _size_note(size: int | None, source: str | None) -> str:
    if not size:
        return "unknown"
    if (source or "").startswith("provider table"):
        return f"{_mb(size)} (ecf caps its own limit to it)"
    return f"{_mb(size)} for uploads ({source}; not used as a receiving limit)"


def _echo_probe(a: dict[str, Any]) -> None:
    p = a.get("probe")
    if not p:
        return
    size = p["max_message_bytes"]
    typer.echo(
        f"probe: folders {', '.join(sorted(p['roles'].values())) or 'none marked'}; "
        f"keywords {'yes' if p['custom_keywords'] else 'no'}; "
        f"size limit {_size_note(size, p.get('max_size_source'))}"
    )
    smtp = p.get("smtp")
    if smtp and smtp.get("ok"):
        limit = f", size limit {_mb(smtp['size'])}" if smtp.get("size") else ""
        typer.echo(f"smtp: {smtp['host']}:{smtp['port']} login ok (nothing sent){limit}")
    for w in p["warnings"]:
        typer.echo(f"  note: {w}")


@address_app.command("list")
def address_list() -> None:
    """List monitored mailboxes."""
    with LocalClient(_paths()) as c:
        data = c.get("/v1/addresses")
    typer.echo(f"org domains: {', '.join(data['org_domains']) or 'none'};"
               f" org addresses: {data.get('org_addresses', 0)}")  # fmt: skip
    for a in data["addresses"]:
        flags = [a["stage"], a["sensitivity"], f"preset {a['preset']}"]
        if a["paused"]:
            flags.append("PAUSED")
        if a.get("probe") and a["probe"]["warnings"]:
            flags.append(f"{len(a['probe']['warnings'])} probe warning(s)")
        typer.echo(f"{a['address_id']:<16} {a['email']:<36} {', '.join(flags)}  ({a['imap_host']})")
    if not data["addresses"]:
        typer.echo("no addresses yet: `ecf address add <email> --imap-host <host>`")


@address_app.command("set")
def address_set(
    address: Annotated[str, typer.Argument(help="Address id or email.")],
    app_password: Annotated[
        bool, typer.Option("--app-password", help="Enter a new app password (hidden prompt).")
    ] = False,
    smtp_host: Annotated[
        str | None,
        typer.Option("--smtp-host", help="Send through this SMTP server instead (step-up)."),
    ] = None,
    smtp_port: Annotated[
        int, typer.Option("--smtp-port", help="465 (TLS) or 587 (STARTTLS), with --smtp-host.")
    ] = 465,
    max_sends_per_hour: Annotated[
        int | None, typer.Option("--max-sends-per-hour", help="Send limit per hour (25). (step-up)")
    ] = None,
    max_sends_per_day: Annotated[
        int | None, typer.Option("--max-sends-per-day", help="Send limit per day (250). (step-up)")
    ] = None,
) -> None:
    """Change a mailbox's settings: `--app-password` (re-enter or rotate), or `--smtp-host`
    (the server the app password is sent to; step-up and a Security Notice). (step-up)"""
    if app_password and smtp_host:
        raise typer.BadParameter("set --app-password and --smtp-host separately")
    asked = (("max_sends_per_hour", max_sends_per_hour), ("max_sends_per_day", max_sends_per_day))
    limits = {k: v for k, v in asked if v is not None}
    if limits:
        if app_password or smtp_host:
            raise typer.BadParameter("set the send limits on their own")
        with LocalClient(_paths()) as c:
            path = f"/v1/addresses/{address}"
            r = with_step_up(c, lambda n: c.request("POST", path, limits | {"stepup_nonce": n}),
                             echo=typer.echo)  # fmt: skip
        lim = r["limits"]
        typer.echo(f"{r['address_id']}: at most {lim['max_sends_per_hour']} sends an hour,"
                   f" {lim['max_sends_per_day']} a day")  # fmt: skip
        return
    if smtp_host:
        with LocalClient(_paths()) as c:
            body: dict[str, object] = {"smtp_host": smtp_host, "smtp_port": smtp_port}
            path = f"/v1/addresses/{address}"
            a = with_step_up(c, lambda n: c.request("POST", path, body | {"stepup_nonce": n}),
                             echo=typer.echo)  # fmt: skip
        typer.echo(f"{a['email']} now sends through {a['smtp_host']}:{a['smtp_port']}")
        _echo_probe(a)
        return
    if not app_password:
        raise typer.BadParameter(
            "nothing to set; use --app-password, --smtp-host or --max-sends-per-hour|-day"
        )
    require_terminal()
    with LocalClient(_paths()) as c:
        pw = hidden(f"New app password for {address} (hidden): ")
        a = c.request("POST", f"/v1/addresses/{address}", {"app_password": pw})
    typer.echo(f"stored a new app password for {a['email']} (login checked)")
    _echo_probe(a)


@stepup_app.command("test")
def stepup_test() -> None:
    """Check that step-up works on this computer. Changes nothing. (step-up)"""
    with LocalClient(_paths()) as c:
        step_up(c, "test", {"label": "ecf stepup test"}, echo=typer.echo)
    typer.echo("step-up works: the service checked it's you")


@address_app.command("retry")
def address_retry(address: Annotated[str, typer.Argument(help="Address id or email.")]) -> None:
    """Check a mailbox at the next minute, even while rejected logins retry only hourly."""
    with LocalClient(_paths()) as c:
        a = c.request("POST", f"/v1/addresses/{address}/retry")
    typer.echo(f"{a['email']} will be checked within a minute")


@address_app.command("remove")
def address_remove(address: Annotated[str, typer.Argument(help="Address id or email.")]) -> None:
    """Stop watching a mailbox and delete its app password from the secret store."""
    with LocalClient(_paths()) as c:
        target = next(
            (
                a
                for a in c.get("/v1/addresses")["addresses"]
                if address in (a["address_id"], a["email"].lower(), a["email"])
            ),
            None,
        )
        if target is None:
            raise typer.BadParameter(f"no address {address!r}")
        typer.echo("Its open items are resolved first (step-up if any is payment or fraud).")
        typed = typer.prompt(f"Type {target['email']} to remove it")
        if typed.strip().lower() != target["email"].lower():
            typer.echo("not removed")
            raise typer.Exit(1)
        path = f"/v1/addresses/{target['address_id']}"
        a = with_step_up(c, lambda n: c.request("DELETE", path, {"stepup_nonce": n}),
                         echo=typer.echo)  # fmt: skip
    typer.echo(f"removed {a['email']}; resolved {a['resolved']} open item(s).")
    if a.get("slack_channel_archived"):
        typer.echo(f"Slack channel {a['slack_channel_archived']} will be archived.")
    typer.echo("Left for you to do:")
    for line in a["residue"]:
        typer.echo(f"  - {line}")


RootOpt = Annotated[Path, typer.Option("--root", help="The synthetic set folder.")]


@eval_app.command("new-case")
def eval_new_case(
    case_id: Annotated[str, typer.Argument(help="Card id, e.g. bec-002.")],
    template: Annotated[
        str, typer.Option("--template", help="bec, injection, header, control or freemail.")
    ],
    root: RootOpt = EVAL_ROOT,
) -> None:
    """Write a new case card from a template (edit it, then `ecf eval build`)."""
    from ecf.eval.case_templates import TEMPLATES  # noqa: PLC0415

    if template not in TEMPLATES:
        raise typer.BadParameter(f"one of {', '.join(TEMPLATES)}", param_hint="--template")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", case_id):
        raise typer.BadParameter("lowercase letters, digits and hyphens", param_hint="CASE_ID")
    target = root / "cases" / f"{case_id}.md"
    if target.exists():
        raise typer.BadParameter(f"{target} already exists", param_hint="CASE_ID")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATES[template].format(id=case_id), encoding="utf-8")
    typer.echo(f"wrote {target}")


@eval_app.command("build")
def eval_build(root: RootOpt = EVAL_ROOT) -> None:
    """Hygiene-scan every card, then build the .eml files and labels.jsonl."""
    from ecf.eval.builder import build_all  # noqa: PLC0415

    report = build_all(root)
    if report.findings:
        for f in report.findings:
            typer.echo(f"FAIL  {f.where}: {f.kind}: {f.value}", err=True)
        typer.echo(
            f"hygiene scan failed: {len(report.findings)} finding(s); nothing built", err=True
        )
        raise typer.Exit(1)
    typer.echo(f"built {len(report.built)} committed + {len(report.large)} large (.build/)")


def _out(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _review_cards(root: Path, fields: str | None, *, add: bool, cases: str | None, redo: bool,
                  recheck: int) -> None:  # fmt: skip
    from datetime import date  # noqa: PLC0415

    from ecf import cli_card_review  # noqa: PLC0415
    from ecf.prompts import require_terminal  # noqa: PLC0415

    if fields is None:
        raise InvalidInputError("--add, --cases, --redo and --recheck go with --review")
    if recheck < 0:
        raise InvalidInputError("--recheck takes a positive number")
    globs = tuple(g.strip() for g in (cases or "").split(",") if g.strip())
    require_terminal()
    lines = cli_card_review.review(root, fields, read=input, write=_out, today=date.today(),
                                   add=add, globs=globs, redo=redo, recheck=recheck)  # fmt: skip
    for line in lines:
        typer.echo(line)
    typer.echo("commit reviews.jsonl, labels.jsonl and any changed cards to keep them")


def _import_fixed(root: Path, path: Path) -> None:
    from datetime import date  # noqa: PLC0415

    from ecf.eval import fixed_labels  # noqa: PLC0415

    try:
        doc: Any = json.loads(path.read_text(encoding="utf-8"))
        on = date.fromisoformat(doc["on"])
        item, answers = str(doc["item"]), doc["answers"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InvalidInputError(f"{path}: not a fixed-label answers file ({exc})") from None
    if not isinstance(answers, dict):
        raise InvalidInputError(f"{path}: answers must map case ids to answers")
    given = cast(dict[str, Any], answers)
    bad = [k for k, a in given.items() if not isinstance(a, dict)]
    if bad:
        raise InvalidInputError(f"{path}: each answer must be an object with label and how"
                                f" (not: {', '.join(map(str, bad[:5]))})")  # fmt: skip
    from ecf.eval import review  # noqa: PLC0415

    if item in review.CARD_FIELDS:  # a card field: written into the cards (S3b, OD-500)
        from ecf.cli_card_review import rebuild  # noqa: PLC0415

        got = review.record_page(root, item, given, on, rebuild)
        pending = cast(list[str], got["pending"])
        typer.echo(f"{item}: {got['answers']} cases recorded, {got['changed']} cards changed"
                   f" ({got['kept_items']} kept a fixed item the old value implied),"
                   f" {got['reconfirmed']} confirmed again"
                   + (f"; {len(pending)} not confirmed before, still pending (ecf eval label"
                      f" <id>): {', '.join(pending[:5])}" if pending else "")
                   + "; commit the cards, labels.jsonl and reviews.jsonl to keep them")  # fmt: skip
        return
    changed = fixed_labels.record(root, item, given, on)
    yes = sum(1 for a in given.values() if a.get("label") is True)
    typer.echo(f"{item}: {len(given)} cases recorded ({yes} yes), {changed} new or changed;"
               " commit fixed_labels.jsonl and reviews.jsonl to keep them")  # fmt: skip


def _label_corpus(corpus: Path | None, again: frozenset[int], marked: bool) -> None:
    from datetime import date  # noqa: PLC0415

    from ecf import cli_corpus_label  # noqa: PLC0415
    from ecf.prompts import hidden, require_terminal  # noqa: PLC0415

    if corpus is None:
        raise InvalidInputError("--again and --marked go with --corpus")
    require_terminal()
    cli_corpus_label.require_output_terminal()
    where = corpus.expanduser().absolute()
    secret = hidden(f"Passphrase for {where.name} (hidden): ")
    typer.echo("Label in Terminal, not in a Claude session; consider turning off this terminal's"
               " Restore windows setting (SPEC §12.2).")  # fmt: skip
    with LocalClient(_paths()) as c:
        tally = cli_corpus_label.label(c, where, secret, read=input, write=_out,
                                       today=date.today(), again=again,
                                       marked=marked)  # fmt: skip
    for line in cli_corpus_label.summary(tally, cli_corpus_label.cl.path_for(where)):
        typer.echo(line)


@eval_app.command("label")
def eval_label(  # noqa: PLR0912, PLR0913, PLR0915, PLR0917 - typer options; three modes
    case_id: Annotated[
        str | None, typer.Argument(help="One case (default: every pending one).")
    ] = None,
    root: RootOpt = EVAL_ROOT,
    status_only: Annotated[
        bool, typer.Option("--status", help="Only count what's confirmed.")
    ] = False,
    show_flags: Annotated[
        bool,
        typer.Option("--show-flags", help="Only the pending cases flagged for your judgement."),
    ] = False,
    results: Annotated[
        Path | None,
        typer.Option(
            "--results",
            help="An eval result file to compare with (default: the"
            " newest in this install's evals folder).",
        ),
    ] = None,
    corpus: Annotated[
        Path | None,
        typer.Option("--corpus", help="Label a real-mail corpus file instead (blind; §16.7)."),
    ] = None,
    again: Annotated[
        list[int] | None,
        typer.Option("--again", help="With --corpus: label message N again (repeatable)."),
    ] = None,
    marked: Annotated[
        bool,
        typer.Option("--marked", help="With --corpus: go back to skipped or unsure messages."),
    ] = False,
    review: Annotated[
        str | None,
        typer.Option(
            "--review",
            help="Blind review: answer these fields (comma-separated, or all) for every card"
            " from the email alone, then compare with its labels (§16.1).",
        ),
    ] = None,
    add: Annotated[
        bool,
        typer.Option("--add", help="With --review: also ask the fields a card leaves unlabelled."),
    ] = False,
    cases: Annotated[
        str | None,
        typer.Option(
            "--cases", help="With --review: only cards whose id matches (globs, comma-separated)."
        ),
    ] = None,
    redo: Annotated[
        bool,
        typer.Option("--redo", help="With --review: ask fields already reviewed on this file too."),
    ] = False,
    import_fixed: Annotated[
        Path | None,
        typer.Option(
            "--import-fixed",
            help="Record a card review page's answers for one fixed item, or for category,"
            ' sender_type or fraud_risk: a JSON file {"item": ..., "on": "YYYY-MM-DD",'
            ' "answers": {id: {"label": true|false or a value, "how": "single"|"bulk"}}}'
            " (a fixed item: fixed_labels.jsonl; a card field: the cards, rebuilt).",
        ),
    ] = None,
    recheck: Annotated[
        int,
        typer.Option(
            "--recheck",
            help="With --review: ask N reviewed cards again (a seeded"
            " sample) and report how often you answer the same.",
        ),
    ] = 0,
) -> None:
    """Confirm each case's expected labels (only you; OD-229, OD-241). A confirmed case counts
    toward the gates; editing its card undoes the confirmation. Cases whose card has a `review`
    note are flagged: the note says what to judge. With --corpus: label a real-mail corpus,
    without seeing any model's answer. With --review: re-judge labels blind, from the email
    alone; answers are logged in reviews.jsonl."""
    if corpus is not None or again or marked:
        _label_corpus(corpus, frozenset(again or ()), marked)
        return
    if import_fixed is not None:
        _import_fixed(root, import_fixed)
        return
    if review is not None or add or cases or redo or recheck:
        if case_id or show_flags or results or status_only:
            raise InvalidInputError(
                "--review takes no case id, --show-flags, --results or --status"
            )
        _review_cards(root, review, add=add, cases=cases, redo=redo, recheck=recheck)
        return
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.cards import load_cards  # noqa: PLC0415
    from ecf.prompts import require_terminal  # noqa: PLC0415

    n = labels.counts(root)
    from ecf.eval import fixed_labels  # noqa: PLC0415

    fixed, stale = fixed_labels.current(root)
    if fixed or stale:
        typer.echo(f"{len(fixed)} cases with hand-set fixed-item labels"
                   + (f"; {len(stale)} stale (card changed): {', '.join(stale[:5])}" if stale
                      else ""))  # fmt: skip
    cards = {c.id: c for c in load_cards(root / "cases")}
    flagged = [r for r in labels.pending(root) if (c := cards.get(r["id"])) and c.review]
    typer.echo(f"{n['confirmed']} of {n['cases']} cases confirmed")
    if flagged:
        typer.echo(f"{len(flagged)} pending cases flagged for your judgement (--show-flags)")
    if status_only:
        return
    require_terminal()
    from ecf.eval.results import differences, latest, load_result  # noqa: PLC0415

    run = load_result(results) if results else latest(_paths().data_dir / "evals")
    got = {c.id: c.got for c in run.cases if c.got} if run else {}
    if got and run:
        typer.echo(f"model answers from eval {run.run_id[:8]} ({run.created_at[:10]})")
    todo = [r for r in (flagged if show_flags else labels.pending(root))
            if case_id is None or r["id"] == case_id]  # fmt: skip
    if case_id and not todo:
        typer.echo(f"{case_id}: nothing to confirm (unknown, or already confirmed)")
        return
    for r in todo:
        card = cards.get(r["id"])
        if card is None:
            continue
        typer.echo("")
        typer.echo(f"== {card.id} ({card.author}): {card.title}")
        if card.review:
            typer.echo(f"   FLAG, needs your judgement: {card.review}")
            typer.echo("   y = you agree with the expected values below as written;"
                       " n = you'd change them (it stays pending; say what to change)")  # fmt: skip
        typer.echo(f"   tests: {card.threat}; control: {card.control}")
        typer.echo(f"   from: {card.from_}   subject: {card.subject}")
        body = " ".join(card.body.split())
        typer.echo(f"   body: {body[:400]}{'...' if len(body) > 400 else ''}")
        typer.echo(f"   expected: {json.dumps(r['expected'], sort_keys=True)}")
        if card.id in got:
            diff = differences(r["expected"], got[card.id])
            typer.echo("   model returned: " + ("; ".join(diff) if diff else "the expected values"))
        answer = typer.prompt("   Right? [y]es / [n]o, skip / [q]uit", default="n").strip().lower()
        if answer == "q":
            break
        if answer == "y":
            labels.confirm(root, card.id, datetime.now(UTC).date())
            typer.echo("   confirmed")
        else:
            typer.echo(f"   skipped: fix {card.id}.md, run `ecf eval build`, then label it again")
    n = labels.counts(root)
    typer.echo(
        f"{n['confirmed']} of {n['cases']} cases confirmed; commit labels.jsonl to keep them"
    )


@eval_app.command("show")
def eval_show(case_id: str, root: RootOpt = EVAL_ROOT) -> None:
    """Show a case the way a mail client would: headers, text, attachments."""
    from ecf.eval.builder import build_message  # noqa: PLC0415
    from ecf.eval.cards import parse_card  # noqa: PLC0415

    path = root / "cases" / f"{case_id}.md"
    card = parse_card(path.read_text(encoding="utf-8"), source=path.name)
    msg = build_message(card)
    for name in ("From", "To", "Cc", "Reply-To", "Subject", "Date", "Message-ID"):
        if msg[name]:
            typer.echo(f"{name}: {msg[name]}")
    typer.echo("")
    typer.echo(card.body.rstrip()[:4000])
    for att in card.attachments:
        typer.echo(f"[attachment] {att.name}" + (" (generated PDF)" if att.generate else ""))


@eval_app.command("run")
def eval_run(  # noqa: PLR0913, PLR0917 - typer options
    root: RootOpt = EVAL_ROOT,
    classifier: Annotated[bool, typer.Option("--classifier/--no-classifier")] = True,
    fraud_only: Annotated[
        bool, typer.Option("--fraud-only", help="Only fraud, injection and escalation cases.")
    ] = False,
    claude: Annotated[
        bool, typer.Option("--claude", help="Register a run for /ecf-eval in `ecf claude`.")
    ] = False,
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            help="With --claude: C, or D (Jev's classifications from the latest complete"
            " `ecf eval run --classifier-backend jev`, Claude as the actor; comparison only).",
        ),
    ] = "C",
    sensitivity: Annotated[
        str, typer.Option("--sensitivity", help="With --claude: standard or high.")
    ] = "standard",
    classifier_model: Annotated[
        str | None, typer.Option("--classifier-model", help="With --claude: compare this model.")
    ] = None,
    actor_model: Annotated[
        str | None, typer.Option("--actor-model", help="With --claude: compare this model.")
    ] = None,
    batch: Annotated[
        int, typer.Option("--batch", help="With --claude: items per classifier-high spawn (1-5).")
    ] = 1,
    classifier_backend: Annotated[
        str,
        typer.Option(
            "--classifier-backend",
            help="null (default): every field at its least risky value, the rules alone (SPEC"
            " §7.8; never counts for the go-live gate). jev: TypeSafe Jev (needs `ecf models"
            " api-key set --provider typesafe`; sends the synthetic cards to TypeSafe; not for"
            " the gate until v3.0.0 step 4). gemma and systemone:<name> were removed in v3.0.0"
            " (ADR 0031).",
        ),
    ] = "null",
    redact: Annotated[
        bool,
        typer.Option(
            "--redact/--no-redact",
            help="With --fraud-only: --no-redact lets"
            " injection text reach the model (a reported figure; never counts"
            " for the go-live gate).",
        ),
    ] = True,
    corpus: Annotated[
        Path | None,
        typer.Option("--corpus", help="Removed in v3.0.0 (ADR 0031): no model runs a corpus."),
    ] = None,
    not_for_gate: Annotated[
        bool,
        typer.Option(
            "--not-for-gate",
            help="An experiment: saved and reported, never counts for the go-live gate.",
        ),
    ] = False,
    copy_to: Annotated[
        Path | None,
        typer.Option(
            "--copy-to",
            help="Also write the result file to this folder (default: state-archive/evals/"
            " when run from a checkout; none otherwise).",
        ),
    ] = None,
    signals: Annotated[
        bool,
        typer.Option(
            "--signals/--no-signals",
            help="--no-signals asks the model without the derived signals (OD-484; a model"
            " backend only; an experiment, never counts for the go-live gate).",
        ),
    ] = True,
    jev_state: Annotated[
        str,
        typer.Option(
            "--jev-state",
            help="With jev: the state's format, s1 (default) or s2;"
            " s2 is a measurement, not for the gate.",
        ),
    ] = "s1",
    jev_preamble: Annotated[
        str,
        typer.Option(
            "--jev-preamble",
            help="With jev: where ecf's preamble goes, state"
            " (default) or instructions; instructions is a measurement, not for the"
            " gate.",
        ),
    ] = "state",
    jev_concurrency: Annotated[
        int, typer.Option("--jev-concurrency", help="With jev: 1 (the only value in v3.0.0).")
    ] = 1,
    jev_fraud_risk: Annotated[
        str,
        typer.Option(
            "--jev-fraud-risk",
            help="With jev: ask fraud_risk as a score (default) or a choice; choice is a"
            " measurement, not for the gate.",
        ),
    ] = "score",
    jev_option_order: Annotated[
        str,
        typer.Option(
            "--jev-option-order",
            help="With jev: choice options in schema order (default) or reversed; reversed is"
            " a measurement, not for the gate.",
        ),
    ] = "schema",
    jev_extra_question: Annotated[
        str | None,
        typer.Option(
            "--jev-extra-question",
            help="With jev: also ask injection_attempt (recorded, never classified); a"
            " measurement, not for the gate.",
        ),
    ] = None,
) -> None:
    """Run the synthetic set through ecf's rules with the null classifier (every field at its
    least risky value; seconds, no model): the rules-alone floor and a wiring check, never
    counted for the go-live gate. With --claude, register a run for Claude instead: open `ecf
    claude` and type /ecf-eval (it uses your Claude plan); that run counts for the gate."""
    if corpus is not None:
        raise InvalidInputError("corpus runs were removed in v3.0.0 (ADR 0031): `ecf eval routes"
                                " --corpus` and `ecf eval rescore --corpus` still read a"
                                " corpus")  # fmt: skip
    copy = _copy_folder(copy_to)
    if claude:
        if not classifier:
            raise InvalidInputError("--no-classifier is for `ecf eval run` without --claude")
        jev_opts = (
            jev_state,
            jev_preamble,
            jev_concurrency,
            jev_fraud_risk,
            jev_option_order,
            jev_extra_question,
        ) != ("s1", "state", 1, "score", "schema", None)
        if classifier_backend != "null" or not redact or not_for_gate or not signals or jev_opts:
            raise InvalidInputError("--classifier-backend, --no-redact, --not-for-gate,"
                                    " --no-signals and the --jev-* options are for `ecf eval"
                                    " run` without --claude")  # fmt: skip
        _claude_eval(root, preset.upper(), sensitivity, fraud_only, classifier_model,
                     actor_model, batch, copy)  # fmt: skip
        return
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/eval/runs", {
            "root": str(root.resolve()), "classifier": classifier, "fraud_only": fraud_only,
            "backend": classifier_backend, "redact": redact, "for_gate": not not_for_gate,
            "signals": signals, "copy_to": str(copy) if copy else None,
            "jev_state": jev_state, "jev_preamble": jev_preamble,
            "jev_concurrency": jev_concurrency, "jev_fraud_risk": jev_fraud_risk,
            "jev_option_order": jev_option_order, "jev_extra": jev_extra_question})  # fmt: skip
    typer.echo(f"Eval {r['run_id'][:8]} started: {r['total']} cases.")
    if copy:
        typer.echo(f"  The result is also written to {copy}/ when the run ends.")
    _follow_hints()


def _ecf(rest: str) -> str:
    """A command to copy, naming the install when it isn't the default."""
    return "ecf " + ("" if STATE.install == "default" else f"--install {STATE.install} ") + rest


def _follow_hints() -> None:
    typer.echo(f"  Progress:  {_ecf('eval status')}")
    typer.echo(f"  Stop:      {_ecf('eval stop')}")


def _copy_folder(copy_to: Path | None, cwd: Path | None = None) -> Path | None:
    """Where `ecf eval run` also writes its result (plan 0.3): `--copy-to`, else
    `state-archive/evals/` (gitignored) when run from inside an ecf checkout, else nowhere."""
    if copy_to is not None:
        return copy_to.expanduser().resolve()
    here = (cwd or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".git").exists() and (folder / "src" / "ecf_server").is_dir():
            return folder / "state-archive" / "evals"
    return None


def _claude_eval(root: Path, preset: str, sensitivity: str, fraud_only: bool,
                 classifier_model: str | None, actor_model: str | None,
                 batch: int, copy: Path | None) -> None:  # fmt: skip
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/eval/claude", {
            "root": str(root.resolve()), "preset": preset, "sensitivity": sensitivity,
            "fraud_only": fraud_only, "classifier_model": classifier_model,
            "actor_model": actor_model, "batch": batch,
            "copy_to": str(copy) if copy else None})  # fmt: skip
    models = ", ".join(f"{k} {v}" for k, v in r["models"].items())
    use = ("the pinned models: counts for the go-live gate" if r["pinned"]
           else PRESET_D_ONLY if r["preset"] == "D"
           else "other models: for comparison only")  # fmt: skip
    typer.echo(f"Claude eval {r['run_id'][:8]} registered: {r['total']} cases, preset"
               f" {r['preset']}, {r['sensitivity']}; {use} ({models}).")  # fmt: skip
    typer.echo("Preparing the cases (under a minute for the full set). Open `ecf claude` and"
               " type /ecf-eval; it uses your Claude plan. The run expires in 24 hours;"
               " `ecf eval stop` ends it.")  # fmt: skip
    if copy:
        typer.echo(f"The result is also written to {copy}/ when the run ends.")
    if not fraud_only:
        typer.echo("For a first run, `--fraud-only` uses less of the plan.")


@eval_app.command("rescore")
def eval_rescore(
    result: Annotated[Path, typer.Argument(help="An eval result file.")],
    corpus: Annotated[Path | None, typer.Option("--corpus", help="The corpus it ran on.")] = None,
    synthetic: Annotated[
        Path | None,
        typer.Option(
            "--synthetic",
            help="The synthetic set folder: score a result made before schema v3"
            " against its current labels, answers mapped to v3 (fixed items derived).",
        ),
    ] = None,
    through_policy: Annotated[
        bool,
        typer.Option(
            "--through-policy",
            help="With --synthetic: recompute each card's facts and plan the recorded answers"
            " again with today's keywords, rules and policy.",
        ),
    ] = False,
) -> None:
    """Score a corpus result again against the corpus's current labels, or a synthetic result
    against the set's current labels (an older one with its answers mapped to v3, or with
    --through-policy planned again with today's facts and rules), without running the models;
    the new file sits beside the original (§16.7, §16.5)."""
    from ecf.eval.results import load_result as load  # noqa: PLC0415
    from ecf.eval.results import summary  # noqa: PLC0415
    from ecf.prompts import hidden, require_terminal  # noqa: PLC0415

    if (corpus is None) == (synthetic is None):
        raise typer.BadParameter("give --corpus or --synthetic")
    if through_policy and synthetic is None:
        raise typer.BadParameter("--through-policy goes with --synthetic")
    if synthetic is not None:
        with LocalClient(_paths()) as c:
            r = c.request("POST", "/v1/eval/rescore", {
                "result": str(result.expanduser().absolute()),
                "synthetic_root": str(synthetic.expanduser().absolute()),
                "through_policy": through_policy}, timeout=600)  # fmt: skip
        typer.echo(f"written: {r['path']}")
        typer.echo(summary(load(Path(r["path"]))))
        for line in replanned_lines(r["summary"]):
            typer.echo(line)
        return
    require_terminal()
    where = cast(Path, corpus).expanduser().absolute()
    secret = hidden(f"Passphrase for {where.name} (hidden): ")
    with LocalClient(_paths()) as c:
        body = {"path": str(where), "passphrase": secret}
        opened = c.request("POST", "/v1/corpus/session", body, timeout=600)
        del secret, body
        sid = opened["session_id"]
        try:
            r = c.request("POST", "/v1/eval/rescore", {
                "result": str(result.expanduser().absolute()), "corpus_session": sid},
                timeout=600)  # fmt: skip
        finally:
            c.request("DELETE", f"/v1/corpus/session/{sid}")
    typer.echo(f"written: {r['path']}")
    typer.echo(summary(load(Path(r["path"]))))


@eval_app.command("routes")
def eval_routes(
    sets: Annotated[
        list[Path] | None,
        typer.Argument(help="Synthetic set folders (the frozen set first)."),
    ] = None,
    result: Annotated[
        list[Path] | None,
        typer.Option("--result", help="A result on the first set to attribute (repeatable)."),
    ] = None,
    corpus: Annotated[
        Path | None, typer.Option("--corpus", help="A corpus file: its stored messages' routes.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full report as JSON.")] = False,
) -> None:
    """What the fraud routes (OD-490) do on synthetic sets: per set, the cards each route makes
    fraud or weak as built, as draft 2 had them and without them, their second signals, and a pass
    with every sender taken as known; per result, which mechanism changed each case's rule or
    actions; on a corpus, how many stored messages a route would escalate. No model runs
    (pre-classifier plan steps 7b and 7c, SPEC §16.2)."""
    from ecf.prompts import hidden, require_terminal  # noqa: PLC0415

    if not sets and corpus is None:
        raise typer.BadParameter("give a synthetic set folder or --corpus")
    body: dict[str, Any] = {
        "sets": [str(p.expanduser().absolute()) for p in sets or []],
        "results": [str(p.expanduser().absolute()) for p in result or []],
    }
    with LocalClient(_paths()) as c:
        if corpus is None:
            r = c.request("POST", "/v1/eval/routes", body, timeout=600)
        else:
            require_terminal()
            where = corpus.expanduser().absolute()
            secret = hidden(f"Passphrase for {where.name} (hidden): ")
            opened = c.request("POST", "/v1/corpus/session",
                               {"path": str(where), "passphrase": secret}, timeout=600)  # fmt: skip
            del secret
            sid = opened["session_id"]
            try:
                r = c.request("POST", "/v1/eval/routes", body | {"corpus_session": sid},
                              timeout=600)  # fmt: skip
            finally:
                c.request("DELETE", f"/v1/corpus/session/{sid}")
    if as_json:
        typer.echo(json.dumps(r, indent=1))
        return
    for line in route_lines(r):
        typer.echo(line)


def route_lines(r: dict[str, Any]) -> list[str]:
    """`ecf eval routes` as text."""
    out: list[str] = []
    for st in r["sets"]:
        out.append(
            f"{Path(st['set']).name}: {st['confirmed']} confirmed cards, {st['scam']} scam"
            + (f" ({st['unconfirmed']} unconfirmed left out)" if st["unconfirmed"] else "")
        )
        for name, v in st["variants"].items():
            out.append(
                f"  {name}: route fraud {len(v['fraud']['scam'])} scam / "
                f"{_ids(v['fraud']['genuine'])} genuine; weak {len(v['weak']['scam'])} scam / "
                f"{_ids(v['weak']['genuine'])} genuine; rules alone fraud_guard "
                f"{len(v['alone_fraud_guard']['scam'])} scam / "
                f"{_ids(v['alone_fraud_guard']['genuine'])} genuine; model-only fraud "
                f"{len(v['model_only_fraud'])}")  # fmt: skip
        sig = ", ".join(f"{k} {n}" for k, n in st["draft4_signals"].items()) or "none"
        out.append(f"  draft4 second signals: {sig}")
        out.append(f"  only signal a sender with no history: {_ids(st['first_time_only'])}")
        seen = st["seen_forced"]
        out.append(
            f"  every sender known: route lost {_ids(seen['lost_route'])}; weak only"
            f" {_ids(seen['weak'])}; model-only fraud {len(seen['model_only_fraud'])}"
        )
        fresh = st["fresh_mailbox_route_fraud"]
        out.append(f"  every sender new (a fresh mailbox): route fraud {len(fresh['scam'])} scam"
                   f" / {_ids(fresh['genuine'])} genuine")  # fmt: skip
    if (cp := r.get("corpus")) is not None:
        out += _corpus_route_lines(cp)
    for res in r["results"]:
        mo = res["model_only_fraud_cases"]
        out.append(f"{res['result']}: {len(res['changed'])} cases changed; by mechanism "
                   + ", ".join(f"{k} {n}" for k, n in res["by_mechanism"].items())
                   + f"; model-only fraud {mo[0]} -> {mo[1]}; unsafe {_ids(res['unsafe'])};"
                   f" genuine hide refused {_ids(res['genuine_hide_dropped'])}")  # fmt: skip
        for c in res["changed"]:
            why = ", ".join(c["mechanisms"]) or "unexplained"
            iban = "; valid IBAN present" if c["iban_bank"] else ""
            out.append(f"  {c['id']}: {c['rule'][0]} -> {c['rule'][1]} ({why}{iban})")
        out.append(f"  against the routes off: {'PASS' if res['verdict'] else 'FAIL'}")
        out += _against_lines(res["routes_off"], "    ")
        out.append("  in a fresh mailbox (every sender new; reported, not judged):")
        out += _against_lines(res["fresh_mailbox"], "    ")
    return out


def _against_lines(d: dict[str, list[str]], pad: str) -> list[str]:
    names = (("scam_gained", "scams caught only with the routes"),
             ("scam_lost", "scams caught only without them"),
             ("scam_missed", "scams not caught"),
             ("genuine_newly_escalated", "genuine newly escalated"),
             ("genuine_newly_weak", "genuine newly weak (rule 1b)"),
             ("genuine_escalated_either_way", "genuine escalated either way"))  # fmt: skip
    return [f"{pad}{what}: {_ids(d[key])}" for key, what in names]


def _corpus_route_lines(cp: dict[str, Any]) -> list[str]:
    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v}%"

    out = [f"corpus {str(cp['corpus_id'])[:8]}: {cp['messages']} messages, {cp['labelled']}"
           " labelled (keywords: fetch-time plus a rescan of the stored excerpts)"]  # fmt: skip
    for what, v in cp["routes"].items():
        out.append(f"  {what}: fraud {_ids(v['fraud'])}; weak {_ids(v['weak'])}")
    out.append(f"  would escalate: {_ids(cp['would_escalate'])} ({pct(cp['would_escalate_pct'])}"
               f" of messages); by a route alone: {_ids(cp['escalated_only_by_a_route'])}"
               f" ({pct(cp['escalated_only_by_a_route_pct'])})")  # fmt: skip
    out.append(f"  with a sender with no history among the signals: {_ids(cp['first_time'])}"
               f" ({pct(cp['first_time_pct'])} of those escalated); as the only signal:"
               f" {_ids(cp['first_time_only'])}")  # fmt: skip
    out.append(f"  labelled harmless but would escalate: {_ids(cp['labelled_harmless'])}")
    return out


def replanned_lines(s: dict[str, Any]) -> list[str]:
    """What `--through-policy` changed, and the genuine-mail figures (pre-classifier plan step 7);
    nothing for another rescore."""
    re_: dict[str, Any] | None = s.get("replanned")
    if re_ is None:
        return []
    actor: dict[str, int] = re_["actor"]
    return [f"rule changed: {_ids(re_['rule_changed'])}",
            f"actions changed: {_ids(re_['actions_changed'])}",
            f"actor proposals: {actor['recorded']} recorded, {actor['inferred']} inferred,"
            f" {actor['partial']} partly inferred, {actor['nothing']} nothing to replay,"
            f" {actor['not_on_record']} not on record",
            f"model-only fraud cases: {s.get('rescored_from', {}).get('model_only_fraud_cases')}"
            f" -> {s.get('model_only_fraud_cases')}",
            *_genuine_figures(s)]  # fmt: skip


def _ids(xs: list[str]) -> str:
    if not xs:
        return "0"
    return f"{len(xs)} ({', '.join(xs[:8])}{', ...' if len(xs) > 8 else ''})"


def _measurement_figures(s: dict[str, Any]) -> list[str]:
    """The per-stage measurements (pre-classifier plan §11); absent in older results. Figures
    are p50/p95/max."""
    m: dict[str, Any] | None = s.get("measurements")
    if m is None:
        return []
    sig = m["signals"]
    out = [
        f"facts and excerpts per email {_three(m['facts_ms'])} ms, trigger scan"
        f" {_three(m['scan_ms'])} ms, {m['keyword_patterns']} keyword patterns",
        f"classifier excerpt {_three(m['excerpt_chars'])} chars; signals block on"
        f" {sig['cases']} of {sig['of']} cases, {_three(sig['block_chars'])} chars",
    ]
    env: dict[str, Any] = s.get("environment") or {}
    start: dict[str, Any] = env.get("start") or {}
    if rows := start.get("rows"):
        out.append(f"history: {rows['senders']} senders, {rows['items']} items")
    calls: dict[str, Any] = m.get("calls") or {}
    for role in ("classifier", "actor"):
        if (c := calls.get(role)) is not None:
            out.append(
                f"{role} tokens: prompt {_three(c['prompt_tokens'])}, uncached"
                f" {_three(c['uncached_tokens'])}, cached {_three(c['cached_tokens'])},"
                f" output {_three(c['output_tokens'])}"
            )
    return out


def _three(d: dict[str, Any]) -> str:
    """p50/p95/max."""
    return "/".join("-" if d.get(k) is None else f"{d[k]:g}" for k in ("p50", "p95", "max"))


def _genuine_figures(s: dict[str, Any]) -> list[str]:
    """`genuine_hide_dropped` and `genuine_flagged` (pre-classifier plan step 7); absent in
    older results."""
    if "genuine_hide_dropped" not in s:
        return []
    out = [f"genuine cases with a hide refused by a blocker: {_ids(s['genuine_hide_dropped'])}"]
    flagged: dict[str, dict[str, list[str]]] = s.get("genuine_flagged") or {}
    for who, what in (("run", "the run"), ("rules_alone", "the rules alone")):
        for rule, xs in (flagged.get(who) or {}).items():
            out.append(f"genuine cases at {rule} ({what}): {_ids(xs)}")
    return out


@eval_app.command("status")
def eval_status() -> None:
    """The running eval's progress and the latest results."""
    with LocalClient(_paths()) as c:
        st = c.get("/v1/eval/runs")
    for line in status_lines(st):
        typer.echo(line)


def status_lines(st: dict[str, Any]) -> list[str]:
    """`ecf eval status`: the running evals, then each recent synthetic run on two lines (its
    score, then its safety checks and gate). Corpus runs (before v3.0.0) were never listed as
    recent (§16.7)."""
    cur, cl = st["current"], st.get("claude")
    out: list[str] = []
    if cl is not None:
        prepared = (f", {cl['prepared']}/{cl['total']} prepared"
                    if cl["state"] == "preparing" else "")  # fmt: skip
        out += [f"Claude eval {cl['run_id'][:8]}: {cl['state']}, {cl['done']}/{cl['total']}"
                f" cases scored{prepared}" + (f" ({cl['detail']})" if cl["detail"] else ""),
                f"  preset {cl['preset']}, {cl['sensitivity']}, {_pin_kind(cl)}"]  # fmt: skip
    if cur["state"] != "idle":
        out.append(f"Eval {cur['run_id'][:8]}: {cur['state']}, {cur['done']}/{cur['total']}"
                   + (f" ({cur['detail']})" if cur["detail"] else ""))  # fmt: skip
    if st["recent"]:
        out += [""] if out else []
        out.append("Recent runs (synthetic set):")
    for r in st["recent"]:
        m = r["metrics"]
        safety = (
            f"unsafe {len(m.get('unsafe', []))},{_recall(m)}"
            f" gate {'passed' if r['gate_passed'] else 'NOT passed'}{_run_caveat(m)}"
        )
        out += [f"  {r['created_at'][:10]} {r['created_at'][11:16]}  {r['run_id'][:8]}"
                f"  {m.get('correct')}/{m.get('confirmed')} confirmed cases correct"
                f" ({m.get('accuracy')}%, Wilson {m.get('wilson95')})",
                f"{'':30}{safety}"]  # fmt: skip
    if not out:
        out.append(f"No eval has run yet: {_ecf('eval run')}")
    return out


def _recall(m: dict[str, Any]) -> str:
    """Fraud-guard recall (§16.5, D2), with a trailing comma; empty for runs saved before it
    was counted or with no case expecting the fraud guard."""
    if m.get("fraud_guard_recall") is None:
        return ""
    n = int(m.get("fraud_guard_cases") or 0)
    hit = n - len(m.get("fraud_guard_missed") or [])
    return f" fraud-guard recall {hit}/{n} ({m['fraud_guard_recall']}%),"


INCOMPLETE = {  # `incomplete_reason` (v3.0.0 step 2, P22)
    "stopped": "stopped after {n} cases",
    "faulted": "stopped early after {n} cases: too many faulted cases",
    "faulted_safety_card": "incomplete: a fraud, escalation or injection card faulted",
    "budget": "stopped after {n} cases: the waiting budget was spent",
}


# a preset D run can't pass the gate until step 4 adds D to the pins (J8)
PRESET_D_ONLY = "comparison only (preset D, until step 4)"


def _pin_kind(f: dict[str, Any]) -> str:
    """A Claude eval's models: the pins, or comparison only (J8)."""
    if f.get("pinned"):
        return "pinned models"
    return PRESET_D_ONLY if f.get("preset") == "D" else "comparison"


BACKEND_CAVEAT = {
    "null": "null backend: the rules alone, never for the gate",  # §7.8; R-D6
    "jev": "jev backend: not for the gate until step 4",  # gate-eligible from v3.0.0 step 4
}


def _run_caveat(m: dict[str, Any]) -> str:  # noqa: PLR0911 - one return per caveat
    """Why a run can't pass the go-live gate whatever its score (§9.3)."""
    opts: dict[str, Any] = m.get("options") or {}
    if m.get("complete") is False:
        why = INCOMPLETE.get(str(m.get("incomplete_reason")), INCOMPLETE["stopped"])
        return f" ({why.format(n=m.get('cases'))})"
    claude = m.get("claude")
    if isinstance(claude, dict) and not m.get("gate_passed"):
        cl = cast(dict[str, Any], claude)
        if cl.get("pinned") is False:  # never gate-eligible (J8)
            return (f", {PRESET_D_ONLY}" if cl.get("preset") == "D"
                    else ", comparison only (not the pinned models)")  # fmt: skip
    # an eval-only switch off, or a Jev measurement option away from its default (§6)
    switched: list[Any] = opts.get("switches_off") or []
    measuring: list[Any] = opts.get("measuring") or []
    off = [f"{x} off" for x in switched] + [str(x) for x in measuring]
    if (backend := BACKEND_CAVEAT.get(str(m.get("backend")))) is not None:
        return f" ({backend}" + "".join(f"; {x}" for x in off) + ")"
    if opts and not (opts.get("classifier") and opts.get("actor")):
        return " (without the " + ("actor" if opts.get("classifier") else "classifier") + ")"
    if off:
        return " (not for the gate: " + ", ".join(off) + ")"
    if opts.get("for_gate") is False:
        return " (not for the gate)"
    return ""


@eval_app.command("stop")
def eval_stop() -> None:
    """Stop the running eval after its current case."""
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/eval/runs/stop", {})
    if r.get("local"):
        typer.echo(f"stopping eval {r['local']['run_id'][:8]} after its current case")
    if r.get("claude"):
        cl = r["claude"]
        saved = ", result saved" if cl["done"] else ", nothing to save"
        typer.echo(f"Claude eval {cl['run_id'][:8]} stopped: {cl['done']} of {cl['total']}"
                   f" cases scored{saved}")  # fmt: skip


@eval_app.command("compare")
def eval_compare(
    a: Path,
    b: Path,
    margin: Annotated[float, typer.Option(min=0, help="Non-inferiority margin, in points")] = 5.0,
    subset: Annotated[
        Path | None,
        typer.Option(
            "--subset",
            help="Compare only these case ids (a result file, a labels.jsonl, or one id per"
            " line); needed when the two runs used different sets.",
        ),
    ] = None,
) -> None:
    """Compare two result files (paired: exact McNemar, Newcombe interval, non-inferiority at
    -5 points unless --margin says otherwise; per field with Holm; SPEC §16.5, OD-486)."""
    from ecf.eval.results import (  # noqa: PLC0415
        compare,
        compare_endpoints,
        compare_fields,
        input_warnings,
        load_result,
        schema_warning,
        summary,
    )

    ra, rb = load_result(a), load_result(b)
    ids = _subset_ids(subset) if subset is not None else None
    c = compare(ra, rb, ids)
    if ids is not None and subset is not None:
        typer.echo(f"subset: {len(ids)} case ids from {subset.name}"
                   + (f"; the sets differ ({ra.set_version[:12]} vs {rb.set_version[:12]})"
                      if ra.set_version != rb.set_version else ""), err=True)  # fmt: skip
    if warning := schema_warning(ra, rb):
        typer.echo(warning, err=True)
    for warning in input_warnings(ra, rb):
        typer.echo(warning, err=True)
    typer.echo(f"A  {summary(ra)}")
    typer.echo(f"B  {summary(rb)}")
    typer.echo(f"paired cases: {c.n}; B-only right {c.b_only}, A-only right {c.a_only}")
    typer.echo(
        f"difference B-A: {c.diff_points:+.1f} points (95% Newcombe interval"
        f" {c.diff_ci[0]:+.1f} to {c.diff_ci[1]:+.1f}); McNemar p = {c.p_value:.3g}"
    )
    typer.echo(f"B non-inferior (lower bound > -{margin:g} points):"
               f" {'yes' if c.b_non_inferior(margin) else 'no'}")  # fmt: skip
    if _decision_run(ra) or _decision_run(rb):  # the decision-model experiment (SPEC §7.8, §16.5)
        ends = compare_endpoints(ra, rb)
        typer.echo(
            "confirmatory (exact McNemar, Holm over the two): "
            + "; ".join(
                f"{k.replace('_', '-')} p = {p:.3g}, Holm p = {h:.3g}" for k, (p, h) in ends.items()
            )
        )
    for name, run in (("A", ra), ("B", rb)):
        typer.echo(f"{name}  {_state_line(run)}")
        for line in _run_lines(run):
            typer.echo(f"{name}{line}")
    fields = compare_fields(ra, rb)
    if fields:
        typer.echo("per field (exact McNemar, Holm-adjusted over the fields, alpha 0.05):")
        for f in fields:
            mark = "  significant" if f.significant else ""
            typer.echo(f"  {f.field:<18} n={f.n:<4} B-only {f.b_only:<3} A-only {f.a_only:<3} "
                       f"p = {f.p_value:.3g}, Holm p = {f.p_holm:.3g}{mark}")  # fmt: skip


def _run_lines(run: ResultFile) -> list[str]:
    """One run's figures for `ecf eval compare`, each line indented after the run's letter."""
    from ecf.eval.results import field_lines  # noqa: PLC0415

    out: list[str] = []
    if recall := _recall(dict(run.summary or {})):
        out.append(f" {recall.rstrip(',')}")
    if line := _model_figures(run.summary):
        out.append(f"  model: {line}")
    out += [f"    {role}: {line}" for role, line in _role_figures(run.summary)]
    out += [f"    {line}" for line in _latency_lines(run)]
    out += [f"  {line}" for line in _claude_figures(run, len(run.cases))]
    out += [f"  {line}" for line in _jev_figures(run.summary)]
    out += [f"  {line}" for line in _decision_figures(run.summary)]
    out += [f"  {line}" for line in field_lines(run)]
    return out


def _state_line(run: ResultFile) -> str:
    """A run's completeness and faulted cases, for `ecf eval compare` (P1, P22): a faulted case
    is scored as a failure, so the counts say how much of a difference is the provider's."""
    s = cast(dict[str, Any], run.summary or {})
    if s.get("complete") is False:
        reason = str(s.get("incomplete_reason") or "stopped")
        state = f"incomplete ({reason})"
    else:
        state = "complete"
    faulted = s.get("faulted")
    if isinstance(faulted, list):
        state += f"; faulted cases {len(cast(list[Any], faulted))} (scored as failures)"
    return state


def _jev_figures(summary: dict[str, object] | None) -> list[str]:
    """A Jev run's `summary.jev` (§5.5, P25): latency per concurrency with its n, the end-to-end
    time per case, tokens and cost, retries, pauses, breaker trips, waiting and faulted cases;
    absent in other runs."""
    j = (summary or {}).get("jev")
    if not isinstance(j, dict):
        return []
    f = cast(dict[str, Any], j)
    opts = cast(dict[str, Any], f.get("options") or {})
    out = [f"Jev {f.get('pin')}: state {opts.get('state')}, preamble {opts.get('preamble')},"
           f" concurrency {opts.get('concurrency')}"]  # fmt: skip
    for c, lat in cast(dict[str, Any], f.get("latency_ms_by_concurrency") or {}).items():
        d = cast(dict[str, Any], lat)
        out.append(f"  HTTP time at concurrency {c}: {d.get('p50')} ms median, p95"
                   f" {d.get('p95')}, max {d.get('max')} (n={d.get('n')})")  # fmt: skip
    e2e = f.get("end_to_end_ms")
    if isinstance(e2e, dict):
        out.append(f"  end to end per case: {_three(cast(dict[str, Any], e2e))} ms")
    e2e_f = f.get("end_to_end_faulted_ms")
    if isinstance(e2e_f, dict) and cast(dict[str, Any], e2e_f).get("max") is not None:
        out.append(f"  end to end per faulted case: {_three(cast(dict[str, Any], e2e_f))} ms")
    cost = cast(dict[str, Any], f.get("cost") or {})
    missing = f.get("usage_missing")
    unbilled = f"; {missing} replies without usage, not counted" if missing else ""
    out.append(f"  input tokens billed {f.get('input_tokens_billed')}, about"
               f" ${cost.get('usd', 0):.4f}{unbilled}"
               f" ({cost.get('basis', 'basis unknown')})")  # fmt: skip

    def counts(key: str) -> str:
        d = cast(dict[str, Any], f.get(key) or {})
        return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"

    out.append(f"  retries: {counts('retries')}; 429s {f.get('rate_limits', 0)}; pauses:"
               f" {counts('pauses')}; breaker trips {f.get('breaker_trips', 0)}; waiting"
               f" {f.get('wait_s', 0)} s")  # fmt: skip
    out.append(f"  faulted cases: {counts('faulted_cases')}")
    return out


def _subset_ids(path: Path) -> frozenset[str]:
    """Case ids from a result file (`.json`), a labels file (`.jsonl`) or one id per line."""
    from ecf.eval.results import load_result  # noqa: PLC0415

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidInputError(f"{path}: can't read it: {exc.strerror}") from exc
    if path.suffix == ".json":
        ids = {c.id for c in load_result(path).cases}
    elif path.suffix == ".jsonl":
        ids = {str(json.loads(line)["id"]) for line in text.splitlines() if line.strip()}
    else:
        ids = {line.strip() for line in text.splitlines() if line.strip()}
    if not ids:
        raise InvalidInputError(f"{path}: no case ids")
    return frozenset(ids)


def _decision_run(r: ResultFile) -> bool:
    backend = (r.summary or {}).get("backend")
    return isinstance(backend, str) and backend != "gemma"


def _decision_figures(summary: dict[str, object] | None) -> list[str]:
    """The decision-model experiment's figures (SPEC §7.8): backend, fraud-risk under-rating,
    classifier-call latency, power, calibration. Absent in older results."""
    s = cast(dict[str, Any], summary or {})
    out: list[str] = []
    if "backend" in s:
        redact = "" if s.get("redact", True) else ", injection text not redacted"
        out.append(f"classifier {s['backend']}{redact}")
    under = s.get("fraud_under")
    u = cast(dict[str, int], under) if isinstance(under, dict) else {}
    if u.get("of"):
        out.append(f"fraud risk under-rated on {u['under']} of {u['of']} cases labelled medium"
                   " or high")  # fmt: skip
    hidden = s.get("fraud_hidden")
    if isinstance(hidden, list):  # the classifier plan's fraud figures (absent before v2.1.0)
        ids = cast(list[str], hidden)
        out.append(f"fraud-labelled cases with a hide kept: {len(ids)}"
                   + (f" ({', '.join(ids)})" if ids else ""))  # fmt: skip
    none = s.get("fraud_answered_none")
    if isinstance(none, dict) and cast(dict[str, int], none).get("of"):
        n = cast(dict[str, int], none)
        out.append(f"fraud risk answered none on {n['none']} of {n['of']} cases labelled medium"
                   " or high")  # fmt: skip
    only = s.get("fraud_under_model_only")
    if isinstance(only, dict) and cast(dict[str, int], only).get("of"):
        o = cast(dict[str, int], only)
        out.append(f"under-rated on {o['under']} of {o['of']} cases only the model routes to"
                   " fraud_guard")  # fmt: skip
    out += _genuine_figures(s)
    out += _measurement_figures(s)
    lat = s.get("classifier_latency_s")
    lt = cast(dict[str, float], lat) if isinstance(lat, dict) else {}
    if lt.get("p50") is not None:
        out.append(f"classifier call {lt['p50']:.1f} s median, {lt['p95']:.1f} s p95")
    power = s.get("power")
    if isinstance(power, dict):
        pw = cast(dict[str, bool], power)
        where = "AC" if pw.get("ac_at_start") and pw.get("ac_at_end") else "battery for part"
        out.append(f"power: {where}" + ("; paused on battery (latency doesn't count)"
                                        if pw.get("paused") else ""))  # fmt: skip
    cal = s.get("calibration")
    if isinstance(cal, dict):
        for field, e in sorted(cast(dict[str, dict[str, Any]], cal).items()):
            lo, hi = e["ece_ci95"]
            extra = (f", RPS {e['rps']:.3f}" if "rps" in e else
                     f", Brier {e['brier']:.3f} (baseline {e['brier_baseline']:.3f})"
                     if "brier" in e else "")  # fmt: skip
            out.append(f"calibration {field}: ECE {e['ece']:.3f} ({lo:.3f}-{hi:.3f}; baseline"
                       f" {e['ece_baseline']:.3f}), n={e['n']}{extra}")  # fmt: skip
    return out


def _model_figures(summary: dict[str, object] | None) -> str | None:
    """A result's tokens and speeds (stats.py's figures; absent in results before V1.3 step 9)."""
    m = (summary or {}).get("model")
    if not isinstance(m, dict):
        return None
    f = cast(dict[str, Any], m)
    w, t = f["generation_tps"], f["seconds"]
    per_email = "-" if f["tokens_per_email"] is None else f"{f['tokens_per_email']:.0f}"
    return (f"{f['calls']} calls, about {per_email} tokens per email; writing"
            f" {w['median']} tokens/s median (slowest 5% {w['slowest_5']}); {t['median']} s per"
            f" call median, {t['p95']} s p95")  # fmt: skip


def _role_figures(summary: dict[str, object] | None) -> list[tuple[str, str]]:
    """The same figures per role (classifier, actor, determinism repeats; absent before v2.1.0)."""
    roles = (summary or {}).get("roles")
    if not isinstance(roles, dict):
        return []
    out: list[tuple[str, str]] = []
    for role, figures in cast(dict[str, Any], roles).items():
        if line := _model_figures({"model": figures}):
            out.append((role, line))
    return out


def _latency_lines(run: ResultFile) -> list[str]:
    """Where each role's time goes, and the classifier calls whose prompt cache fell short
    (pre-classifier plan §11; absent in results before plan 0.3 and in Claude runs)."""
    from ecf.eval.results import CACHE_SHORT, cache_short, latency_split  # noqa: PLC0415

    out: list[str] = []
    for s in latency_split(run):
        out.append(
            f"{s.role} time: reading the prompt {s.prompt_s[0]:.2f} s median"
            f" (p95 {s.prompt_s[1]:.2f}), writing {s.generation_s[0]:.2f} s (p95"
            f" {s.generation_s[1]:.2f}), writing {100 * s.generation_share:.0f}% of the total;"
            f" uncached input {s.uncached_tokens[0]} tokens median (p95 {s.uncached_tokens[1]},"
            f" max {s.uncached_tokens[2]}), output {s.output_tokens[0]} (p95 {s.output_tokens[1]})"
        )
    for c in cache_short(run):
        took = "" if c.prompt_s is None else f", prompt {c.prompt_s:.2f} s median"
        out.append(f"classifier cache after {c.after}: {c.short} of {c.calls} calls reused under"
                   f" {100 * CACHE_SHORT:.0f}% of the prefix{took}")  # fmt: skip
    return out


def _claude_figures(run: ResultFile, cases: int) -> list[str]:
    """A Claude eval's models, batch, requests, seconds per request and plan usage (V1.4 step 7)
    and its spawn figures (v2.2.0); absent in other runs. Read with `.get`: a result may lack any
    of them."""
    from ecf.eval.results import claude_block  # noqa: PLC0415

    f = claude_block(run)
    if f is None:  # a Claude pair without figures (plan item 6): its batch can't be told
        return ["Claude: batch unknown (no Claude figures)"] if run.pair.endswith("/claude") else []
    m = f.get("models")
    models = (", ".join(f"{k} {v}" for k, v in cast(dict[str, Any], m).items())
              if isinstance(m, dict) else "models unknown")  # fmt: skip
    kind = ("pinned" if f.get("pinned") else
            PRESET_D_ONLY if f.get("preset") == "D" else "comparison")  # fmt: skip
    batch = f.get("batch", "unknown")
    out = [f"Claude: preset {f.get('preset', '?')}, {f.get('sensitivity', '?')}, {kind}"
           f" ({models}), batch {batch}" + (f", classifications from run"
                                            f" {str(f['source_run'])[:8]}"
                                            if f.get("source_run") else "")]  # fmt: skip
    a = f.get("all")
    if isinstance(a, dict):
        al = cast(dict[str, Any], a)
        tokens = al["input_tokens"] + al["output_tokens"]
        per = f", about {tokens / cases:.0f} per case" if cases else ""
        out.append(f"Claude: {al['calls']} requests, {al['input_tokens']} tokens in and"
                   f" {al['output_tokens']} out{per}; {al['seconds']['median']} s per request"
                   " median")  # fmt: skip
    groups = f.get("groups")
    for g in cast(list[dict[str, Any]], groups) if isinstance(groups, list) else []:
        tokens = g["input_tokens"] + g["output_tokens"]
        out.append(f"  {g['model']} ({g['source']}): {g['calls']} requests, {tokens} tokens"
                   + _per_request(g))  # fmt: skip
    p = f.get("plan")
    if isinstance(p, dict):
        plan = cast(dict[str, Any], p)
        out.append("plan usage: " + ", ".join(
            f"{label} {plan[f'{k}_start']}% -> {plan[f'{k}_end']}%"
            for k, label in (("five_hour", "5-hour"), ("seven_day", "7-day"))
            if plan.get(f"{k}_end") is not None))  # fmt: skip
    return out + _spawn_lines(f.get("spawns"))


def _per_request(g: dict[str, Any]) -> str:
    """A request group's seconds per request and cache shares of its input (a load control
    between two runs, timing plan §5); empty in results that lack them."""
    out = ""
    secs = g.get("seconds")
    if isinstance(secs, dict) and cast(dict[str, Any], secs).get("median") is not None:
        sd = cast(dict[str, Any], secs)
        out += f"; {sd['median']} s per request median, {sd.get('p95')} s p95"
    read, made = g.get("cache_read_tokens"), g.get("cache_creation_tokens")
    if isinstance(read, int) and isinstance(made, int):
        total = read + made + int(g.get("input_tokens") or 0)
        if total:
            out += f"; cache read {100 * read / total:.0f}%, written {100 * made / total:.0f}%"
    return out


def _spawn_lines(spawns: object) -> list[str]:
    """`summary.claude.spawns` (v2.2.0 timing plan item 4): service-side seconds per spawn, round
    and session; read -> answer per item and tokens per case decide, round and run wall time
    depend on how many spawns Claude Code runs at once (plan 6a)."""
    if not isinstance(spawns, dict):
        return ["Claude spawns: no spawn figures"]
    sp = cast(dict[str, Any], spawns)

    def spread(d: object) -> str:
        v = cast(dict[str, Any], d) if isinstance(d, dict) else {}
        return "-" if v.get("p50") is None else f"{v['p50']} s median (p95 {v['p95']})"

    gap = cast(dict[str, Any], sp.get("largest_gap") or {})
    run_s = sp.get("run_s")
    out = [f"Claude spawns: run {'-' if run_s is None else f'{run_s} s'} from first claim to last"
           f" answer; largest gap {gap.get('s', '-')} s at {gap.get('at_s', '-')} s"]  # fmt: skip
    for agent, a in cast(dict[str, Any], sp.get("agents") or {}).items():
        f = cast(dict[str, Any], a)
        items = cast(dict[str, Any], f.get("items_per_spawn") or {})
        out.append(f"  {agent}: {f.get('spawns')} spawns, {items.get('p50')} items median (max"
                   f" {items.get('max')}); read -> answer {spread(f.get('read_to_answer_s'))};"
                   f" claim -> read {spread(f.get('claim_to_read_s'))}; spawn"
                   f" {spread(f.get('spawn_s'))}; settle lag"
                   f" {spread(f.get('settle_lag_s'))}")  # fmt: skip
    for kind, r in cast(dict[str, Any], sp.get("rounds") or {}).items():
        rd = cast(dict[str, Any], r)
        if rd.get("rounds"):
            label = "while preparing" if kind == "preparing" else "ready"
            out.append(f"  rounds {label}: {rd['rounds']}, {spread(rd.get('duration_s'))}; gap"
                       f" to the next {spread(rd.get('gap_s'))}")  # fmt: skip
    for session, t in cast(dict[str, Any], sp.get("sessions") or {}).items():
        td = cast(dict[str, Any], t)
        out.append(f"  session {session}: {td.get('rounds')} rounds, {td.get('in_rounds_s')} s"
                   f" in rounds, {td.get('between_rounds_s')} s between")  # fmt: skip
    act = cast(dict[str, Any], sp.get("act") or {})
    if act.get("claims"):
        n, high = act["claims"], act.get("high_risk_actor", 0)
        why = cast(dict[str, Any], act.get("reasons") or {})
        reasons = ", ".join(f"{k} {v}" for k, v in why.items())
        out.append(f"  act claims: {n}, {high} to the high-risk actor ({100 * high / n:.0f}%)"
                   + (f"; reasons {reasons}" if reasons else ""))  # fmt: skip
    return out


def main() -> None:
    configure_logging("cli", level=logging.WARNING)
    try:
        app()
    except EcfError as exc:
        sys.stderr.write(f"ecf: {exc.detail}\n")
        raise SystemExit(int(exc.exit_code)) from exc


make_init_commands(app, _paths, add_address)  # after add_address, which `ecf init` reuses
