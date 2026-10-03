"""`ecf import <bundle> [--dry-run] [--replace]` (SPEC §11.9; OD-352 to OD-361; V1.5 steps 9b and
9c) and `ecf restore <bundle>` (OD-362 to OD-373; step 10a). Each shows its preview first, then
needs yeses and step-up."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.prompts import hidden
from ecf.stepup import with_step_up

SIGNERS = {
    "own": "signed by this install's backup key",
    "typed_key": "signed by the backup key you typed",
    "unknown": "signed by a key ecf doesn't know (treated as foreign)",
    "unsigned": "unsigned (treated as foreign)",
}


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("import")
    def import_command(
        bundle: Annotated[str, typer.Argument(help="An .ecfb file from ecf export.")],
        dry_run: Annotated[
            bool, typer.Option("--dry-run", help="Only show what would come in.")
        ] = False,
        replace: Annotated[
            bool, typer.Option("--replace", help="Replace this install's addresses and emails.")
        ] = False,
    ) -> None:
        """Bring another install's data in from a bundle (or a backup of this one). Addresses
        arrive paused. (step-up)"""
        path = str(Path(os.path.expanduser(bundle)).absolute())
        with LocalClient(paths()) as c:
            head = c.request("POST", "/v1/import/inspect", {"path": path})
            _print_head(head)
            if head["kind"] == "scheduled":
                secret = hidden("Backup key of the install that made it (hidden): ")
            else:
                secret = hidden("Passphrase (hidden): ")
            body: dict[str, Any] = {"path": path, "secret": secret, "dry_run": True}
            r = c.request("POST", "/v1/import", body, timeout=600)
            _print_preview(r)
            if dry_run:
                return
            if not r["target_empty"] and not replace:
                typer.echo("this install has data: add --replace to replace it", err=True)
                raise typer.Exit(1)
            if not typer.confirm("Import this?", default=False):
                raise typer.Exit(1)
            body = {"path": path, "secret": secret, "replace": replace}
            if replace:
                body["install"] = typer.prompt("Type this install's name to replace its data")
            done = with_step_up(c, lambda n: c.request("POST", "/v1/import",
                                                       body | {"nonce_id": n}, timeout=600),
                                echo=typer.echo)  # fmt: skip
        n = done["counts"]
        typer.echo(f"imported {n.get('addresses', 0)} address(es) and {n.get('items', 0)} emails;"
                   f" {n.get('reposted', 0)} approval(s) to decide again")  # fmt: skip
        if done["config_changes"]:
            typer.echo("config changed: " + "; ".join(
                f"{ch['section']}: {ch['change']}" for ch in done["config_changes"]))  # fmt: skip
        typer.echo("next: ecf address set <address> --app-password, reconnect Slack (ecf slack"
                   " set-tokens), then ecf resume <address> for each")  # fmt: skip

    @app.command("restore")
    def restore_command(
        bundle: Annotated[str, typer.Argument(help="A backup (.ecfb) of this install.")],
    ) -> None:
        """Recover this install from one of its backups (also on a new computer). Addresses
        arrive paused at their previous stage. (step-up)"""
        restore(paths(), str(Path(os.path.expanduser(bundle)).absolute()))


def restore(paths: Paths, path: str) -> dict[str, Any]:
    """`ecf restore`, and `ecf init --restore` after it starts the service (step 10b)."""
    with LocalClient(paths) as c:
        head = c.request("POST", "/v1/import/inspect", {"path": path})
        _print_head(head)
        body: dict[str, Any] = {"path": path}
        if head["kind"] == "scheduled":
            body["secret"] = hidden("Backup key (hidden): ")
        else:
            body["secret"] = hidden("Passphrase (hidden): ")
            body["backup_key"] = hidden("Backup key (hidden; it signed this export): ")
        r = c.request("POST", "/v1/restore", body | {"dry_run": True}, timeout=600)
        _print_restore(r)
        if r["older"] and not typer.confirm(
            f"This backup (seq {r['seq']}) is older than your newest (seq"
            f" {r['newest_seq_here']}). Go back to it anyway?", default=False):  # fmt: skip
            raise typer.Exit(1)
        typer.echo("If ecf still runs on another computer with this install, stop it first:"
                   " two copies would act on the same mail.")  # fmt: skip
        if not typer.confirm("Is ecf stopped everywhere else (or is this the same computer)?",
                             default=False):  # fmt: skip
            raise typer.Exit(1)
        if not typer.confirm("Restore?", default=False):
            raise typer.Exit(1)
        body |= {"stopped": True, "older_ok": bool(r["older"])}
        done = with_step_up(c, lambda n: c.request("POST", "/v1/restore", body | {"nonce_id": n},
                                                   timeout=600),
                            echo=typer.echo)  # fmt: skip
    n = done["counts"]
    typer.echo(f"restored {n.get('addresses', 0)} address(es) and {n.get('items', 0)} emails;"
               f" generation {done['generation']}; {n.get('reposted', 0)} approval(s) to decide"
               " again")  # fmt: skip
    if done["safety_copy"]:
        typer.echo(f"the data from before is kept for 7 days: {done['safety_copy']}")
    typer.echo("next, for each address: ecf address set <address> --app-password, ecf check"
               " <address>, then ecf resume <address>; Slack: ecf slack status (ecf slack"
               " set-tokens if it isn't connected)")  # fmt: skip
    return done


def _print_restore(r: dict[str, Any]) -> None:
    src = r["source"]
    when = str(src.get("created_at") or "?")[:16].replace("T", " ")
    new = "; this install is empty (a new computer)" if r["empty_target"] else ""
    typer.echo(f"backup of {src.get('install')} ({src.get('install_id')}) from {when} UTC,"
               f" seq {src.get('seq')}{new}")  # fmt: skip
    typer.echo("rows: " + ", ".join(f"{k} {n}" for k, n in r["counts"].items() if n))
    for a in r["addresses"]:
        typer.echo(f"  address {a['address_id']} ({a['email']}): paused until resumed, stage"
                   f" {a['stage']}, outbound {'on' if a['outbound'] else 'off'}")  # fmt: skip
    for ch in r["config_changes"]:
        typer.echo(f"  config {ch['section']}: {ch['change']}")
    sd = r["senders"]
    if any(sd.values()):
        typer.echo(f"  sender records: {sd['added']} added, {sd['removed']} removed,"
                   f" {sd['changed']} changed")  # fmt: skip


def _print_head(h: dict[str, Any]) -> None:
    when = str(h.get("created_at") or "?")[:16].replace("T", " ")
    typer.echo(f"{h['kind']} bundle from install {h.get('install_id')}, made {when} UTC"
               f" (seq {h.get('seq')}, schema {h.get('schema_version')})")  # fmt: skip
    if h["claims_this_install"] and not h["own"]:
        typer.echo("warning: it names this install but isn't signed by this install's key")


def _print_preview(r: dict[str, Any]) -> None:
    src = r["source"]
    signer = SIGNERS.get(r["signer"], r["signer"])
    typer.echo(f"source: {src.get('install')} ({src.get('install_id')}), ecf"
               f" {src.get('product_version')}; {signer}")  # fmt: skip
    typer.echo("rows: " + ", ".join(f"{k} {n}" for k, n in r["counts"].items() if n))
    for a in r["addresses"]:
        typer.echo(f"  address {a['address_id']} ({a['email']}): arrives paused, stage"
                   f" {a['stage']}, outbound off")  # fmt: skip
    typer.echo(f"re-posted for a fresh decision: {r['reposted']}; marked failed_unknown:"
               f" {r['failed_unknown']}"
               + (f"; needs_human (nothing to approve): {r['needs_human']}"
                  if r.get("needs_human") else ""))  # fmt: skip
    typer.echo(f"settings: {r['settings_kept']} kept, {r['settings_dropped']} dropped; Slack"
               f" routes dropped: {r['routes_dropped']}")  # fmt: skip
    typer.echo("not carried over:")
    for line in r["not_carried"]:
        typer.echo(f"  - {line}")
    if not r["target_empty"]:
        typer.echo("this install isn't empty: importing needs --replace")
