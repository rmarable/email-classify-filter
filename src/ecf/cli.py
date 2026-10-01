"""The `ecf` command-line interface."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlencode

import typer

from ecf import __version__, watch
from ecf.cli_admin import make_commands as make_admin_commands
from ecf.cli_init import make_commands as make_init_commands
from ecf.cli_items import make_commands as make_item_commands
from ecf.cli_models import make_models_app
from ecf.cli_slack import make_app as make_slack_app
from ecf.cli_stats import make_stats_command
from ecf.client import LocalClient
from ecf.doctor import Level, run_checks
from ecf.errors import EcfError
from ecf.ids import SLUG_PATTERN
from ecf.log import configure_logging
from ecf.paths import Paths, paths_for
from ecf.prompts import hidden, require_terminal
from ecf.service_unit import manager_for
from ecf.status import CHECK_FAILED
from ecf.stepup import step_up, with_step_up

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
app.add_typer(make_models_app(_paths), name="models")
make_stats_command(app, _paths)
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


@alerts_app.command("set")
def alerts_set(
    to: Annotated[str, typer.Option("--to", help="slack (email arrives in V1.5).")],
    cls: Annotated[
        str | None, typer.Argument(help="mail, system or operator (all when left out).")
    ] = None,
) -> None:
    """Change where alerts go. (step-up)"""
    body: dict[str, Any] = {"class": cls, "to": [t for t in to.split(",") if t.strip()]}
    with LocalClient(_paths()) as c:
        with_step_up(c, lambda n: c.request("POST", "/v1/alerts", body | {"nonce_id": n}),
                     echo=typer.echo)  # fmt: skip
    typer.echo("changed; a Security Notice says so in Slack")


@alerts_app.command("test")
def alerts_test() -> None:
    """Send a test alert on every route."""
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/alerts/test")
    where = [("slack (queued: it posts within a minute)" if w == "slack" else w)
             for w in r["sent"]]  # fmt: skip
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
        + (" (Python changed: re-grant needed)" if ss.get("interpreter_changed") else "")
    )
    sl = st.get("slack", {})
    if not sl.get("installed"):
        typer.echo("slack:     not installed (`ecf slack install`)")
    else:
        state = "connected" if sl.get("connected") else "NOT connected"
        typer.echo(f"slack:     {state}; last connected {sl.get('last_connected_at') or 'never'}")
    m = st.get("model")
    if m:
        typer.echo(f"model:     {_model_backlog(m)}")
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
    until_empty: Annotated[
        bool, typer.Option("--until-empty", help="Keep checking while mail is waiting.")
    ] = False,
) -> None:
    """Check mail now: fetch, verify senders, run the fraud and regulator checks, then the local
    model on what waits for it. Exit 3 when a check failed or the local model isn't ready."""
    body: dict[str, object] = {"until_empty": until_empty}
    if address:
        body["address_id"] = address
    failed = False
    with LocalClient(_paths()) as c:
        for r in c.stream("POST", "/v1/checks", body):
            if r.get("done"):
                break
            if "model" in r:
                m = r["model"]
                failed |= m["status"] == "not_ready"
                typer.echo(_model_line(m))
                continue
            failed |= r["status"] in CHECK_FAILED
            typer.echo(_check_line(r))
    if failed:
        raise typer.Exit(3)


def _model_backlog(m: dict[str, Any]) -> str:
    if not m["waiting"]:
        return "nothing waiting"
    text = f"{m['waiting']} waiting"
    if m.get("eta_s") is not None:
        text += f", about {max(1, round(m['eta_s'] / 60))} min at the measured speed"
    if m.get("eval"):
        text += "; an eval holds the model"
    elif m.get("on_battery"):
        text += "; on battery it runs at the off-hours interval (plug in to catch up)"
    return text


def _model_line(m: dict[str, Any]) -> str:
    who = f"{'local model':<16}"
    if m["status"] == "off":
        return f"{who} {m['detail']}"
    if m["status"] == "not_ready":
        return f"{who} not ready: {m['detail']}"
    if m["status"] == "eval":
        return f"{who} an eval holds the model; {m['waiting']} waiting"
    if m["status"] == "worker":
        return f"{who} the service's own run is working on them; {m['waiting']} still waiting"
    if m["status"] == "gave_up":
        return (f"{who} stopped waiting after {m['minutes']} min; {m['waiting']} still waiting"
                " (ecf status)")  # fmt: skip
    extra = {"budget": " (6-minute budget used)", "hot": " (paused: running hot)",
             "stopped": " (service stopping)"}.get(m["status"], "")  # fmt: skip
    return (f"{who} {m['done']} done, {m['failed']} failed, {m['waiting']} still waiting"
            f"{extra}")  # fmt: skip


def _check_line(r: dict[str, Any]) -> str:
    who = f"{r['address_id']:<16}"
    if r["status"] == "first_run":
        return f"{who} first check: started from now (older mail: `ecf backfill`)"
    if r["status"] == "busy":
        return f"{who} skipped: another check holds this address"
    if r["status"] == "waiting":
        return f"{who} waiting for the scheduled check that holds this address"

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
        str | None, typer.Option("--since", help="Read mail that arrived since this date.")
    ] = None,
    act: Annotated[
        bool, typer.Option("--act", help="Label, flag and escalate as for new mail.")
    ] = False,
    stop: Annotated[bool, typer.Option("--stop", help="End a running backfill.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask before --act.")] = False,
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
            raise typer.BadParameter("add --since <date>, e.g. --since 2026-09-01")
        if act and not yes:
            stage = next((s["stage"] for s in c.get("/v1/stages")["addresses"]
                          if address in (s["address_id"], s.get("email"))), "?")  # fmt: skip
            typer.echo(f"--act runs the pre-check on every email since {since} as on new mail;"
                       f" {address} is in {stage} (shadow does nothing, assist labels and flags),"
                       " and escalations post to Slack.")  # fmt: skip
            if not typer.confirm("Go ahead?", default=False):
                raise typer.Exit(1)
        r = c.request("POST", "/v1/backfill", {"address_id": address, "since": since, "act": act})
    how = "labels, flags and escalations as for new mail" if r["act"] else (
        "records only: nothing is done to the mailbox or escalated")  # fmt: skip
    typer.echo(f"backfill of {r['address_id']} since {r['since']} started ({how}); it runs with"
               " the checks, new mail first. Progress: ecf backfill; to end it:"
               f" ecf backfill {r['address_id']} --stop")  # fmt: skip


def _backfill_status(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        how = "acting" if r["act"] else "records only"
        fraud = (f", {r['fraud']} with a fraud signal (ecf logs --address {r['address_id']}"
                 " --event precheck)") if r.get("fraud") else ""  # fmt: skip
        when = "" if r["outcome"] == "running" else f" at {r['at'][:16]}"
        typer.echo(f"{r['address_id']:<16} since {r['since']} ({how}): {r['outcome']}{when},"
                   f" {r['items']} read{fraud}")  # fmt: skip
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
    if since:
        params["since"] = _since(since)
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


def _since(value: str) -> str:
    """An age like 30m, 2h or 3d becomes a UTC timestamp; anything else is passed through."""
    m = re.fullmatch(r"(\d+)([mhd])", value.strip())
    if not m:
        return value
    unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
    when = datetime.now(UTC) - timedelta(**{unit: int(m.group(1))})
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _log_line(e: dict[str, Any]) -> str:
    where = e.get("address_id") or "-"
    data = json.dumps(e["data"], sort_keys=True) if e["data"] else ""
    flag = "" if e["outcome"] == "ok" else f" [{e['outcome']}]"
    return f"{e['ts'][:19]}  {where:<12} {e['event']}{flag}  {data}".rstrip()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def claude(ctx: typer.Context) -> None:
    """Open Claude Code in ecf's own configuration (run /ecf-review there; tools arrive in V1.4).

    Needs its own Claude login (it uses a separate configuration folder). Extra arguments are
    passed to `claude`.
    """
    from ecf import claude_wrapper  # noqa: PLC0415

    typer.echo("ecf claude: review tools arrive in V1.4; this opens the configured session only.")
    raise typer.Exit(claude_wrapper.run(_paths(), list(ctx.args)))


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


@app.command("replay")
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
    imap_host: Annotated[str, typer.Option("--imap-host", help="IMAP server (port 993, TLS).")],
    sensitivity: Annotated[
        str | None, typer.Option(help="standard, or high for finance mailboxes (extra checks).")
    ] = None,
    preset: Annotated[
        str | None, typer.Option(help="A all-local, B local + Claude actor, C all-Claude.")
    ] = None,
    address_id: Annotated[
        str | None, typer.Option("--id", help="Short name (default: from the local part).")
    ] = None,
) -> None:
    """Add a mailbox: checks the app password by logging in, then stores it in the OS secret
    store. It starts in shadow (watch only) with outbound off. Needs a real terminal."""
    require_terminal()
    with LocalClient(_paths()) as c:
        add_address(c, email, imap_host, sensitivity, preset, address_id)


PRESET_NOTES = {
    "B": "Preset B: Claude acts only when you run /ecf-review (V1.4). The local fallback for items"
    " waiting on Claude (claude_queue_timeout) is off.",
    "C": "Preset C: every message waits for /ecf-review (V1.4) and uses your Claude plan. The"
    " local fallback (claude_queue_timeout) is off.",
}


def add_address(
    c: LocalClient,
    email: str,
    imap_host: str,
    sensitivity: str | None,
    preset: str | None,
    address_id: str | None,
) -> dict[str, Any]:
    """The prompts and request behind `ecf address add` (also used by `ecf init`)."""
    current = c.get("/v1/addresses")
    if sensitivity is None:
        suggestion = _suggest_sensitivity(email)
        sensitivity = typer.prompt(
            "Sensitivity (standard, or high for finance mailboxes)", default=suggestion
        )
    if preset is None:
        preset = typer.prompt("Preset (A all-local, B local + Claude, C all-Claude)", default="A")
    body: dict[str, object] = {
        "email": email,
        "imap_host": imap_host,
        "sensitivity": sensitivity,
        "preset": (preset or "").upper(),
    }
    if address_id:
        body["address_id"] = address_id
    if not current["org_domains"]:
        domain = email.rsplit("@", 1)[-1].lower()
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
    for w in p["warnings"]:
        typer.echo(f"  note: {w}")


@address_app.command("list")
def address_list() -> None:
    """List monitored mailboxes."""
    with LocalClient(_paths()) as c:
        data = c.get("/v1/addresses")
    typer.echo(f"org domains: {', '.join(data['org_domains']) or 'not set'}")
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
) -> None:
    """Change a mailbox's settings. Now: `--app-password` (re-enter or rotate)."""
    if not app_password:
        raise typer.BadParameter("nothing to set; use --app-password")
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
    template: Annotated[str, typer.Option("--template", help="bec, injection, header or control.")],
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


@eval_app.command("label")
def eval_label(
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
) -> None:
    """Confirm each case's expected labels (only you; OD-229, OD-241). A confirmed case counts
    toward the gates; editing its card undoes the confirmation. Cases whose card has a `review`
    note are flagged: the note says what to judge."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.cards import load_cards  # noqa: PLC0415
    from ecf.prompts import require_terminal  # noqa: PLC0415

    n = labels.counts(root)
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
def eval_run(
    root: RootOpt = EVAL_ROOT,
    classifier: Annotated[bool, typer.Option("--classifier/--no-classifier")] = True,
    actor: Annotated[bool, typer.Option("--actor/--no-actor")] = True,
    fraud_only: Annotated[
        bool, typer.Option("--fraud-only", help="Only fraud, injection and escalation cases.")
    ] = False,
    battery_floor: Annotated[
        int, typer.Option("--battery-floor", help="Pause at this battery percent (OD-237).")
    ] = 15,
) -> None:
    """Run the synthetic set through the local model (holds the model; fraud checks go on). A
    full run takes hours on a laptop: run it overnight on AC power (OD-230)."""
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/eval/runs", {
            "root": str(root.resolve()), "classifier": classifier, "actor": actor,
            "fraud_only": fraud_only, "battery_floor": battery_floor})  # fmt: skip
    typer.echo(f"eval {r['run_id'][:8]} started: {r['total']} cases; follow it with"
               " `ecf eval status`, stop it with `ecf eval stop`")  # fmt: skip
    if r.get("on_battery"):
        typer.echo(f"On battery ({r.get('battery')}%). The eval pauses at {battery_floor}% and"
                   " resumes on AC power.")  # fmt: skip


@eval_app.command("status")
def eval_status() -> None:
    """The running eval's progress and the latest results."""
    with LocalClient(_paths()) as c:
        st = c.get("/v1/eval/runs")
    cur = st["current"]
    if cur["state"] != "idle":
        typer.echo(f"eval {cur['run_id'][:8]}: {cur['state']}, {cur['done']}/{cur['total']}"
                   + (f" ({cur['detail']})" if cur["detail"] else ""))  # fmt: skip
    for r in st["recent"]:
        m = r["metrics"]
        typer.echo(f"{r['created_at'][:16]} {r['run_id'][:8]}: {m.get('correct')}/"
                   f"{m.get('confirmed')} confirmed cases correct ({m.get('accuracy')}%, Wilson"
                   f" {m.get('wilson95')}), unsafe {len(m.get('unsafe', []))},"
                   f" gate {'passed' if r['gate_passed'] else 'NOT passed'}")  # fmt: skip
    if cur["state"] == "idle" and not st["recent"]:
        typer.echo("no eval has run yet: ecf eval run")


@eval_app.command("stop")
def eval_stop() -> None:
    """Stop the running eval after its current case."""
    with LocalClient(_paths()) as c:
        r = c.request("POST", "/v1/eval/runs/stop", {})
    typer.echo(f"stopping eval {r['run_id'][:8]} after its current case")


@eval_app.command("compare")
def eval_compare(a: Path, b: Path) -> None:
    """Compare two result files (paired, exact McNemar; non-inferiority at -3 points; per field
    with Holm)."""
    from ecf.eval.results import compare, compare_fields, load_result, summary  # noqa: PLC0415

    ra, rb = load_result(a), load_result(b)
    c = compare(ra, rb)
    typer.echo(f"A  {summary(ra)}")
    typer.echo(f"B  {summary(rb)}")
    typer.echo(f"paired cases: {c.n}; B-only right {c.b_only}, A-only right {c.a_only}")
    typer.echo(
        f"difference B-A: {c.diff_points:+.1f} points "
        f"(95% CI {c.diff_ci[0]:+.1f} to {c.diff_ci[1]:+.1f}); McNemar p = {c.p_value:.3g}"
    )
    typer.echo(f"B non-inferior (lower bound > -3 points): {'yes' if c.b_non_inferior else 'no'}")
    for name, run in (("A", ra), ("B", rb)):
        line = _model_figures(run.summary)
        if line:
            typer.echo(f"{name}  model: {line}")
    fields = compare_fields(ra, rb)
    if fields:
        typer.echo("per field (exact McNemar, Holm-adjusted over the fields, alpha 0.05):")
        for f in fields:
            mark = "  significant" if f.significant else ""
            typer.echo(f"  {f.field:<18} n={f.n:<4} B-only {f.b_only:<3} A-only {f.a_only:<3} "
                       f"p = {f.p_value:.3g}, Holm p = {f.p_holm:.3g}{mark}")  # fmt: skip


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


def main() -> None:
    configure_logging("cli", level=logging.WARNING)
    try:
        app()
    except EcfError as exc:
        sys.stderr.write(f"ecf: {exc.detail}\n")
        raise SystemExit(int(exc.exit_code)) from exc


make_init_commands(app, _paths, add_address)  # after add_address, which `ecf init` reuses
