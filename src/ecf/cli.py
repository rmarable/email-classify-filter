"""The `ecf` command-line interface."""

import typer

from ecf import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="email-classify-filter")


@app.callback()
def root() -> None:
    """email-classify-filter: watch mailboxes, flag fraud, approve actions in Slack."""


@app.command()
def version() -> None:
    """Print the ecf version."""
    typer.echo(__version__)


def main() -> None:
    app()
