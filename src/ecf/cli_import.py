"""`ecf import <bundle> --dry-run` (SPEC §11.9; OD-352 to OD-356; V1.5 step 9b). Applying an import
arrives in step 9c."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.prompts import hidden

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
    ) -> None:
        """Bring another install's data in from a bundle. Now: --dry-run only."""
        path = str(Path(os.path.expanduser(bundle)).absolute())
        with LocalClient(paths()) as c:
            head = c.request("POST", "/v1/import/inspect", {"path": path})
            _print_head(head)
            if head["kind"] == "scheduled":
                secret = hidden("Backup key of the install that made it (hidden): ")
            else:
                secret = hidden("Passphrase (hidden): ")
            body: dict[str, Any] = {"path": path, "secret": secret, "dry_run": dry_run}
            r = c.request("POST", "/v1/import", body, timeout=600)
        _print_preview(r)


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
               f" {r['failed_unknown']}")  # fmt: skip
    typer.echo(f"settings: {r['settings_kept']} kept, {r['settings_dropped']} dropped; Slack"
               f" routes dropped: {r['routes_dropped']}")  # fmt: skip
    typer.echo("not carried over:")
    for line in r["not_carried"]:
        typer.echo(f"  - {line}")
    if not r["target_empty"]:
        typer.echo("this install isn't empty: a real import would need --replace")
