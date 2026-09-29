"""The `ecf` command-line interface."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf import __version__
from ecf.client import LocalClient
from ecf.doctor import Level, run_checks
from ecf.errors import EcfError
from ecf.ids import SLUG_PATTERN
from ecf.log import configure_logging
from ecf.paths import Paths, paths_for
from ecf.prompts import hidden, require_terminal
from ecf.service_unit import manager_for

app = typer.Typer(no_args_is_help=True, add_completion=False, help="email-classify-filter")
service_app = typer.Typer(no_args_is_help=True, help="Install and control the background service.")
app.add_typer(service_app, name="service")
eval_app = typer.Typer(no_args_is_help=True, help="Synthetic eval set and results.")
app.add_typer(eval_app, name="eval")
address_app = typer.Typer(no_args_is_help=True, help="Monitored mailboxes.")
app.add_typer(address_app, name="address")
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


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop the service and remove its unit. Data is kept (use `ecf destroy` to remove it)."""
    m = manager_for(_paths())
    m.uninstall()
    typer.echo(f"removed {m.unit_path}")


@service_app.command("start")
def service_start() -> None:
    """Start the service (also clears the crash-loop breaker)."""
    manager_for(_paths()).start()
    typer.echo("started")


@service_app.command("stop")
def service_stop() -> None:
    """Stop the service until the next login."""
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
    paths = _paths()
    with LocalClient(paths) as c:
        current = c.get("/v1/addresses")
        if sensitivity is None:
            suggestion = _suggest_sensitivity(email)
            sensitivity = typer.prompt(
                "Sensitivity (standard, or high for finance mailboxes)", default=suggestion
            )
        if preset is None:
            preset = typer.prompt(
                "Preset (A all-local, B local + Claude, C all-Claude)", default="A"
            )
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
                "(changing them later is `ecf config apply`, V1.2)."
            )
            answer = typer.prompt("Organization domains, comma-separated", default=domain)
            body["org_domains"] = [d.strip() for d in answer.split(",") if d.strip()]
        body["app_password"] = hidden(f"App password for {email} (hidden): ")
        a = c.request("POST", "/v1/addresses", body)
    typer.echo(
        f"added {a['email']} as {a['address_id']!r}: {a['sensitivity']}, preset "
        f"{a['preset']}, stage {a['stage']}, outbound off"
    )
    _echo_probe(a)


def _echo_probe(a: dict[str, Any]) -> None:
    p = a.get("probe")
    if not p:
        return
    size = p["max_message_bytes"]
    typer.echo(
        f"probe: folders {', '.join(sorted(p['roles'].values())) or 'none marked'}; "
        f"keywords {'yes' if p['custom_keywords'] else 'no'}; "
        f"size limit {f'{size:,} bytes' if size else 'unknown'}"
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


@address_app.command("remove")
def address_remove(address: Annotated[str, typer.Argument(help="Address id or email.")]) -> None:
    """Stop watching a mailbox and delete its app password from the secret store."""
    with LocalClient(_paths()) as c:
        target = next(
            (a for a in c.get("/v1/addresses")["addresses"]
             if address in (a["address_id"], a["email"].lower(), a["email"])),
            None,
        )  # fmt: skip
        if target is None:
            raise typer.BadParameter(f"no address {address!r}")
        typed = typer.prompt(f"Type {target['email']} to remove it")
        if typed.strip().lower() != target["email"].lower():
            typer.echo("not removed")
            raise typer.Exit(1)
        a = c.request("DELETE", f"/v1/addresses/{target['address_id']}")
    typer.echo(f"removed {a['email']}. Left for you to do:")
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


@eval_app.command("compare")
def eval_compare(a: Path, b: Path) -> None:
    """Compare two result files (paired, exact McNemar; non-inferiority at -3 points)."""
    from ecf.eval.results import compare, load_result, summary  # noqa: PLC0415

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


def main() -> None:
    configure_logging("cli", level=logging.WARNING)
    try:
        app()
    except EcfError as exc:
        sys.stderr.write(f"ecf: {exc.detail}\n")
        raise SystemExit(int(exc.exit_code)) from exc
