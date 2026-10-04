"""`ecf export keys show|rotate` and `ecf export dir set` (SPEC §11.9; V1.5 step 8a); `ecf export
status|now` for scheduled export (step 8b); manual `ecf export --to <file>` with a passphrase
(step 9a)."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.prompts import hidden
from ecf.stepup import with_step_up

DIR_HELP = "An existing folder, ideally on another disk or a synced folder."
EXPORT_HELP = "Backups: the key, where they go, and a manual export."
TO_HELP = "A new .ecfb file, or an existing folder to put one in."


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    export_app = typer.Typer(invoke_without_command=True, help=EXPORT_HELP)
    app.add_typer(export_app, name="export")

    @export_app.callback()
    def export_manual(
        ctx: typer.Context,
        to: Annotated[str | None, typer.Option("--to", help=TO_HELP)] = None,
    ) -> None:
        """With --to: export all of ecf's data to one passphrase-protected file. (step-up)"""
        if ctx.invoked_subcommand is not None:
            if to is not None:
                raise typer.BadParameter("--to goes alone: ecf export --to <file>")
            return
        if to is None:
            typer.echo(ctx.get_help())
            raise typer.Exit(0)
        manual(paths(), to)

    _key_commands(export_app, paths)
    dir_app = typer.Typer(no_args_is_help=True, help="Where backups go (export_dir).")
    export_app.add_typer(dir_app, name="dir")

    @export_app.command("status")
    def export_status() -> None:
        """The schedule, the last backup, failures, the key and where backups go."""
        with LocalClient(paths()) as c:
            r = c.get("/v1/export")
        _print_schedule(r)
        _print(r)

    @export_app.command("now")
    def export_now() -> None:
        """Make a scheduled backup now (as the schedule would; kept and pruned with them)."""
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/export/now", timeout=600)
        if not r["ok"]:
            typer.echo(f"backup failed: {r['error']}", err=True)
            raise typer.Exit(1)
        typer.echo(f"backup written: {Path(r['dir']) / r['file']} ({_size(r['bytes'])})")
        if r["pruned"]:
            typer.echo(f"removed {len(r['pruned'])} older backup(s)")

    @dir_app.command("set")
    def dir_set(
        directory: Annotated[str, typer.Argument(help=DIR_HELP)],
    ) -> None:
        """Choose where backups go. (step-up)"""
        where = str(Path(os.path.expanduser(directory)).absolute())
        with LocalClient(paths()) as c:
            set_dir(c, where)


def _key_commands(export_app: typer.Typer, paths: Callable[[], Paths]) -> None:
    keys_app = typer.Typer(no_args_is_help=True, help="The backup key.")
    export_app.add_typer(keys_app, name="keys")

    @keys_app.command("show")
    def keys_show() -> None:
        """The backup key's fingerprint and where backups go."""
        with LocalClient(paths()) as c:
            _print(c.get("/v1/export"))

    @keys_app.command("rotate")
    def keys_rotate() -> None:
        """Make a new backup key (the first one too). ecf shows it once, for your password
        manager. (step-up)"""
        with LocalClient(paths()) as c:
            old = c.get("/v1/export")["key"]
            if old is not None:
                typer.echo(f"This replaces backup key {old['fingerprint']}. Bundles made before"
                           " now can then be restored only with the old key: keep it until"
                           " they're gone.")  # fmt: skip
                if not typer.confirm("Make a new key?", default=False):
                    raise typer.Exit(1)
            got = new_key(c)
        if got["dir"] is None:
            typer.echo("next: ecf export dir set <directory>")


def new_key(c: LocalClient) -> dict[str, Any]:
    """Make a backup key and put it in use: shown once, `saved` and the fingerprint typed, then
    step-up (`ecf export keys rotate` and `ecf init`). Returns the service's export state."""
    r = c.request("POST", "/v1/export/keys/new")
    typer.echo("\nYour backup key (ecf shows it only this once and doesn't keep it):\n")
    typer.echo(f"    {r['key_text']}\n")
    typer.echo(f"Fingerprint: {r['fingerprint']}\n")
    typer.echo("Save the key in your password manager. Restoring a backup needs it; without"
               " it, nobody can read your backups, you included.")  # fmt: skip
    while typer.prompt("Type 'saved' once it's saved").strip().lower() != "saved":
        pass
    typed = _typed_fingerprint(r["fingerprint"])
    body: dict[str, Any] = {"pending_id": r["pending_id"], "fingerprint": typed}
    got: dict[str, Any] = with_step_up(c, lambda n: c.request("POST", "/v1/export/keys",
                                                              body | {"nonce_id": n}),
                                       echo=typer.echo)  # fmt: skip
    typer.echo(f"backup key {got['key']['fingerprint']} in use (generation"
               f" {got['key']['generation']})")  # fmt: skip
    return got


def set_dir(c: LocalClient, where: str, fingerprint: str | None = None) -> dict[str, Any]:
    """Point backups at `where` (absolute): the key's fingerprint typed (or the one just typed for
    a new key, `ecf init`), then step-up. Returns the service's export state."""
    key = c.get("/v1/export")["key"]
    if key is None:
        typer.echo("make the backup key first: ecf export keys rotate", err=True)
        raise typer.Exit(1)
    typed = fingerprint or typer.prompt(f"Type the backup key's fingerprint ({key['fingerprint']})")
    body: dict[str, Any] = {"path": where, "fingerprint": typed}
    got: dict[str, Any] = with_step_up(c, lambda n: c.request("POST", "/v1/export/dir",
                                                              body | {"nonce_id": n}),
                                       echo=typer.echo)  # fmt: skip
    typer.echo(f"backups go to {got['dir']}")
    if got["same_volume"]:
        typer.echo(_SAME_VOLUME, err=True)
    return got


_SAME_VOLUME = ("warning: that folder is on the same disk as ecf's data; a backup there won't"
                " survive the disk failing. An external disk or a synced folder (iCloud Drive,"
                " Dropbox) is safer.")  # fmt: skip


def _typed_fingerprint(fp: str, tries: int = 3) -> str:
    """Ask for the new key's fingerprint, so a key nobody saw isn't put in use; the service checks
    it again."""
    for _ in range(tries):
        typed = typer.prompt("Type the fingerprint shown above")
        if _plain(typed) == _plain(fp):
            return typed
        typer.echo("that's not the fingerprint shown above")
    typer.echo("the new key wasn't put in use; run `ecf export keys rotate` again", err=True)
    raise typer.Exit(1)


def _plain(s: str) -> str:
    return "".join(s.split()).replace("-", "").upper()


def manual(paths: Paths, to: str) -> None:
    """`ecf export --to` (also offered by `ecf destroy`, OD-389)."""
    where = Path(os.path.expanduser(to)).absolute()
    if where.is_dir():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        where = where / f"ecf-{paths.install}-manual-{stamp}.ecfb"
    elif where.suffix != ".ecfb":
        where = where.with_name(where.name + ".ecfb")
    with LocalClient(paths) as c:
        offered = c.get("/v1/export/passphrase")["passphrase"]
        typer.echo("\nA passphrase for this export (ecf doesn't keep it; without it nobody can read"
                   " the file):\n")  # fmt: skip
        typer.echo(f"    {offered}\n")
        if typer.confirm("Use this passphrase? (No: type your own)", default=True):
            secret = offered
            while typer.prompt("Type 'saved' once it's saved").strip().lower() != "saved":
                pass
        else:
            typer.echo("At least 20 characters or 5 different words.")
            secret = hidden("Passphrase (hidden): ", confirm=True)
        body: dict[str, Any] = {"path": str(where), "passphrase": secret}
        r = with_step_up(c, lambda n: c.request("POST", "/v1/export", body | {"nonce_id": n},
                                                timeout=600),
                         echo=typer.echo)  # fmt: skip
    typer.echo(f"exported to {r['path']} ({_size(r['bytes'])}"
               + ("" if r["signed"] else "; unsigned: no backup key yet") + ")")  # fmt: skip


def _size(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(n // 1024, 1)} KB"


def _print_schedule(r: dict[str, Any]) -> None:
    typer.echo(f"schedule: {r['schedule']} (keeps the newest {r['keep']})")
    last = r["last_ok"]
    if last is None:
        typer.echo("last backup: none yet")
    else:
        typer.echo(f"last backup: {last['at'][:16].replace('T', ' ')} UTC, {last['file']}"
                   f" ({_size(last['bytes'])})")  # fmt: skip
    if r["failures"]:
        err: dict[str, Any] = r["last_error"] or {}
        typer.echo(f"failing: {r['failures']} tries in a row; last: {err.get('why')}")
    if not r["set_up"]:
        typer.echo("not set up: ecf export keys rotate, then ecf export dir set <directory>")


def _print(r: dict[str, Any]) -> None:
    key = r["key"]
    if key is None:
        typer.echo("backup key: none (ecf export keys rotate)")
    else:
        typer.echo(f"backup key: {key['fingerprint']} (generation {key['generation']}, made"
                   f" {key['created_at'][:16].replace('T', ' ')} UTC)")  # fmt: skip
        if r["previous"]:
            typer.echo(f"  earlier keys: {r['previous']} (older bundles need them)")
    if r["dir"] is None:
        typer.echo("backups go to: not set (ecf export dir set <directory>)")
    else:
        typer.echo(f"backups go to: {r['dir']}")
        if r["same_volume"]:
            typer.echo(_SAME_VOLUME)
        elif r["same_volume"] is None:
            typer.echo("  warning: ecf can't read that folder now")
