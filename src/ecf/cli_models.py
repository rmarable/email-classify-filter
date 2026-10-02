"""`ecf models status|install` and `ecf models serve install|uninstall|status` (SPEC §7.5;
V1.3 steps 1b, 1c; OD-246)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from ecf import ollama_unit
from ecf.client import LocalClient
from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths

POLL_S = 2.0
START_WAIT_S = 30.0


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

    serve_app = typer.Typer(no_args_is_help=True, help="ecf's login item that runs Ollama.")
    models_app.add_typer(serve_app, name="serve")

    @serve_app.command("install")
    def serve_install() -> None:
        """Run Ollama from ecf's login item with ecf's fixed settings (replaces `brew services`)."""
        unit = ollama_unit.manager_for(paths().root)
        unit.install()
        typer.echo(f"installed {unit.unit_path}; Ollama runs with "
                   + " ".join(f"{k}={v}" for k, v in ollama_unit.ENV.items()))  # fmt: skip

    @serve_app.command("uninstall")
    def serve_uninstall() -> None:
        """Stop Ollama's login item and remove it (the pulled model stays)."""
        unit = ollama_unit.manager_for(paths().root)
        unit.uninstall()
        typer.echo(f"removed {unit.unit_path}")

    @serve_app.command("status")
    def serve_status() -> None:
        """Whether ecf's Ollama login item is installed and running."""
        st = ollama_unit.manager_for(paths().root).status()
        typer.echo(f"installed: {'yes' if st.installed else 'no'}; running:"
                   f" {'yes' if st.running else 'no'}")  # fmt: skip
        if st.detail.startswith("its program"):
            typer.echo(st.detail, err=True)
        if not st.running:
            raise typer.Exit(1)

    @models_app.command("install")
    def install() -> None:
        """Pull the pinned model into Ollama, check its digest and copy it to ecf's own name.
        Starts ecf's Ollama login item first when nothing else serves Ollama."""
        with LocalClient(paths()) as c:
            ok = run_install(c, paths().root)
        if not ok:
            raise typer.Exit(1)

    return models_app


def run_install(c: LocalClient, root: Path) -> bool:
    """`ecf models install` (also `ecf init`'s model step): start Ollama if needed, then pull,
    check and copy the pinned model, printing progress. True when it's installed."""
    _ensure_ollama(c, root)
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
        if p["state"] == "idle":  # the install's progress lives in the service's memory
            raise ServiceUnavailableError("the service restarted during the install: run"
                                          " `ecf models install` again")  # fmt: skip
        time.sleep(POLL_S)
    if p["state"] == "failed":
        typer.echo(f"install failed: {p['error']}", err=True)
        return False
    st: dict[str, Any] = c.get("/v1/models")
    if not st["ready"]:  # installed, but something else stops model work (e.g. the listener)
        typer.echo(f"installed, but not ready: {st['fault']['text']}", err=True)
        return False
    typer.echo("installed and ready; check it any time with: ecf models status")
    return True


def _cause(c: LocalClient) -> str | None:
    fault: dict[str, Any] = c.get("/v1/models").get("fault") or {}
    return fault.get("cause")


def _ensure_ollama(c: LocalClient, root: Path) -> None:
    cause = _cause(c)
    unit = ollama_unit.manager_for(root)
    if cause == "not_running":
        if not unit.status().installed:
            typer.echo("starting Ollama from ecf's login item (ecf models serve install)")
        unit.install()
        deadline = time.monotonic() + START_WAIT_S
        while _cause(c) == "not_running":
            if time.monotonic() > deadline:
                raise ServiceUnavailableError(f"Ollama didn't start within {START_WAIT_S:.0f} s;"
                                              " see `ecf models serve status`")  # fmt: skip
            time.sleep(1)
    elif not unit.status().running:
        typer.echo("note: Ollama is running outside ecf's login item, so ecf can't set its"
                   " settings; ecf doctor shows any that differ (ecf models serve install"
                   " replaces it once you stop the other one)")  # fmt: skip


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
    cl = st.get("claude")
    if cl:  # V1.4 step 2: the Claude models presets B and C use
        typer.echo("Claude pins (models.lock): " + ", ".join(
            f"{r} {i}" for r, i in cl["effective"].items()))  # fmt: skip
        for fam, i in sorted(cl["overrides"].items()):
            typer.echo(f"  override: {fam} -> {i} (ecf settings set claude_model_override none"
                       " clears it)")  # fmt: skip
    if st["install"]["state"] not in ("idle",):
        typer.echo(
            f"last install: {_progress(st['install'])}"
            + (f" ({st['install']['error']})" if st["install"]["error"] else "")
        )
