"""`ecf drafts stats` (M8 plan §7, OD-583): what happened to the drafts ecf wrote. Counts only;
`--since` takes the range parser's forms (`ecf.timerange`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlencode

import typer

from ecf.cli_stats import since_param
from ecf.client import LocalClient
from ecf.paths import Paths

BY = ("model", "prompt", "category", "address", "seen_before")
NOT_ENOUGH = "not enough drafts yet"


def make_drafts_app(paths: Callable[[], Paths]) -> typer.Typer:
    app = typer.Typer(help="The drafts ecf wrote: what happened to them.", no_args_is_help=True)

    @app.command("stats")
    def stats(
        since: Annotated[
            str, typer.Option("--since", help="How far back: 24h, 7d, 2026-09-01, ...")
        ] = "30d",
        by: Annotated[
            str | None,
            typer.Option("--by", help="Split by model, prompt, category, address or seen_before."),
        ] = None,
    ) -> None:
        """Outcomes, how many reply-needed emails got a draft, ratings, safety counts, a weekly
        trend, and before and after a change to the actor's instructions."""
        if by is not None and by not in BY:
            raise typer.BadParameter("one of " + ", ".join(BY), param_hint="--by")
        params = {"since": since_param(since)} | ({"by": by} if by else {})
        with LocalClient(paths()) as c:
            show(c.get("/v1/drafts/stats?" + urlencode(params)))

    return app


def _pct(s: dict[str, Any]) -> str:
    if not s["n"]:
        return "none yet"
    return (f"{s['share'] * 100:.0f}% ({s['k']} of {s['n']}; 95% interval"
            f" {s['low'] * 100:.0f}-{s['high'] * 100:.0f}%)")  # fmt: skip


def show(r: dict[str, Any]) -> None:
    typer.echo(f"since {r['since'][:16].replace('T', ' ')} UTC")
    t, h = r["totals"], r["headline"]
    typer.echo(f"\n{t['drafts']} draft(s) written; {t['refused']} refused by the checker;"
               f" {t['offered']} offered to you")  # fmt: skip
    for k, v in sorted(r["outcomes"].items()):
        typer.echo(f"  {k}: {v}")
    typer.echo("\nRates")
    for label, key in (("saved", "saved"), ("rejected", "rejected"), ("expired", "expired"),
                       ("sent unchanged, of those sent", "sent_unchanged"),
                       ("👍, of those rated", "thumbs_up")):  # fmt: skip
        typer.echo(f"  {label}: {_pct(h[key])}")
    _coverage(r["coverage"])
    s = r["safety"]
    typer.echo(f"\nSafety\n  drafts with links copied from the email:"
               f" {s['drafts_with_copied_links']}\n  rated as promising something:"
               f" {s['promised']}")  # fmt: skip
    for code, n in sorted(s["refused_by_code"].items()):
        typer.echo(f"  refused, {code}: {n}")
    if r["reasons"]:
        typer.echo("\n👎 reasons")
        for k, v in sorted(r["reasons"].items()):
            typer.echo(f"  {k}: {v}")
    if r["weeks"]:
        typer.echo("\nBy week (written, saved, refused, 👍, 👎)")
        for w in r["weeks"]:
            typer.echo(f"  {w['week']}: {w['drafts']}, {w['saved']}, {w['refused']}, {w['up']},"
                       f" {w['down']}")  # fmt: skip
    _before_after(r["before_after"])
    if "by" in r:
        typer.echo(f"\nBy {r['by']['field']} (written, saved, refused, 👍, 👎)")
        for g in r["by"]["groups"]:
            typer.echo(f"  {g['name']}: {g['drafts']}, {g['saved']}, {g['refused']}, {g['up']},"
                       f" {g['down']}")  # fmt: skip
    _cost(r["cost"])


def _coverage(c: dict[str, Any]) -> None:
    typer.echo(f"\nEmails needing a reply: {c['reply_needed']}; got a draft: {_pct(c['drafted'])}")
    for k, v in sorted(c["rest"].items()):
        typer.echo(f"  no draft, {k}: {v}")
    for k, v in sorted(c["seen_before"].items()):
        typer.echo(f"  sender seen before, {k}: {_pct(v)}")


def _before_after(b: dict[str, Any] | None) -> None:
    if b is None:
        return
    typer.echo(f"\nBefore and after the actor's instructions changed ({b['before']} to"
               f" {b['after']})")  # fmt: skip
    for label, key in (("saved", "saved"), ("👍", "thumbs_up")):
        x = b[key]
        if not x["enough"]:
            typer.echo(f"  {label}: {NOT_ENOUGH} ({x['before_n']} before, {x['after_n']} after)")
            continue
        typer.echo(f"  {label}: {x['before']['share'] * 100:.0f}% to"
                   f" {x['after']['share'] * 100:.0f}%, difference"
                   f" {x['difference'] * 100:+.0f} points (95% interval {x['low'] * 100:+.0f} to"
                   f" {x['high'] * 100:+.0f})")  # fmt: skip


def _cost(c: dict[str, Any]) -> None:
    if not c["sessions"]:
        return
    per = f", about ${c['usd_per_item']:.3f} API-equivalent per email" if c["usd_per_item"] else ""
    typer.echo(f"\nCost, averaged over the whole review session\n  {c['sessions']} session(s),"
               f" {c['items']} email(s){per}; {c['drafts_per_session']} draft(s) per"
               " session")  # fmt: skip
