"""`ecf inbox` and `ecf item ...` (SPEC §10.2; V1.2 step 7a). Everything printed here may come from
an email, so control characters are removed before it reaches your terminal (an email can't send
escape sequences to it)."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.stepup import with_step_up

PREVIEW = 20


def plain(text: Any) -> str:
    """Text safe for a terminal: no control or format characters except line breaks and tabs."""
    return "".join(
        ch for ch in str(text) if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def item_line(i: dict[str, Any]) -> str:
    stale = " STALE" if i["stale"] else ""
    flag = " [payment/fraud]" if i["payment_or_fraud"] else ""
    return plain(f"{i['short_id']}  {i['address_id']:<14} {i['status']:<18}{stale}{flag}  "
                 f"{i['sender'][:40]}: {i['subject'][:60]}")  # fmt: skip


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> typer.Typer:
    """Adds `inbox` to `app` and returns the `item` group."""
    item_app = typer.Typer(no_args_is_help=True, help="One email ecf is tracking.")

    @app.command("inbox")
    def inbox(
        address: Annotated[str | None, typer.Option("--address", help="One address.")] = None,
        stale: Annotated[bool, typer.Option("--stale", help="Only stale items.")] = False,
    ) -> None:
        """Everything waiting on you: escalations and items needing a decision."""
        q = "&".join(p for p in (f"address_id={address}" if address else "",
                                 "stale=1" if stale else "") if p)  # fmt: skip
        with LocalClient(paths()) as c:
            found = c.get("/v1/inbox" + (f"?{q}" if q else ""))["items"]
        for i in found:
            typer.echo(item_line(i))
        typer.echo(f"{len(found)} waiting" + ("; details: ecf item show <id>" if found else ""))

    @item_app.command("show")
    def show(item: Annotated[str, typer.Argument(help="Item ID (8 or more characters).")]) -> None:
        """Everything ecf keeps about one email, including its stored excerpt."""
        with LocalClient(paths()) as c:
            d = c.get(f"/v1/items/{item}")
        for line in describe(d):
            typer.echo(plain(line))

    @item_app.command("resolve")
    def resolve(
        items: Annotated[list[str] | None, typer.Argument(help="Item IDs.")] = None,
        reason: Annotated[str, typer.Option("--reason", help="Why (kept in the audit log).")] = "",
        ids: Annotated[str | None, typer.Option("--ids", help="Comma-separated item IDs.")] = None,
        older_than: Annotated[
            int | None, typer.Option("--older-than", help="Every open item older than N days.")
        ] = None,
        address: Annotated[str | None, typer.Option("--address", help="With --older-than.")] = None,
    ) -> None:
        """Close emails without acting on them. (step-up for payment or fraud items)"""
        refs = [*(items or []), *(x.strip() for x in (ids or "").split(",") if x.strip())]
        if not reason.strip():
            raise typer.BadParameter("say why with --reason")
        body: dict[str, Any] = {"reason": reason, "address_id": address}
        if older_than is not None:
            if refs:
                raise typer.BadParameter("give item IDs or --older-than, not both")
            body["older_than_days"] = older_than
        elif refs:
            body["ids"] = refs
        else:
            raise typer.BadParameter("give item IDs, --ids or --older-than")
        with LocalClient(paths()) as c:
            preview = c.request("POST", "/v1/items/resolve", body | {"dry_run": True})
            chosen: list[str] = preview["would_resolve"]
            if not chosen:
                typer.echo("nothing to resolve")
                return
            if len(chosen) > 1 or older_than is not None:
                typer.echo(f"This closes {len(chosen)} email(s):")
                for sid in chosen[:PREVIEW]:
                    typer.echo(f"  {sid[:8]}")
                if len(chosen) > PREVIEW:
                    typer.echo(f"  ... and {len(chosen) - PREVIEW} more")
                if not typer.confirm("Close them?", default=False):
                    raise typer.Exit(1)
            done = with_step_up(c, lambda n: c.request("POST", "/v1/items/resolve",
                                                       body | {"ids": chosen, "nonce_id": n,
                                                               "older_than_days": None}),
                                echo=typer.echo)["resolved"]  # fmt: skip
        typer.echo(f"closed {len(done)} email(s) as resolved by you")

    return item_app


def describe(d: dict[str, Any]) -> list[str]:
    lines = [
        f"id:           {d['id']}",
        f"address:      {d['address_id']}   status: {d['status']}"
        + ("  (STALE)" if d["stale"] else ""),
        f"received:     {d['created_at']}",
        f"from:         {d['sender']}",
        f"subject:      {d['subject']}",
    ]
    if d["why"]:
        lines.append(f"why:          {d['why']}")
    lines += [
        f"sender check: {d['sender_check']}",
        f"flags:        {d['flags']}",
        f"done:         {d['done']}",
    ]
    if d["escalation"]:
        e = d["escalation"]
        lines.append(
            f"escalation:   {e['state']}" + (f" at {e['posted_at']}" if e["posted_at"] else "")
        )
    if d["payment_or_fraud"]:
        lines.append("note:         This acts on the email only. ecf never pays anything.")
    lines.append("history:")
    lines += [
        f"  {h['ts']}  {h['event']:<28} {h['actor']:<14} {h['outcome']}" for h in d["history"]
    ]
    lines.append("excerpt (stored text, shown only here):")
    lines += [f"  {x}" for x in (d["excerpt"] or "(none kept)").splitlines()]
    return lines
