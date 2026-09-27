"""The `ecf` command-line interface."""

from __future__ import annotations

import re
import sys
from typing import Annotated

import typer

from ecf import __version__
from ecf.errors import EcfError
from ecf.ids import SLUG_PATTERN
from ecf.paths import Paths, paths_for
from ecf.service_unit import manager_for

app = typer.Typer(no_args_is_help=True, add_completion=False, help="email-classify-filter")
service_app = typer.Typer(no_args_is_help=True, help="Install and control the background service.")
app.add_typer(service_app, name="service")


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


def main() -> None:
    try:
        app()
    except EcfError as exc:
        sys.stderr.write(f"ecf: {exc.detail}\n")
        raise SystemExit(int(exc.exit_code)) from exc
