"""`ecf backfill` and `ecf reprocess` (SPEC §10.2; OD-507, plan v3.0.0-STEP5 §3, §4): old mail
scanned or decided again, as service runs with one Slack summary each.

Both read `--since` and `--until` with the one range parser (`ecf.timerange`) and echo the range
in UTC before anything runs. `ecf backfill --act` first has the service count the range (at the
next check), shows the count and an estimate, asks, and needs step-up over 500 emails to classify;
`--yes` skips the question, never the step-up.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import typer

from ecf import timerange
from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf.stepup import with_step_up

COUNT_WAIT_S = 900  # the count runs at the address's next check
POLL_S = 2.0


def resolve(since: str, until: str | None) -> tuple[str, str | None]:
    """The range as UTC times, echoed; refused with the parser's hint."""
    try:
        start, end = timerange.resolve(since, until, now=datetime.now(UTC))
    except InvalidInputError as exc:
        raise typer.BadParameter(exc.detail) from None
    a, b = timerange.stamp(start), timerange.stamp(end) if end else None
    typer.echo(f"range (UTC): {a[:19].replace('T', ' ')} to "
               f"{b[:19].replace('T', ' ') if b else 'now'}")  # fmt: skip
    return a, b


def run_backfill(c: LocalClient, address: str, since: str, until: str | None, *, act: bool,
                 yes: bool) -> dict[str, Any]:  # fmt: skip
    a, b = resolve(since, until)
    body: dict[str, Any] = {"address_id": address, "since": a, "until": b, "act": act,
                            "typed": {"since": since, "until": until}}  # fmt: skip
    if act:
        n = count(c, address, a, b)
        show_count(n)
        if not yes:
            stage = next((s["stage"] for s in c.get("/v1/stages")["addresses"]
                          if address in (s["address_id"], s.get("email"))), "?")  # fmt: skip
            typer.echo(f"--act runs the pre-check on these as on new mail; {address} is in"
                       f" {stage} (shadow does nothing, assist labels and flags), and each waits"
                       " for /ecf-review. Fraud and regulator finds go to the scan's Slack"
                       " summary.")  # fmt: skip
            if not typer.confirm("Go ahead?", default=False):
                raise typer.Exit(1)
    return with_step_up(c, lambda nonce: c.request("POST", "/v1/backfill",
                                                   body | {"nonce_id": nonce}))  # fmt: skip


def count(c: LocalClient, address: str, since: str, until: str | None) -> dict[str, Any]:
    """Ask the service to count the range and wait for the next check's answer."""
    c.request("POST", "/v1/backfill/count", {"address_id": address, "since": since,
                                             "until": until})  # fmt: skip
    typer.echo("counting at the address's next check...")
    waited = 0.0
    while waited < COUNT_WAIT_S:
        n = c.get(f"/v1/backfill/count?address_id={address}")
        if n["state"] == "done" and (n["since"], n["until"]) == (since, until):
            return n
        time.sleep(POLL_S)
        waited += POLL_S
    raise typer.BadParameter("no check counted it within 15 minutes: is the service checking"
                             " this address? (ecf status)")  # fmt: skip


def show_count(n: dict[str, Any]) -> None:
    typer.echo(f"{n['in_range']} in range, {n['held']} already held, {n['to_classify']} to"
               " classify (as of the last check; may be one check stale)")  # fmt: skip
    e = n["estimate"]
    if "usd" in e:
        typer.echo(f"about ${e['usd']:.2f} API-equivalent (${e['per_email_usd']:.4f} per email,"
                   f" {e['source']}); not a charge on a plan")  # fmt: skip
    else:
        typer.echo(f"about ${e['usd_low']:.2f}-{e['usd_high']:.2f} API-equivalent, from"
                   f" {e['source']}; not a charge on a plan")  # fmt: skip
    if e["minutes"] is not None:
        typer.echo(f"about {e['minutes']} minutes of /ecf-review session time")
    else:
        typer.echo("/ecf-review session time: not measured yet")
    typer.echo("while these wait, new mail is reviewed first; they don't count toward the"
               " reminders")  # fmt: skip
    if n["to_classify"] > ACT_CAP:
        typer.echo(f"over {ACT_CAP} emails to classify: step-up follows")


ACT_CAP = 500


def make_reprocess_command(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("reprocess")
    def reprocess(
        address: Annotated[str | None, typer.Argument(help="Address id or email.")] = None,
        since: Annotated[
            str | None, typer.Option("--since", help="From: a date, a time or an age (30d).")
        ] = None,
        until: Annotated[
            str | None, typer.Option("--until", help="Up to this time (default now).")
        ] = None,
        rules_only: Annotated[
            bool, typer.Option("--rules-only", help="Plan again on stored classifications only.")
        ] = False,
        force: Annotated[
            bool, typer.Option("--force", help="Every eligible item, changed or not.")
        ] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Count only.")] = False,
        status: Annotated[bool, typer.Option("--status", help="Show each reprocess.")] = False,
        stop: Annotated[bool, typer.Option("--stop", help="End a running reprocess.")] = False,
    ) -> None:
        """Decide old mail again after its classifier, prompt, schema or rules changed (step-up
        over 500 to classify, or 500 that could change the mailbox)."""
        with LocalClient(paths()) as c:
            if status or address is None:
                _reprocess_status(c.get("/v1/reprocess")["runs"])
                return
            if stop:
                r = c.request("POST", "/v1/reprocess/stop", {"address_id": address})
                typer.echo(f"stopped the reprocess of {r['address_id']}: {r['reclassify']} sent"
                           f" to Claude, {r['replan']} planned again so far")  # fmt: skip
                return
            if since is None:
                raise typer.BadParameter("add --since, e.g. --since 2026-09-01 or --since 30d")
            run_reprocess(c, address, since, until, rules_only=rules_only, force=force,
                          dry_run=dry_run)  # fmt: skip


def run_reprocess(c: LocalClient, address: str, since: str, until: str | None, *,
                  rules_only: bool, force: bool,
                  dry_run: bool) -> dict[str, Any] | None:  # fmt: skip
    """The dry run's screen, always; then confirmation and, over the cap, step-up."""
    a, b = resolve(since, until)
    body: dict[str, Any] = {"address_id": address, "since": a, "until": b,
                            "rules_only": rules_only, "force": force,
                            "typed": {"since": since, "until": until}}  # fmt: skip
    n = c.request("POST", "/v1/reprocess", body | {"dry_run": True})
    skipped = ", ".join(f"{v} {k}" for k, v in sorted(n["skipped"].items())) or "none"
    typer.echo(f"{n['in_range']} held in range: {n['reclassify']} reclassify, {n['replan']}"
               f" re-plan only, {n['unchanged']} unchanged; {n['touch']} would touch the"
               f" mailbox; skipped: {skipped}")  # fmt: skip
    if n["reclassify"]:
        typer.echo("reclassified mail waits for /ecf-review, after new mail; it doesn't count"
                   " toward the reminders")  # fmt: skip
    if dry_run or not (n["reclassify"] or n["replan"]):
        return None
    if n["reclassify"] > ACT_CAP or n["touch"] > ACT_CAP:
        typer.echo(f"over {ACT_CAP}: step-up follows")
    if not typer.confirm("Go ahead?", default=False):
        raise typer.Exit(1)
    r: dict[str, Any] = with_step_up(c, lambda nonce: c.request(
        "POST", "/v1/reprocess", body | {"nonce_id": nonce}))  # fmt: skip
    typer.echo(f"reprocess {r['run_id']} of {r['address_id']} started; it runs in batches with"
               " one Slack summary. Progress: ecf reprocess --status")  # fmt: skip
    return r


def _reprocess_status(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        counts: dict[str, int] = r.get("skipped") or {}
        skipped = sum(counts.values())
        when = "" if r["outcome"] == "running" else f" at {str(r.get('at', ''))[:16]}"
        failed: list[str] = r.get("failed") or []
        failing = f", failed: {', '.join(failed)}" if failed else ""
        typer.echo(f"{r['address_id']:<16} {r['run_id']}: {r['outcome']}{when},"
                   f" {r['reclassify']} sent to Claude, {r['replan']} planned again,"
                   f" {skipped} skipped{failing}")  # fmt: skip
    if not rows:
        typer.echo("no reprocess yet")
