"""`ecf models status|install` and `ecf models serve install|uninstall|status` (SPEC §7.5;
V1.3 steps 1b, 1c; OD-246); `ecf models api-key set|clear` and `ecf models watch`, the weekly
model watch (SPEC §7.6; V1.4 step 10)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf import ollama_unit
from ecf.client import LocalClient
from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths
from ecf.prompts import hidden, require_terminal

POLL_S = 2.0
START_WAIT_S = 30.0


def make_models_app(paths: Callable[[], Paths]) -> typer.Typer:
    models_app = typer.Typer(
        no_args_is_help=True, help="The local model (Ollama) and the pinned Claude models."
    )

    @models_app.command("status")
    def status() -> None:
        """The pinned model, whether Ollama is ready to use, and any install in progress."""
        with LocalClient(paths()) as c:
            st: dict[str, Any] = c.get("/v1/models")
        _print(st)
        if not st["ready"]:
            raise typer.Exit(1)

    _watch_commands(models_app, paths)

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
    def install(
        decision: Annotated[
            str | None,
            typer.Option(
                "--decision",
                help="Install an eval-only decision model from decision_models.lock"
                " (e.g. tev1-4b; SPEC §7.8) instead of the local model.",
            ),
        ] = None,
    ) -> None:
        """Pull the pinned model into Ollama, check its digest and copy it to ecf's own name.
        Starts ecf's Ollama login item first when nothing else serves Ollama."""
        with LocalClient(paths()) as c:
            ok = run_install(c, paths().root, decision=decision)
        if not ok:
            raise typer.Exit(1)

    return models_app


def run_install(c: LocalClient, root: Path, *, decision: str | None = None) -> bool:
    """`ecf models install` (also `ecf init`'s model step): start Ollama if needed, then pull,
    check and copy the pinned model, printing progress. True when it's installed. With
    `decision`, an eval-only decision model (SPEC §7.8)."""
    _ensure_ollama(c, root)
    c.request("POST", "/v1/models/install", {"decision": decision} if decision else {})
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
    if decision:  # eval only: the local model's readiness doesn't apply
        typer.echo(f"{decision} installed for evaluation only (ecf eval run"
                   f" --classifier-backend systemone:{decision})")  # fmt: skip
        return True
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
    for line in watch_lines(st.get("watch")):
        typer.echo(line)
    if st["install"]["state"] not in ("idle",):
        typer.echo(
            f"last install: {_progress(st['install'])}"
            + (f" ({st['install']['error']})" if st["install"]["error"] else "")
        )


def watch_lines(w: dict[str, Any] | None) -> list[str]:
    """The weekly model watch, for `ecf models status` (SPEC §7.6; V1.4 step 10)."""
    if not w:
        return []
    out: list[str] = []
    for mid, e in sorted(w["lifecycle"].items()):
        when = (f"retires {e['retires']}" if e["retires"] else
                f"retirement not before {e['not_sooner_than']}")  # fmt: skip
        out.append(f"  {mid}: {e['state']}, {when} (models.lock)")
    nxt = str(w["next_at"])[:16].replace("T", " ") + " UTC" if w["next_at"] else "within a minute"
    if not w["api_key"]:
        out.append(f"model watch: no Models API key (optional: ecf models api-key set);"
                   f" next run {nxt}")  # fmt: skip
    else:
        cl: dict[str, Any] = w.get("claude") or {}
        if cl.get("error"):
            out.append(f"model watch: Models API failed: {cl['error']}; next run {nxt}")
        elif cl.get("checked_at"):
            out.append(f"model watch: Models API read {str(cl['checked_at'])[:10]}"
                       f" ({cl['listed']} models); next run {nxt}")  # fmt: skip
            if cl.get("missing"):
                out.append(f"  not listed: {', '.join(cl['missing'])}")
            if cl.get("newer"):
                out.append(f"  newer in a pinned family: {', '.join(sorted(cl['newer']))}")
        else:
            out.append(f"model watch: Models API not read yet; next run {nxt}")
    ol: dict[str, Any] = w.get("ollama") or {}
    if ol.get("error"):
        out.append(f"  Ollama library: couldn't read the {ol.get('repo') or 'model'} tags page"
                   f" ({ol['error']})")  # fmt: skip
    elif ol.get("checked_at"):
        out.append(f"  Ollama library: {len(ol['tags'])} {ol['repo']} tags,"
                   f" read {str(ol['checked_at'])[:10]}")  # fmt: skip
    return out + _release_lines(w.get("release") or {})


def _release_lines(rel: dict[str, Any]) -> list[str]:
    """The watch's look at ecf's GitHub releases (§7.6; operator decision D7, 2026-10-06)."""
    if rel.get("error"):
        return [f"  ecf releases: couldn't list them ({rel['error']})"]
    if rel.get("checked_at"):
        newest = f"{rel['newest']} is out (ecf upgrade)" if rel.get("newest") else "none newer"
        return [f"  ecf releases: {newest}, read {str(rel['checked_at'])[:10]}"]
    return []


def _watch_commands(models_app: typer.Typer, paths: Callable[[], Paths]) -> None:
    """`ecf models api-key set|clear` and `ecf models watch` (SPEC §7.6; V1.4 step 10)."""
    key_app = typer.Typer(no_args_is_help=True, help="The optional Anthropic Models API key.")
    models_app.add_typer(key_app, name="api-key")

    @key_app.command("set")
    def key_set() -> None:
        """Store an API key so the weekly watch can read Anthropic's model list (optional)."""
        require_terminal()
        key = hidden("Anthropic API key, sk-ant-... (hidden): ")
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/models/api-key", {"key": key})
        typer.echo(f"Stored; the Models API lists {r['listed']} models for it. The watch runs"
                   " within a minute, then weekly (ecf models status).")  # fmt: skip

    @key_app.command("clear")
    def key_clear() -> None:
        """Remove the Models API key; retirement notices then come with ecf releases."""
        with LocalClient(paths()) as c:
            c.request("POST", "/v1/models/api-key", {"clear": True})
        typer.echo("Removed the Models API key.")

    @models_app.command("watch")
    def watch() -> None:
        """Run the weekly model watch at the next tick (within a minute)."""
        with LocalClient(paths()) as c:
            c.request("POST", "/v1/models/watch", {})
        typer.echo("The model watch runs within a minute; see ecf models status.")
