"""`ecf init --mode local [--resume]` and `ecf init status` (SPEC §13.1; V1.2 step 11c).

Every step is skipped when the service reports it done, so running `init` again resumes. Every
setup step goes through the service (tokens, app passwords and org domains are stored by it), so
the service unit is installed first when it isn't running: an unconfigured service idles
(§10a). V1.2 steps: checklist, service, disk-encryption and secret-store checks, install role,
Slack, first address (with org domains), then a final check that the unit is running. The model
step arrives in V1.3; the email-alerts and export steps in V1.5 (OD-206).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any

import typer

from ecf import doctor
from ecf.client import LocalClient
from ecf.errors import EcfError
from ecf.paths import Paths
from ecf.prompts import require_terminal
from ecf.service_unit import ServiceManager, manager_for

START_WAIT_S = 30.0
CHECKLIST = """Have ready:
  - a Slack workspace where you can click "Install to Workspace", and a Slack configuration
    token (api.slack.com/apps, Your App Configuration Tokens; it expires after 12 hours)
  - your Slack member ID (your profile, the ... menu, Copy member ID)
  - each mailbox's IMAP server and an app password for it
  - your organization's domains, and whether each mailbox is standard or high (finance)
  - whether this install is prod (your real mail) or test
  - Ollama (presets A and B) arrives in V1.3; Claude Code (B and C) in V1.4"""

AddAddress = Callable[[LocalClient, str, str, str | None, str | None, str | None], Any]


def make_commands(app: typer.Typer, paths: Callable[[], Paths], add: AddAddress) -> None:
    init_app = typer.Typer(
        invoke_without_command=True, help="Set up ecf on this computer (step by step)."
    )
    app.add_typer(init_app, name="init")

    @init_app.callback()
    def init(
        ctx: typer.Context,
        mode: Annotated[
            str, typer.Option("--mode", help="local (AWS mode arrives in M1).")
        ] = "local",
        resume: Annotated[
            bool, typer.Option("--resume", help="Continue without the checklist.")
        ] = False,
    ) -> None:
        """Set up ecf: service, Slack, first mailbox. Safe to run again; done steps are skipped."""
        if ctx.invoked_subcommand is not None:
            return
        if mode != "local":
            raise typer.BadParameter(
                "use --mode local (AWS mode arrives in M1)", param_hint="--mode"
            )
        require_terminal()
        p = paths()
        if not resume:
            typer.echo(CHECKLIST)
            if not typer.confirm("Ready?", default=True):
                raise typer.Exit(1)
        manager = manager_for(p)
        _service(p, manager)
        with LocalClient(p) as c:
            _checks(c)
            st: dict[str, Any] = c.get("/v1/init")
            _role(c, st)
            _slack(c, st)
            _first_address(c, add)
        _unit(p, manager)
        typer.echo("Done for now. `ecf init status` shows each step; `ecf doctor` checks it all.")

    @init_app.command("status")
    def init_status() -> None:
        """Which setup steps are done."""
        p = paths()
        unit = manager_for(p).status()
        try:
            with LocalClient(p) as c:
                st = c.get("/v1/init")
        except EcfError as exc:
            typer.echo(f"service       not answering ({exc.detail}): ecf init --mode local")
            raise typer.Exit(3) from None
        for line in describe(st, installed=unit.installed, running=unit.running):
            typer.echo(line)


def describe(st: dict[str, Any], *, installed: bool, running: bool) -> list[str]:
    def row(name: str, done: bool, text: str) -> str:
        return f"{name:<14}{'done' if done else 'to do':<7}{text}"

    role = st["install_role"]
    return [
        row("service", installed and running,
            "installed and running" if installed and running else "ecf init --mode local"),
        row("install role", role is not None, role or "asked by ecf init"),
        row("slack", st["slack_installed"] and st["slack_member"] is not None,
            f"member {st['slack_member']}" if st["slack_member"]
            else "ecf slack install" if not st["slack_installed"]
            else "click Confirm in the DM ecf sent you"),
        row("org domains", bool(st["org_domains"]),
            ", ".join(st["org_domains"]) or "set with the first address"),
        row("first address", bool(st["addresses"]),
            ", ".join(st["addresses"]) or "ecf address add"),
        row("models", False, "arrives in V1.3"),
        row("export", False, "arrives in V1.5"),
    ]  # fmt: skip


def _service(p: Paths, manager: ServiceManager) -> None:
    if _answering(p):
        typer.echo("service: running")
        return
    typer.echo("service: starting it (it idles until set up)")
    if manager.status().installed:
        manager.start()  # also clears a tripped crash-loop breaker
    else:
        manager.install()
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        if _answering(p):
            return
        time.sleep(0.5)
    typer.echo("The service didn't answer within 30 s. See `ecf service status` and `ecf logs`.")
    raise typer.Exit(3)


def _answering(p: Paths) -> bool:
    try:
        with LocalClient(p) as c:
            c.get("/v1/health", auth=False)
    except EcfError:
        return False
    return True


def _checks(c: LocalClient) -> None:
    disk = doctor.check_disk_encryption()
    typer.echo(f"disk encryption: {disk.detail}")
    if disk.level is doctor.Level.FAIL:
        typer.echo(f"  {disk.fix}. Full-disk encryption is required: your mailbox passwords and"
                   " mail metadata are on this computer.")  # fmt: skip
        if not typer.confirm("Continue anyway?", default=False):
            raise typer.Exit(1)
    backend, detail = secret_store(c)
    if backend is None:
        typer.echo(f"secret store: unavailable ({detail}); see `ecf doctor`")
        raise typer.Exit(3)
    typer.echo(f"secret store: {backend}")


def secret_store(c: LocalClient) -> tuple[str | None, str]:
    """The service's secret-store backend, or None and why not."""
    store: dict[str, Any] = c.get("/v1/status")["secret_store"]
    backend = store.get("backend")
    return (str(backend) if backend else None), str(store.get("detail") or "unknown")


def _role(c: LocalClient, st: dict[str, Any]) -> None:
    if st["install_role"]:
        return
    role = typer.prompt("Is this install prod (your real mail) or test?", default="prod")
    c.request("POST", "/v1/init/role", {"install_role": role.strip().lower()})
    typer.echo(f"install role: {role.strip().lower()} (fixed from now on)")


def _slack(c: LocalClient, st: dict[str, Any]) -> None:
    from ecf.cli_slack import install_slack  # noqa: PLC0415 - only init needs it here

    if st["slack_installed"]:
        typer.echo("slack: installed" + ("" if st["slack_member"] else
                                         " (click Confirm in the DM ecf sent you)"))  # fmt: skip
        return
    if not typer.confirm("Connect Slack now? (needs the configuration token)", default=True):
        typer.echo("slack: skipped; later: ecf slack install")
        return
    install_slack(c)


def _first_address(c: LocalClient, add: AddAddress) -> None:
    if c.get("/v1/init")["addresses"]:
        typer.echo("first address: added")
        return
    if not typer.confirm("Add the first mailbox now?", default=True):
        typer.echo("first address: skipped; later: ecf address add")
        return
    email = typer.prompt("Mailbox address (e.g. ap@example.com)").strip()
    host = typer.prompt("Its IMAP server (port 993, TLS)").strip()
    add(c, email, host, None, None, None)


def _unit(p: Paths, manager: ServiceManager) -> None:
    s = manager.status()
    if s.installed and s.running:
        typer.echo("service unit: installed and running")
        return
    if _answering(p):  # e.g. `ecf watch`: a second copy would only fail on the instance lock
        typer.echo("service unit: the service is running outside its unit (ecf watch?); run"
                   " `ecf service install` once it stops")  # fmt: skip
        return
    manager.install()
    typer.echo("service unit: installed")
