"""`ecf corpus fetch|status|stop|info` (SPEC §16.7; OD-466 to OD-468): a real-mail test corpus,
read from a mailbox you own into one encrypted file. The service does the work; this asks for
what it needs before step-up (R33), shows the passphrase once, and follows the run."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf.prompts import hidden, require_terminal
from ecf.stepup import with_step_up
from ecf.text import one_line

POLL_S = 2.0
PREFLIGHT_TIMEOUT_S = 300.0
MIB = 1024 * 1024
NO_STDOUT = (
    "this command shows a passphrase or mail text and needs its output on a terminal, not a file"
    " or a pipe; run it in Terminal"
)
INFO_KEYS = ("corpus_id", "created_at", "ecf_version", "source_domain", "folder", "order", "count",
             "bytes", "complete", "reason", "skipped", "source_preset")  # fmt: skip
ORDER_HELP = "most-recent (default), oldest or random."
FOLDER_HELP = "inbox, all-mail, or a folder's name (default: All Mail on Gmail, else INBOX)."


def require_tty_output() -> None:
    """R184: `> file` or `| tee` would capture the passphrase or excerpts."""
    if not sys.stdout.isatty():
        raise InvalidInputError(NO_STDOUT)


def make_corpus_app(paths: Callable[[], Paths]) -> typer.Typer:
    corpus_app = typer.Typer(
        no_args_is_help=True, help="A real-mail test corpus from a mailbox you own (SPEC §16.7)."
    )

    @corpus_app.command("fetch")
    def fetch(  # noqa: PLR0913, PLR0917 - the command's options
        out: Annotated[str, typer.Option("--out", help="A new .ecfcorpus file.")],
        total: Annotated[int, typer.Option(help="How many messages (max 5000).")] = 500,
        chunk: Annotated[int, typer.Option(help="Messages between pauses (1-100).")] = 10,
        sleep: Annotated[int, typer.Option(help="Seconds between chunks (0-600).")] = 10,
        max_mib: Annotated[int, typer.Option(help="MiB of mail at most (max 512).")] = 256,
        order: Annotated[str, typer.Option(help=ORDER_HELP)] = "most-recent",
        folder: Annotated[str, typer.Option(help=FOLDER_HELP)] = "default",
        address: Annotated[str | None, typer.Option(help="A watched address.")] = None,
        include_own: Annotated[bool, typer.Option(help="Keep drafts, sent mail, ecf's.")] = False,
        allow_spam: Annotated[bool, typer.Option(help="Allow Spam or Trash.")] = False,
        own_passphrase: Annotated[bool, typer.Option(help="Type your own passphrase.")] = False,
    ) -> None:
        """Copy real mail from a mailbox you own into one encrypted file. (step-up)"""
        require_terminal()
        require_tty_output()
        where = str(Path(out).expanduser().absolute())
        body: dict[str, Any] = {"out": where, "total": total, "chunk": chunk, "sleep_s": sleep,
                                "max_bytes": max_mib * MIB, "order": order, "folder": folder,
                                "include_own": include_own, "allow_spam": allow_spam}  # fmt: skip
        if address:
            body["address"] = address
            email = address
        else:
            email = typer.prompt("The mailbox's email address").strip()
            gmail = email.lower().endswith(("@gmail.com", "@googlemail.com"))
            host_default = "imap.gmail.com" if gmail else None
            body |= {"email": email, "host": typer.prompt("IMAP host", default=host_default),
                     "app_password": hidden("App password (hidden; never stored): ")}  # fmt: skip
        body["owner_email"] = typer.prompt(
            f"To confirm {email} is a mailbox you own, type its email address"
            f"{' again' if not address else ''} (ecf can't check ownership)"
        ).strip()
        if own_passphrase:
            body["passphrase"] = hidden("Your passphrase for the file (hidden): ", confirm=True)
        with LocalClient(paths()) as c:
            pre: dict[str, Any] = c.request("POST", "/v1/corpus/preflight", body,
                                            timeout=PREFLIGHT_TIMEOUT_S)  # fmt: skip
            _print_preflight(pre, total)
            if not typer.confirm("Fetch?", default=False):
                raise typer.Exit(1)
            typer.echo(f"The corpus will be written to {where}")
            body["expect_folder"] = pre["folder"]
            got: dict[str, Any] = with_step_up(
                c,
                lambda n: c.request("POST", "/v1/corpus/fetch", body | {"nonce_id": n}),
                echo=typer.echo,
            )
            if got.get("passphrase"):
                _show_passphrase(str(got["passphrase"]))
            _follow(c)

    _merge_command(corpus_app, paths)

    @corpus_app.command("status")
    def status() -> None:
        """The corpus fetch in progress, or the last one."""
        with LocalClient(paths()) as c:
            _print_status(c.get("/v1/corpus"))

    @corpus_app.command("stop")
    def stop() -> None:
        """Stop the running fetch; what it has fetched is written, marked incomplete."""
        with LocalClient(paths()) as c:
            _print_status(c.request("POST", "/v1/corpus/stop"))

    @corpus_app.command("info")
    def info(file: Annotated[str, typer.Argument(help="An .ecfcorpus file.")]) -> None:
        """What a corpus file says about itself (unverified until opened with its passphrase)."""
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/corpus/info", {"path": str(Path(file).expanduser())})
        h = r["header"]
        typer.echo("header (unverified until the file is opened with its passphrase):")
        for key in INFO_KEYS:
            typer.echo(f"  {key}: {one_line(str(h.get(key)), 200)}")
        typer.echo(f"labels beside it: {r['labels']}")

    return corpus_app


def _merge_command(corpus_app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @corpus_app.command("merge")
    def merge(
        files: Annotated[
            list[str], typer.Argument(help="Two or more .ecfcorpus files.", metavar="FILE...")
        ],
        out: Annotated[str, typer.Option("--out", help="A new .ecfcorpus file.")],
        allow_mixed: Annotated[bool, typer.Option(help="Allow different mailboxes.")] = False,
        own_passphrase: Annotated[bool, typer.Option(help="Type your own passphrase.")] = False,
    ) -> None:
        """Merge corpora into a new one (a top-up): duplicates dropped, labels merged. (step-up)"""
        require_terminal()
        require_tty_output()
        sources = [{"path": str(Path(f).expanduser().absolute()),
                    "passphrase": hidden(f"Passphrase for {Path(f).name} (hidden): ")}
                   for f in files]  # fmt: skip
        body: dict[str, Any] = {"sources": sources, "allow_mixed": allow_mixed,
                                "out": str(Path(out).expanduser().absolute())}  # fmt: skip
        if own_passphrase:
            body["passphrase"] = hidden("Your passphrase for the new file (hidden): ",
                                        confirm=True)  # fmt: skip
        with LocalClient(paths()) as c:
            got: dict[str, Any] = with_step_up(
                c, lambda n: c.request("POST", "/v1/corpus/merge", body | {"nonce_id": n},
                                       timeout=PREFLIGHT_TIMEOUT_S),
                echo=typer.echo,
            )  # fmt: skip
        typer.echo(f"written: {got['out']} ({got['count']} messages; {got['labels']} labels)")
        if got.get("passphrase"):
            _show_passphrase(str(got["passphrase"]))


def _print_preflight(pre: dict[str, Any], total: int) -> None:
    typer.echo(f"folder: {pre['folder']} ({pre['exists']} messages)")
    typer.echo(f"first window: {pre['candidates']} candidates; {pre['fit_in_cap']} of the first"
               f" {total} fit within {pre['byte_cap'] / MIB:.0f} MiB")  # fmt: skip
    if pre["budget_share"] is not None:
        typer.echo(f"Gmail: this run uses at most {pre['budget_share'] / MIB:.0f} MiB, half of"
                   " today's download budget left; live checks of this mailbox may then stop on"
                   " the budget for up to a day")  # fmt: skip
    minutes = pre["estimate_s"] / 60
    typer.echo(f"{pre['batches']} batches, about {minutes:.0f} min; keep the Mac on AC power and"
               " awake (a sleep or a service restart ends the run)")  # fmt: skip


def _show_passphrase(secret: str) -> None:
    typer.echo("\nThe corpus passphrase (ecf shows it only this once and doesn't keep it):\n")
    typer.echo(f"    {secret}\n")
    typer.echo("Save it in your password manager only, never in a notes, state or plan file."
               " Without it nobody can open the corpus, you included.")  # fmt: skip
    while typer.prompt("Type 'saved' once it's saved").strip().lower() != "saved":
        pass


def _follow(c: LocalClient) -> None:
    last = ""
    try:
        while True:
            st: dict[str, Any] = c.get("/v1/corpus")
            line = f"{st['state']}: {st['fetched']}/{st['total']} messages"
            if line != last:
                typer.echo(line)
                last = line
            if st["state"] != "running":
                _print_status(st)
                if st["state"] == "failed":
                    raise typer.Exit(1)
                return
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        typer.echo("\nstill running in the service; `ecf corpus status`, or `ecf corpus stop`")
        raise typer.Exit(130) from None


def _print_status(st: dict[str, Any]) -> None:
    typer.echo(f"state: {st['state']}; fetched {st['fetched']} of {st['total']};"
               f" {st['bytes'] / MIB:.1f} MiB")  # fmt: skip
    if st["skipped"]:
        typer.echo("skipped: " + ", ".join(f"{k} {v}" for k, v in sorted(st["skipped"].items())))
    if st["reason"]:
        typer.echo(f"reason: {st['reason']}")
    if st["out"]:
        typer.echo(f"written: {st['out']}")
