"""`ecf outbound enable|disable` (SPEC §8.4, §9.8; OD-323; V1.5 step 3b), `resume` (the send
circuit breaker, OD-059; step 5) and `report|snooze|dismiss` (the reminders, §9.8; step 6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

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

    @out_app.command("resume")
    def outbound_resume(address: ADDRESS) -> None:
        """Let this address send again after it reached its send limit. (step-up)"""
        path = f"/v1/addresses/{address}/outbound"
        with LocalClient(paths()) as c:
            r = with_step_up(c, lambda n: c.request("POST", path,
                                                    {"value": "resume", "nonce_id": n}),
                             echo=typer.echo)  # fmt: skip
        now, lim = r["counts"], r["limits"]
        typer.echo(f"{r['email']}: sends resume ({now['max_sends_per_hour']} in the last hour of"
                   f" {lim['max_sends_per_hour']}, {now['max_sends_per_day']} today of"
                   f" {lim['max_sends_per_day']})")  # fmt: skip

    @out_app.command("report")
    def outbound_report(address: ADDRESS) -> None:
        """What this address would have sent, your reviews of it, its sends and reminders."""
        with LocalClient(paths()) as c:
            r = c.get(f"/v1/addresses/{address}/outbound")
        for line in report_lines(r):
            typer.echo(line)

    @out_app.command("snooze")
    def outbound_snooze(
        address: ADDRESS,
        days: Annotated[int, typer.Option("--days", help="1-90 days.")] = 7,
    ) -> None:
        """No outbound reminders for this address for a while."""
        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/addresses/{address}/outbound",
                          {"value": "snooze", "days": days})  # fmt: skip
        typer.echo(f"{r['email']}: no outbound reminders until {_when(r['snoozed_until'])}")

    @out_app.command("dismiss")
    def outbound_dismiss(address: ADDRESS) -> None:
        """No more outbound reminders for this address (status and doctor still show it)."""
        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/addresses/{address}/outbound", {"value": "dismiss"})
        typer.echo(f"{r['email']}: outbound reminders dismissed")


def _when(ts: str | None) -> str:
    return ts[:16].replace("T", " ") + " UTC" if ts else "-"


def report_lines(r: dict[str, Any]) -> list[str]:
    rec, lim, now = r["record"], r["limits"], r["counts"]
    state = "on" if r["outbound"] else "off"
    out = [
        f"{r['email']} ({r['address_id']}): outbound {state}, {r['stage']}, {r['sensitivity']}",
        f"suppressed sends: {rec['suppressed']}; reviewed {rec['reviewed']},"
        f" {rec['correct']} marked correct ({rec['share']:.0%})",
    ]
    if r["high_gate"]:
        out.append(f"before enabling: {r['high_gate']}")
    if r["suppressed"]:
        out.append("latest suppressed (review them with Correct or Fix on their Slack cards):")
        for s in r["suppressed"]:
            out.append(
                f"  {s['short_id']}  {_when(s['created_at'])}  {s['action']:<16}"
                f" {s['review'] or 'not reviewed':<12} {s['sender']}: {s['subject']}"
            )
    out.append(
        f"send limits: {now['max_sends_per_hour']} of {lim['max_sends_per_hour']} this hour,"
        f" {now['max_sends_per_day']} of {lim['max_sends_per_day']} today"
        + ("; STOPPED at the limit (ecf outbound resume)" if r["tripped"] else "")
    )
    if r["sends"]:
        out.append("latest sends:")
        out += [
            f"  {s['short_id']}  {_when(s['at'])}  {s['kind']:<8} {s['status']}" for s in r["sends"]
        ]
    rem = r["reminders"]
    if rem["dismissed_at"]:
        out.append(f"reminders: dismissed {_when(rem['dismissed_at'])}")
    elif rem["next_at"]:
        snoozed = f" (snoozed until {_when(rem['snoozed_until'])})" if rem["snoozed_until"] else ""
        out.append(f"reminders: {rem['sent']} sent, next {_when(rem['next_at'])}{snoozed}")
    elif not r["outbound"] and rem["live_since"] is None:
        out.append("reminders: start 7 days after the address goes live")
    return out
