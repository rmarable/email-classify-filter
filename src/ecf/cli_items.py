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

    @item_app.command("requeue")
    def requeue(item: Annotated[str, typer.Argument(help="Item ID.")]) -> None:
        """Run a failed or stuck action again. (step-up for sends)"""
        with LocalClient(paths()) as c:
            with_step_up(c, lambda n: c.request("POST", f"/v1/items/{item}/requeue",
                                                {"nonce_id": n}), echo=typer.echo)  # fmt: skip
        typer.echo("queued to run again")

    _decision_commands(app, paths)
    return item_app


def _decision_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("approve")
    def approve(
        item: Annotated[str | None, typer.Argument(help="Item ID (8 or more characters).")] = None,
        pending: Annotated[
            bool, typer.Option("--pending", help="Approvals queued from Slack for step-up.")
        ] = False,
    ) -> None:
        """Approve an action ecf proposed. (step-up for sends, irreversible actions, and hiding
        fraud or regulator email)"""
        with LocalClient(paths()) as c:
            if pending:
                _approve_pending(c)
                return
            if not item:
                raise typer.BadParameter("give an item ID, or --pending")
            r = with_step_up(c, lambda n: c.request("POST", f"/v1/items/{item}/approve",
                                                    {"nonce_id": n}), echo=typer.echo)  # fmt: skip
        typer.echo(_after(r["status"]))

    @app.command("answer")
    def answer(
        item: Annotated[str, typer.Argument(help="Item ID.")],
        text: Annotated[
            str | None, typer.Argument(help="Your answer (asked for if left out).")
        ] = None,
    ) -> None:
        """Answer ecf's question about an email, or confirm an answer you gave in Slack.
        (step-up on payment or fraud items)"""
        with LocalClient(paths()) as c:
            d = c.get(f"/v1/items/{item}")
            p: dict[str, Any] = d.get("proposal") or {}
            if text is None and p.get("answer_pending"):
                typer.echo(plain(f"Your answer from Slack: {p['answer_pending']}"))
                if not typer.confirm("Send it?", default=True):
                    raise typer.Exit(1)
            elif text is None:
                typer.echo(plain(f"ecf's model asks (it can be wrong): {p.get('question', '')}"))
                text = typer.prompt("Your answer")
            body = {"text": text}
            path = f"/v1/items/{item}/answer"
            with_step_up(c, lambda n: c.request("POST", path, body | {"nonce_id": n}),
                         echo=typer.echo)  # fmt: skip
        typer.echo("answered: ecf will use it")

    @app.command("reject")
    def reject(item: Annotated[str, typer.Argument(help="Item ID.")]) -> None:
        """Reject an action ecf proposed; nothing is done."""
        with LocalClient(paths()) as c:
            c.request("POST", f"/v1/items/{item}/reject")
        typer.echo("rejected: nothing was done")

    @app.command("cancel")
    def cancel(item: Annotated[str, typer.Argument(help="Item ID.")]) -> None:
        """Cancel a send during its 10-minute delay."""
        with LocalClient(paths()) as c:
            c.request("POST", f"/v1/items/{item}/cancel")
        typer.echo("cancelled: not sent")


AFTER = {
    "executing": "approved: running now",
    "delayed": "approved: sending in 10 minutes (ecf cancel <id> to stop it)",
    "awaiting_stepup": "queued for step-up",
}


def _after(status: str) -> str:
    return AFTER.get(status, f"approved ({status})")


def _approve_pending(c: LocalClient) -> None:
    p = c.request("POST", "/v1/approvals/pending", {})
    for s in p["sends"]:
        typer.echo(plain(f"send (approve on its own): ecf approve {s['short_id']}: {s['action']}"))
    batch: list[dict[str, Any]] = p["batch"]
    if not batch:
        typer.echo("no approvals waiting for step-up" + (" besides sends" if p["sends"] else ""))
        return
    typer.echo(f"{len(batch)} approval(s) waiting for step-up:")
    for b in batch:
        typer.echo(plain(f"  {b['short_id']}  {b['action']}: {b['sender'][:40]}: "
                         f"{b['subject'][:50]}"))  # fmt: skip
    if p["more"]:
        typer.echo(f"  ({p['more']} more after these)")
    if not typer.confirm("Approve all of these with one step-up?", default=False):
        raise typer.Exit(1)
    ids = [b["id"] for b in batch]
    r = with_step_up(c, lambda n: c.request("POST", "/v1/approvals/pending",
                                            {"confirm_ids": ids, "nonce_id": n}),
                     echo=typer.echo)  # fmt: skip
    for x in r["results"]:
        typer.echo(f"  {x['id'][:8]}: {_after(x['status'])}")


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
