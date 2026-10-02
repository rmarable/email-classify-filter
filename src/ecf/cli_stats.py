"""`ecf stats` (SPEC §13.4; V1.3 step 9): the local model's tokens, speeds and load times; from
V1.4 step 6 also Claude's tokens, times and plan usage from `ecf claude` sessions."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlencode

import typer

from ecf.client import LocalClient
from ecf.paths import Paths

_SINCE = re.compile(r"^(\d+(?:\.\d+)?)([hd])$")


def hours_of(since: str) -> float:
    """`24h`, `7d`, `1.5d` → hours."""
    m = _SINCE.match(since.strip().lower())
    if not m:
        raise typer.BadParameter("a number of hours or days, like 24h or 7d", param_hint="--since")
    return float(m.group(1)) * (24 if m.group(2) == "d" else 1)


def make_stats_command(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("stats")
    def stats(
        since: Annotated[str, typer.Option("--since", help="How far back: 24h, 7d, ...")] = "7d",
        address: Annotated[str | None, typer.Option("--address", help="One address.")] = None,
        preset: Annotated[str | None, typer.Option("--preset", help="A, B or C.")] = None,
    ) -> None:
        """Model usage: the local model's tokens, speeds and load times by model and role, and
        Claude's tokens, times and plan usage from `ecf claude` sessions."""
        params: dict[str, Any] = {"hours": hours_of(since)}
        if address:
            params["address"] = address
        if preset:
            params["preset"] = preset
        with LocalClient(paths()) as c:
            r: dict[str, Any] = c.get("/v1/stats?" + urlencode(params))
        show(r)


def show(r: dict[str, Any]) -> None:
    typer.echo(f"since {r['since'][:16].replace('T', ' ')} UTC")
    if not r["all"]["calls"]:
        typer.echo("the local model wasn't used in this period")
    else:
        for g in r["groups"]:
            typer.echo(f"\n{g['role']} (model {str(g['model'])[:19]})")
            _figures(g)
        typer.echo("\nall roles")
        _figures(r["all"])
    if "claude" in r:
        show_claude(r["claude"])


def show_claude(c: dict[str, Any]) -> None:
    typer.echo("\nClaude (ecf claude sessions, all addresses)")
    if not c["all"]["calls"]:
        typer.echo("  not used in this period")
    else:
        refused = f", {c['refused']} refused by the model check" if c["refused"] else ""
        per = f", about {c['tokens_per_item']} tokens per email" if c["tokens_per_item"] else ""
        typer.echo(f"  {c['sessions']} session(s), {c['items']} email(s){per}{refused}")
        for g in c["groups"]:
            typer.echo(f"  {g['model']} ({g['source']})")
            _claude_figures(g)
        typer.echo("  all")
        _claude_figures(c["all"])
    p = c.get("plan")
    if p:
        used = ", ".join(f"{label} {p[k]:.0f}%" for k, label in (("five_hour", "5-hour"),
                                                                ("seven_day", "7-day"))
                         if p.get(k) is not None)  # fmt: skip
        typer.echo(f"  plan used after the last review ({p['at'][:16].replace('T', ' ')} UTC):"
                   f" {used}")  # fmt: skip


def _claude_figures(g: dict[str, Any]) -> None:
    typer.echo(f"    calls: {g['calls']}; tokens: {g['input_tokens']} in, {g['output_tokens']}"
               f" out, {g['cache_read_tokens']} cache read, {g['cache_creation_tokens']}"
               " cache written")  # fmt: skip
    s = g["seconds"]
    typer.echo(f"    time per request: {_n(s['median'], 2)} s median, {_n(s['p95'], 2)} s p95")
    if g["api_equivalent_usd"] is not None:
        typer.echo(f"    API-equivalent cost: ${g['api_equivalent_usd']:.2f} (not a charge on a"
                   " plan)")  # fmt: skip


def _figures(g: dict[str, Any]) -> None:
    failed = f", {g['failed']} failed" if g["failed"] else ""
    typer.echo(f"  calls: {g['calls']}{failed}")
    per_email = _n(g["tokens_per_email"], 0)
    typer.echo(f"  tokens: {g['input_tokens']} in ({g['cached_tokens']} cached),"
               f" {g['output_tokens']} out; about {per_email} per email")  # fmt: skip
    p, w = g["prompt_tps"], g["generation_tps"]
    typer.echo(f"  reading: {_n(p['median'])} tokens/s median, slowest 5% {_n(p['slowest_5'])}")
    typer.echo(f"  writing: {_n(w['median'])} tokens/s median, slowest 5% {_n(w['slowest_5'])}")
    s, ld = g["seconds"], g["load_seconds"]
    typer.echo(f"  time per call: {_n(s['median'], 2)} s median, {_n(s['p95'], 2)} s p95")
    typer.echo(f"  model load: {_n(ld['median'], 2)} s median, {_n(ld['p95'], 2)} s p95,"
               f" {ld['cold_starts']} cold start(s)")  # fmt: skip


def _n(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v:.{digits}f}"
