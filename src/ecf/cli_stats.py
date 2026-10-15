"""`ecf stats` (SPEC §13.4; V1.3 step 9): the model calls ecf records (`model_calls`: the local
model's before v3.0.0, ADR 0031, until pruned with the logs), and from V1.4 step 6 Claude's
tokens, times and plan usage from `ecf claude` sessions. From v3.0.0 step 5 (OD-507): Slack post
times, and with `--stages` how long items spend in each stage; `--since` takes the range parser's
forms (`ecf.timerange`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import typer

from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf.timerange import parse, stamp


def since_param(since: str) -> str:
    """`--since` as the UTC time the service reads; refused here with the parser's hint."""
    try:
        return stamp(parse(since, now=datetime.now(UTC)))
    except InvalidInputError as exc:
        raise typer.BadParameter(exc.detail, param_hint="--since") from None


def make_stats_command(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("stats")
    def stats(
        since: Annotated[
            str, typer.Option("--since", help="How far back: 24h, 7d, 2026-09-01, ...")
        ] = "7d",
        address: Annotated[str | None, typer.Option("--address", help="One address.")] = None,
        preset: Annotated[
            str | None, typer.Option("--preset", help="C or D (older calls may be A or B).")
        ] = None,
        stages: Annotated[
            bool,
            typer.Option(
                "--stages", help="Time items spend in each stage, and waiting for /ecf-review."
            ),
        ] = False,
    ) -> None:
        """Model usage: recorded model calls' tokens, speeds and load times by model and role
        (the local model's, before v3.0.0), TypeSafe Jev's calls (preset D), Claude's tokens,
        times and plan usage from `ecf claude` sessions, and Slack post times. With --stages:
        time in each item stage."""
        params: dict[str, Any] = {"since": since_param(since)}
        if stages:
            with LocalClient(paths()) as c:
                show_stages(c.get("/v1/stats?" + urlencode(params | {"stages": 1})))
            return
        if address:
            params["address"] = address
        if preset:
            params["preset"] = preset
        with LocalClient(paths()) as c:
            r: dict[str, Any] = c.get("/v1/stats?" + urlencode(params))
        show(r)


def show_stages(r: dict[str, Any]) -> None:
    typer.echo(f"since {r['since'][:16].replace('T', ' ')} UTC")
    groups = (("new_mail", "New mail"), ("scan_or_reprocess", "Scanned or reprocessed mail"))
    for key, title in groups:
        g = r[key]
        typer.echo(f"\n{title}: {g['items']} item(s)")
        if not g["stages"]:
            typer.echo("  no stage changes in this period")
        for st in g["stages"]:
            typer.echo(f"  {st['stage']}: {_n(st['median_s'], 1)} s median,"
                       f" {_n(st['p95_s'], 1)} s p95 (n {st['n']})")  # fmt: skip
        w = g["claude_wait"]
        if w["n"]:
            typer.echo(f"  waiting for /ecf-review: {_n(w['median_s'], 0)} s median,"
                       f" {_n(w['p95_s'], 0)} s p95 (n {w['n']})")  # fmt: skip
    if r["imported_left_out"]:
        typer.echo(f"\n{r['imported_left_out']} imported item(s) left out (their times are"
                   " another computer's)")  # fmt: skip


def show_slack(s: dict[str, Any]) -> None:
    typer.echo("\nSlack posts")
    if not s["posts"]:
        typer.echo("  none in this period")
        return
    lost = s["outcomes"].get("dead", 0) + s["outcomes"].get("channel_gone", 0)
    typer.echo(f"  {s['posts']} post(s){f', {lost} not delivered' if lost else ''};"
               f" {s['retries']} retries; {s['rate_limited']} rate-limited"
               f" ({s['rate_hold_s']} s held); {s['pace_s']} s pacing")  # fmt: skip
    w, c = s["wait"], s["call"]
    typer.echo(f"  queued to done: {_n(w['median_s'], 1)} s median, {_n(w['p95_s'], 1)} s p95")
    typer.echo(f"  Slack call: {_n(c['median_s'], 2)} s median, {_n(c['p95_s'], 2)} s p95")


def show(r: dict[str, Any]) -> None:
    typer.echo(f"since {r['since'][:16].replace('T', ' ')} UTC")
    if not r["all"]["calls"]:
        typer.echo("no recorded model calls in this period")
    else:
        for g in r["groups"]:
            typer.echo(f"\n{g['role']} (model {str(g['model'])[:19]})")
            _figures(g)
        typer.echo("\nall roles")
        _figures(r["all"])
    jev: dict[str, Any] = r.get("jev") or {}  # preset D's TypeSafe Jev (v3.0.0 step 4)
    if jev.get("line"):
        typer.echo(f"\n{jev['line']}")
    if "claude" in r:
        show_claude(r["claude"])
    if "slack" in r:
        show_slack(r["slack"])


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
