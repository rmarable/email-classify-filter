"""`ecf outbound enable|disable` (SPEC §8.4, §9.8; OD-323; V1.5 step 3b)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.stepup import with_step_up

ADDRESS = Annotated[str, typer.Argument(help="Address id or email.")]


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    out_app = typer.Typer(
        no_args_is_help=True, help="Sending replies and forwards (off by default)."
    )
    app.add_typer(out_app, name="outbound")

    @out_app.command("enable")
    def outbound_enable(address: ADDRESS) -> None:
        """Let this address send approved template replies and internal forwards. A high address
        first needs 20 reviewed suppressed sends, 95% of them correct. (step-up)"""
        path = f"/v1/addresses/{address}/outbound"
        with LocalClient(paths()) as c:
            r = with_step_up(c, lambda n: c.request("POST", path, {"value": "on", "nonce_id": n}),
                             echo=typer.echo)  # fmt: skip
        typer.echo(f"{r['email']}: outbound on (every send still needs your approval and step-up)")

    @out_app.command("disable")
    def outbound_disable(address: ADDRESS) -> None:
        """Stop sending from this address at once; sends already approved are stopped too."""
        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/addresses/{address}/outbound", {"value": "off"})
        typer.echo(f"{r['email']}: outbound off")
        if r["stopped"]:
            typer.echo(f"stopped {len(r['stopped'])} approved send(s): {', '.join(r['stopped'])}")
