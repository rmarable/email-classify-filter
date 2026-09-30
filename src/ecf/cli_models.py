"""`ecf models status|install` (SPEC §7.5; V1.3 step 1b)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths

POLL_S = 2.0


def make_models_app(paths: Callable[[], Paths]) -> typer.Typer:
    models_app = typer.Typer(no_args_is_help=True, help="The local model (Ollama).")

    @models_app.command("status")
    def status() -> None:
        """The pinned model, whether Ollama is ready to use, and any install in progress."""
        with LocalClient(paths()) as c:
            st: dict[str, Any] = c.get("/v1/models")
        _print(st)
        if not st["ready"]:
            raise typer.Exit(1)

    @models_app.command("install")
    def install() -> None:
        """Pull the pinned model into Ollama, check its digest and copy it to ecf's own name."""
        with LocalClient(paths()) as c:
            c.request("POST", "/v1/models/install", {})
            last = ""
            while True:
                p: dict[str, Any] = c.get("/v1/models")["install"]
                line = _progress(p)
                if line != last:
                    typer.echo(line)
                    last = line
                if p["state"] in ("done", "failed"):
                    break
                time.sleep(POLL_S)
        if p["state"] == "failed":
            typer.echo(f"install failed: {p['error']}", err=True)
            raise typer.Exit(1)
        typer.echo("installed; check it with: ecf models status")

    return models_app


def _progress(p: dict[str, Any]) -> str:
    if p["total"]:
        pct = int(100 * p["completed"] / p["total"])
        return f"{p['state']}: {p['status']} {pct // 10 * 10}%"
    return f"{p['state']}: {p['status']}"


def _print(st: dict[str, Any]) -> None:
    pin = st["pin"]
    typer.echo(f"pinned: {pin['tag']} ({pin['digest'][:12]}), ecf's copy {pin['ecf_tag']}")
    typer.echo(f"installed: {st['installed_at'] or 'never on this install'}")
    if st["ready"]:
        where = ", ".join(st["listener"])
        typer.echo(f"ready: Ollama {st['version']}, the pinned model, listening on {where} only")
        for k, v in sorted(st.get("env", {}).items()):
            typer.echo(f"  {k}={v}")
    else:
        typer.echo(f"not ready: {st['fault']['text']}")
    if st["install"]["state"] not in ("idle",):
        typer.echo(
            f"last install: {_progress(st['install'])}"
            + (f" ({st['install']['error']})" if st["install"]["error"] else "")
        )
