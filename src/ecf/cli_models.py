"""`ecf models status` (the Claude pins, SPEC §7.5), `ecf models api-key set|clear --provider
anthropic|typesafe` (v3.0.0 step 2a, OD-503) and `ecf models watch`, the weekly model watch
(SPEC §7.6; V1.4 step 10). `ecf models install|prune|serve`, for the local model, went in v3.0.0
(ADR 0031)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import typer

from ecf import __version__, api_keys
from ecf.client import LocalClient
from ecf.errors import ConflictError
from ecf.paths import Paths
from ecf.prompts import hidden, require_terminal
from ecf.upgrade_check import version_key


def make_models_app(paths: Callable[[], Paths]) -> typer.Typer:
    models_app = typer.Typer(no_args_is_help=True, help="The pinned Claude models and API keys.")

    @models_app.command("status")
    def status() -> None:
        """The pinned Claude models, any override, and the weekly model watch."""
        with LocalClient(paths()) as c:
            st: dict[str, Any] = c.get("/v1/models/claude")
        _print(st)

    _watch_commands(models_app, paths)
    return models_app


def _print(st: dict[str, Any]) -> None:
    cl: dict[str, Any] = st["claude"]
    typer.echo("Claude pins (models.lock): " + ", ".join(
        f"{r} {i}" for r, i in cl["effective"].items()))  # fmt: skip
    for fam, i in sorted(cl["overrides"].items()):
        typer.echo(f"  override: {fam} -> {i} (ecf settings set claude_model_override none"
                   " clears it)")  # fmt: skip
    for line in watch_lines(st.get("watch")):
        typer.echo(line)


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
        out.append("model watch: no Models API key (optional: ecf models api-key set --provider"
                   f" anthropic); next run {nxt}")  # fmt: skip
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
    return out + _jev_lines(w) + _release_lines(w.get("release") or {})


def _jev_lines(w: dict[str, Any]) -> list[str]:
    """The Jev pin (`jev.lock`), the TypeSafe aliases the watch last read (v3.0.0 step 4) and the
    version jev-latest answered as (OD-561): the listing can't see the pin retire."""
    if not w.get("jev_pin"):
        return []
    out = [f"Jev pin (jev.lock): {w['jev_pin']}"]
    j: dict[str, Any] = w.get("jev") or {}
    if j.get("error"):
        out.append(f"  TypeSafe's models: couldn't read them: {j['error']}")
    elif j.get("checked_at"):
        aliases: dict[str, Any] = j.get("aliases") or {}
        listed = ", ".join(f"{a} released {d or 'unknown'}" for a, d in sorted(aliases.items()))
        out.append(f"  TypeSafe's aliases, read {str(j['checked_at'])[:10]}: {listed or 'none'}")
    # kept through a failed check: the last version jev-latest answered as
    if (newer := j.get("newer")) is not None:
        out.append(f"  TypeSafe's jev-latest is now {newer['version']} (seen"
                   f" {str(newer['seen_at'])[:10]}); the pin is {w['jev_pin']}: read"
                   " docs.typesafe.ai/models")  # fmt: skip
    elif j.get("latest") and not j.get("error"):
        out.append(f"  jev-latest answers as {j['latest']}, the pinned version")
    return out


def _release_lines(rel: dict[str, Any]) -> list[str]:
    """The watch's look at ecf's GitHub releases (§7.6; operator decision D7, 2026-10-06)."""
    if rel.get("error"):
        return [f"  ecf releases: couldn't list them ({rel['error']})"]
    if rel.get("checked_at"):
        newest = f"{rel['newest']} is out (ecf upgrade)" if rel.get("newest") else "none newer"
        return [f"  ecf releases: {newest}, read {str(rel['checked_at'])[:10]}"]
    return []


OLDER_SERVICE = "the service is older than this ecf; restart it from this version first"
PROVIDER_HELP = ("anthropic (the model watch's Models API key), typesafe (Jev) or mistral"
                 " (`ecf eval grade`); required.")  # fmt: skip
PROVIDER = typer.Option("--provider", help=PROVIDER_HELP, show_default=False)


def check_service_version(c: LocalClient) -> None:
    """Refuse when the service runs an older ecf than this CLI (v3.0.0 step 2a, P4): its key
    routes may differ. Equal versions pass: two checkouts between bumps can't be told apart, and
    then the per-provider route is the guard (an older service answers it with not found)."""
    st: dict[str, Any] = c.get("/v1/status")
    if version_key(str(st["version"])) < version_key(__version__):
        raise ConflictError(OLDER_SERVICE)


def _watch_commands(models_app: typer.Typer, paths: Callable[[], Paths]) -> None:
    """`ecf models api-key set|clear --provider ...` (v3.0.0 step 2a, OD-503) and `ecf models
    watch` (SPEC §7.6; V1.4 step 10)."""
    key_app = typer.Typer(
        no_args_is_help=True,
        help="API keys: Anthropic's Models API (optional), TypeSafe (Jev), Mistral (eval grade).",
    )
    models_app.add_typer(key_app, name="api-key")

    @key_app.command("set")
    def key_set(provider: Annotated[str | None, PROVIDER] = None) -> None:
        """Store an API key: anthropic lets the weekly watch read Anthropic's model list
        (optional); typesafe lets ecf call Jev; mistral lets `ecf eval grade` call Mistral.
        Checked by one call; a Security Notice follows."""
        name = api_keys.check_provider(provider)
        require_terminal()
        with LocalClient(paths()) as c:
            check_service_version(c)
            key = api_keys.check_key(name, hidden(f"{api_keys.LABEL[name]} (hidden): "))
            r = c.request("POST", f"/v1/models/keys/{name}", {"key": key})
        if name == "anthropic":
            typer.echo(f"Stored; the Models API lists {r['listed']} models for it. The watch runs"
                       " within a minute, then weekly (ecf models status).")  # fmt: skip
        else:
            who = "TypeSafe" if name == "typesafe" else "Mistral"
            typer.echo(f"{'Replaced' if r.get('replaced') else 'Stored'} the {who} API key;"
                       f" {who} accepted it.")  # fmt: skip

    @key_app.command("clear")
    def key_clear(provider: Annotated[str | None, PROVIDER] = None) -> None:
        """Remove a stored API key (a Security Notice follows). Without the Anthropic key,
        retirement notices come with ecf releases."""
        name = api_keys.check_provider(provider)
        with LocalClient(paths()) as c:
            check_service_version(c)
            c.request("POST", f"/v1/models/keys/{name}", {"clear": True})
        typer.echo({"anthropic": "Removed the Models API key.",
                    "typesafe": "Removed the TypeSafe API key.",
                    "mistral": "Removed the Mistral API key."}[name])  # fmt: skip

    @models_app.command("watch")
    def watch() -> None:
        """Run the weekly model watch at the next tick (within a minute)."""
        with LocalClient(paths()) as c:
            c.request("POST", "/v1/models/watch", {})
        typer.echo("The model watch runs within a minute; see ecf models status.")
