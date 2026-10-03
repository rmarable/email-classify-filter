"""`ecf init --mode local [--resume]` and `ecf init status` (SPEC §13.1; V1.2 step 11c).

Every step is skipped when the service reports it done, so running `init` again resumes. Every
setup step goes through the service (tokens, app passwords and org domains are stored by it), so
the service unit is installed first when it isn't running: an unconfigured service idles
(§10a). V1.2 steps: checklist, service, disk-encryption and secret-store checks, install role,
Slack, first address (with org domains), then a final check that the unit is running. V1.3 adds
the model step: when an address uses preset A or B and the pinned model isn't ready, offer
`ecf models install` (starting ecf's Ollama login item first). V1.4 adds C with the local fallback
on to "uses the local model", a reminder for each B or C address whose fallback is off
(§4.3), and (step 11) Claude Code and the separate Claude login `ecf claude` needs, offered once
an address uses B or C (`ecf claude --login`). V1.5 step 13b adds, after the first address, the
alert-email question (off by default; OD-400) and backups (key, folder, first backup; OD-401); a
declined one is recorded by the service and `--resume` doesn't ask it again (OD-402).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf import claude_setup, cli_destroy, cli_export, doctor
from ecf.claude_setup import Login
from ecf.claude_wrapper import find_claude, layout
from ecf.cli_models import run_install
from ecf.client import LocalClient
from ecf.errors import EcfError
from ecf.paths import Paths
from ecf.prompts import require_terminal
from ecf.service_unit import ServiceManager, manager_for
from ecf.stepup import with_step_up

START_WAIT_S = 30.0
CHECKLIST = """Have ready:
  - a Slack workspace where you can click "Install to Workspace", and a Slack configuration
    token (api.slack.com/apps, Your App Configuration Tokens; it expires after 12 hours)
  - your Slack member ID (your profile, the ... menu, Copy member ID)
  - each mailbox's IMAP server and an app password for it
  - your organization's domains, and whether each mailbox is standard or high (finance)
  - whether this install is prod (your real mail) or test
  - for presets A and B, Ollama (macOS: brew install ollama && brew pin ollama mlx-c); ecf runs
    it from its own login item and downloads the pinned model (about 8 GB)
  - for presets B and C, Claude Code and a separate Claude login for `ecf claude`; Ollama too
    for C if you'll turn on the local fallback (claude_queue_timeout)
  - a password manager, for the backup key ecf shows once
  - optionally, an email address ecf doesn't watch, for alerts by email"""

AddAddress = Callable[[LocalClient, str, str, str | None, str | None, str | None], Any]


def make_commands(app: typer.Typer, paths: Callable[[], Paths], add: AddAddress) -> None:
    init_app = typer.Typer(
        invoke_without_command=True, help="Set up ecf on this computer (step by step)."
    )
    app.add_typer(init_app, name="init")

    @init_app.callback()
    def init(
        ctx: typer.Context,
        mode: Annotated[
            str, typer.Option("--mode", help="local (AWS mode arrives in M1).")
        ] = "local",
        resume: Annotated[
            bool, typer.Option("--resume", help="Continue without the checklist.")
        ] = False,
        restore: Annotated[
            str | None,
            typer.Option(
                "--restore", help="Set up from a backup of this install (a new computer)."
            ),
        ] = None,
    ) -> None:
        """Set up ecf: service, Slack, first mailbox, alert email, backups, local model. Safe to
        run again; done steps are skipped."""
        if ctx.invoked_subcommand is not None:
            return
        if mode != "local":
            raise typer.BadParameter(
                "use --mode local (AWS mode arrives in M1)", param_hint="--mode"
            )
        require_terminal()
        p = paths()
        _destroyed_before(p)
        if restore is not None:
            _from_backup(p, restore)
            return
        if not resume:
            typer.echo(CHECKLIST)
            if not typer.confirm("Ready?", default=True):
                raise typer.Exit(1)
        manager = manager_for(p)
        _service(p, manager)
        with LocalClient(p) as c:
            _checks(c)
            st: dict[str, Any] = c.get("/v1/init")
            _role(c, st)
            _slack(c, st)
            _first_address(c, add)
            _email(c, resume)
            _backups(c, resume)
            _models(c, p)
            _fallback(c)
            _claude(p, bool(c.get("/v1/init").get("claude_needed")))
        _unit(p, manager)
        typer.echo("Done for now. `ecf init status` shows each step; `ecf doctor` checks it all.")

    @init_app.command("status")
    def init_status() -> None:
        """Which setup steps are done."""
        p = paths()
        unit = manager_for(p).status()
        try:
            with LocalClient(p) as c:
                st = c.get("/v1/init")
        except EcfError as exc:
            typer.echo(f"service       not answering ({exc.detail}): ecf init --mode local")
            raise typer.Exit(3) from None
        login = claude_login(p) if st.get("claude_needed") else None
        for line in describe(st, installed=unit.installed, running=unit.running, login=login):
            typer.echo(line)


def describe(st: dict[str, Any], *, installed: bool, running: bool,
             login: Login | None = None) -> list[str]:  # fmt: skip
    def row(name: str, done: bool, text: str) -> str:
        return f"{name:<14}{'done' if done else 'to do':<7}{text}"

    role = st["install_role"]
    return [
        row("service", installed and running,
            "installed and running" if installed and running else "ecf init --mode local"),
        row("install role", role is not None, role or "asked by ecf init"),
        row("slack", st["slack_installed"] and st["slack_member"] is not None,
            f"member {st['slack_member']}" if st["slack_member"]
            else "ecf slack install" if not st["slack_installed"]
            else "click Confirm in the DM ecf sent you (to send it again: ecf slack"
            " set-member <your member ID>)"),
        row("org domains", bool(st["org_domains"]),
            ", ".join(st["org_domains"]) or "set with the first address"),
        row("first address", bool(st["addresses"]),
            ", ".join(st["addresses"]) or "ecf address add"),
        row("models", st["models"]["installed"] or not st["models"]["needed"],
            "installed (ecf models status)" if st["models"]["installed"]
            else "ecf models install" if st["models"]["needed"]
            else "not needed: no address uses preset A or B (or C with the local fallback)"),
        row("fallback", not st.get("fallback_off"),
            "off for " + ", ".join(st["fallback_off"]) + ": " + FALLBACK_HINT
            if st.get("fallback_off") else "on, or no address uses preset B or C"),
        row("claude", not st.get("claude_needed") or bool(login and login.logged_in),
            "not needed: no address uses preset B or C" if not st.get("claude_needed")
            else f"ecf's own configuration is {login.describe()}" if login and login.logged_in
            else "ecf claude --login (needs Claude Code; ecf doctor checks it)"),
        _email_row(st, row),
        _export_row(st, row),
    ]  # fmt: skip


Row = Callable[[str, bool, str], str]


def _email_row(st: dict[str, Any], row: Row) -> str:
    mail: dict[str, str] | None = st.get("alert_email")
    if mail:
        return row("alert email", True, f"from {mail['from']} to {mail['to']}")
    if when := st.get("skipped", {}).get("email"):
        return row("alert email", True, f"skipped {when[:10]} (off); later: {EMAIL_HINT}")
    return row("alert email", False, f"asked by ecf init (optional); or {EMAIL_HINT}")


def _export_row(st: dict[str, Any], row: Row) -> str:
    ex: dict[str, Any] = st["export"]
    if ex["schedule"] == "off":
        return row("export", True, "off (export_schedule: off; ecf config apply turns it on)")
    if ex["key"] and ex["dir"]:
        last = f"last {ex['last_ok'][:16].replace('T', ' ')} UTC" if ex["last_ok"] else "first due"
        return row("export", True, f"{ex['schedule']} to {ex['dir']}; {last}")
    todo = "ecf export dir set <directory>" if ex["key"] else EXPORT_HINT
    if when := st.get("skipped", {}).get("export"):
        return row("export", False, f"skipped {when[:10]}: no backups are made; {todo}")
    return row("export", False, todo)


def _destroyed_before(p: Paths) -> None:
    """OD-386: a destroy's record for this name warns; an unfinished destroy must finish first."""
    rec = cli_destroy.read_record(p)
    if rec is None:
        return
    if rec.get("phase") != cli_destroy.DONE:
        typer.echo(f"a destroy of {p.install} hasn't finished: run ecf destroy first", err=True)
        raise typer.Exit(1)
    typer.echo(f"Note: an install named {p.install} was destroyed here on"
               f" {str(rec.get('done_at', ''))[:10]} (record: {cli_destroy.record_path(p)});"
               " this one starts empty.")  # fmt: skip


def _from_backup(p: Paths, bundle: str) -> None:
    """`ecf init --restore <bundle>` (SPEC §11.9; OD-371): start the service, restore, then list
    what's left instead of running init's own steps."""
    from ecf.cli_import import restore  # noqa: PLC0415

    _service(p, manager_for(p))
    done = restore(p, str(Path(os.path.expanduser(bundle)).absolute()))
    typer.echo("\nLeft to do on this computer:")
    typer.echo("  1. ecf slack status (ecf slack set-tokens if Slack isn't connected)")
    for aid in done["addresses"]:
        typer.echo(f"  2. ecf address set {aid} --app-password, ecf check {aid}, then ecf resume"
                   f" {aid}")  # fmt: skip
    typer.echo("  3. ecf doctor")


def _service(p: Paths, manager: ServiceManager) -> None:
    if _answering(p):
        typer.echo("service: running")
        return
    typer.echo("service: starting it (it idles until set up)")
    if manager.status().installed:
        manager.start()  # also clears a tripped crash-loop breaker
    else:
        manager.install()
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        if _answering(p):
            return
        time.sleep(0.5)
    typer.echo("The service didn't answer within 30 s. See `ecf service status` and `ecf logs`.")
    raise typer.Exit(3)


def _answering(p: Paths) -> bool:
    try:
        with LocalClient(p) as c:
            c.get("/v1/health", auth=False)
    except EcfError:
        return False
    return True


def _checks(c: LocalClient) -> None:
    disk = doctor.check_disk_encryption()
    typer.echo(f"disk encryption: {disk.detail}")
    if disk.level is doctor.Level.FAIL:
        typer.echo(f"  {disk.fix}. Full-disk encryption is required: your mailbox passwords and"
                   " mail metadata are on this computer.")  # fmt: skip
        if not typer.confirm("Continue anyway?", default=False):
            raise typer.Exit(1)
    backend, detail = secret_store(c)
    if backend is None:
        typer.echo(f"secret store: unavailable ({detail}); see `ecf doctor`")
        raise typer.Exit(3)
    typer.echo(f"secret store: {backend}")


def secret_store(c: LocalClient) -> tuple[str | None, str]:
    """The service's secret-store backend, or None and why not."""
    store: dict[str, Any] = c.get("/v1/status")["secret_store"]
    backend = store.get("backend")
    usable = backend and not store.get("detail")  # a backend it couldn't open isn't usable
    return (str(backend) if usable else None), str(store.get("detail") or "unknown")


def _role(c: LocalClient, st: dict[str, Any]) -> None:
    if st["install_role"]:
        return
    role = ""
    while role not in ("prod", "test"):  # ask again rather than end init on a typo
        role = typer.prompt("Is this install prod (your real mail) or test?",
                            default="prod").strip().lower()  # fmt: skip
    c.request("POST", "/v1/init/role", {"install_role": role})
    typer.echo(f"install role: {role} (fixed from now on)")


def _slack(c: LocalClient, st: dict[str, Any]) -> None:
    from ecf.cli_slack import install_slack  # noqa: PLC0415 - only init needs it here

    if st["slack_installed"]:
        typer.echo("slack: installed" + ("" if st["slack_member"] else
                                         " (click Confirm in the DM ecf sent you)"))  # fmt: skip
        return
    if not typer.confirm("Connect Slack now? (needs the configuration token)", default=True):
        typer.echo("slack: skipped; later: ecf slack install")
        return
    install_slack(c)


def _first_address(c: LocalClient, add: AddAddress) -> None:
    if c.get("/v1/init")["addresses"]:
        typer.echo("first address: added")
        return
    if not typer.confirm("Add the first mailbox now?", default=True):
        typer.echo("first address: skipped; later: ecf address add")
        return
    email = typer.prompt("Mailbox address (e.g. ap@example.com)").strip()
    host = typer.prompt("Its IMAP server (port 993, TLS)").strip()
    add(c, email, host, None, None, None)


EMAIL_HINT = "ecf alerts email set --from <address> --to <destination>"
EXPORT_HINT = "ecf export keys rotate, then ecf export dir set <directory>"


def _skip(c: LocalClient, step: str) -> None:
    c.request("POST", "/v1/init/skip", {"step": step})


def _email(c: LocalClient, resume: bool) -> None:
    """Step 7 (OD-400): alert email, off by default."""
    st: dict[str, Any] = c.get("/v1/init")
    if mail := st.get("alert_email"):
        typer.echo(f"alert email: on, from {mail['from_email']} to {mail['to']}")
        return
    if resume and "email" in st.get("skipped", {}):
        typer.echo(f"alert email: skipped earlier; later: {EMAIL_HINT}")
        return
    senders: list[str] = st.get("smtp_addresses") or []
    if not senders:
        typer.echo(f"alert email: needs an address with an SMTP server first; later: {EMAIL_HINT}")
        return
    if not typer.confirm("Also send alerts by email? (to an address ecf doesn't watch)",
                         default=False):  # fmt: skip
        _skip(c, "email")
        typer.echo(f"alert email: off; later: {EMAIL_HINT}")
        return
    frm = senders[0]
    if len(senders) > 1:
        frm = ""
        while frm not in senders:
            frm = typer.prompt(f"Send them from which address ({', '.join(senders)})",
                               default=senders[0]).strip()  # fmt: skip
    to = typer.prompt("Send them to (an address ecf doesn't watch)").strip()
    body: dict[str, Any] = {"from": frm, "to": to}
    try:
        r = with_step_up(c, lambda n: c.request("POST", "/v1/alerts/email",
                                                body | {"nonce_id": n}),
                         echo=typer.echo)  # fmt: skip
    except EcfError as exc:
        typer.echo(f"alert email: not turned on ({exc.detail}); later: {EMAIL_HINT}")
        return
    typer.echo(f"alert email: on, from {r['email']['from_email']} to {r['email']['to']}; a test"
               " email is on its way")  # fmt: skip


def _backups(c: LocalClient, resume: bool) -> None:
    """Step 8 (OD-401): the backup key, the folder, a first backup."""
    st: dict[str, Any] = c.get("/v1/init")
    ex: dict[str, Any] = st["export"]
    if ex["schedule"] == "off":
        typer.echo("backups: off (export_schedule: off; ecf config apply turns them on)")
        return
    if ex["key"] and ex["dir"]:
        typer.echo(f"backups: {ex['schedule']} to {ex['dir']}")
        return
    if resume and "export" in st.get("skipped", {}):
        typer.echo(f"backups: skipped earlier, so none are made; later: {EXPORT_HINT}")
        return
    if not typer.confirm(f"Set up {ex['schedule']} backups now? (recommended)", default=True):
        _skip(c, "export")
        typer.echo(f"backups: skipped, so none are made; later: {EXPORT_HINT}")
        return
    try:
        typed = None
        if not ex["key"]:
            typed = cli_export.new_key(c)["key"]["fingerprint"]
        where = _ask_folder()
        cli_export.set_dir(c, where, typed)
    except EcfError as exc:
        typer.echo(f"backups: not set up ({exc.detail}); later: {EXPORT_HINT}")
        return
    if typer.confirm("Make the first backup now?", default=True):
        try:
            r: dict[str, Any] = c.request("POST", "/v1/export/now", timeout=600)
        except EcfError as exc:
            r = {"ok": False, "error": exc.detail}
        if r["ok"]:
            typer.echo(f"backups: first written to {Path(r['dir']) / r['file']}")
        else:
            typer.echo(f"backups: the first failed ({r['error']}); ecf export now tries again")


def folder_suggestions(home: Path | None = None, volumes: Path = Path("/Volumes"),
                       platform: str = sys.platform) -> list[Path]:  # fmt: skip
    """Places off this disk for backups (OD-401): iCloud Drive and File Provider folders
    (Dropbox, OneDrive, Google Drive) and mounted volumes on macOS; removable media on Linux."""
    h = home or Path.home()
    bases: list[Path] = []
    if platform == "darwin":
        bases.append(h / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
        bases += _children(h / "Library" / "CloudStorage")
        bases += [v for v in _children(volumes) if not _is_root(v)]
    else:
        user = h.name
        bases += _children(Path("/media") / user) + _children(Path("/run/media") / user)
    return [b / "ecf-backups" for b in bases if b.is_dir()]


def _children(d: Path) -> list[Path]:
    try:
        return sorted(x for x in d.iterdir() if x.is_dir())
    except OSError:
        return []


def _is_root(p: Path) -> bool:
    try:
        return p.resolve() == Path("/")
    except OSError:
        return True


def _ask_folder() -> str:
    hints = folder_suggestions()
    if hints:
        typer.echo("Backups are safest off this disk. Folders found:")
        for i, h in enumerate(hints, 1):
            typer.echo(f"  {i}. {h}")
    while True:
        got = typer.prompt("Backup folder (a number above, or a full path)" if hints
                           else "Backup folder (a full path; an external disk or a synced"
                           " folder is safest)").strip()  # fmt: skip
        if got.isdigit() and 1 <= int(got) <= len(hints):
            where = hints[int(got) - 1]
        else:
            where = Path(os.path.expanduser(got))
            if not where.is_absolute():
                typer.echo("give the full path")
                continue
        if where.is_dir():
            return str(where)
        if typer.confirm(f"Create {where}?", default=True):
            try:
                where.mkdir(mode=0o700, parents=True)
            except OSError as exc:
                typer.echo(f"can't create it: {exc.strerror or exc}")
                continue
            return str(where)


def model_ready(c: LocalClient) -> bool:
    return bool(c.get("/v1/models")["ready"])


def _models(c: LocalClient, p: Paths) -> None:
    if not c.get("/v1/init")["models"]["needed"]:
        typer.echo("models: not needed yet (no address uses preset A or B, or C with the local"
                   " fallback)")  # fmt: skip
        return
    if model_ready(c):
        typer.echo("models: installed and ready")
        return
    if not typer.confirm("Install the local model now? (about 8 GB to download)", default=True):
        typer.echo("models: skipped; later: ecf models install")
        return
    try:
        ok = run_install(c, p.root)
    except EcfError as exc:  # e.g. Ollama isn't installed: the message says how
        typer.echo(f"models: {exc.detail}; then: ecf models install")
        return
    if not ok:
        typer.echo("models: not installed; try again with: ecf models install")


FALLBACK_HINT = "ecf settings set claude_queue_timeout <hours> --address <address>"


def _fallback(c: LocalClient) -> None:
    """A reminder for B and C addresses whose local fallback is off (§4.3); nothing to ask."""
    off: list[str] = c.get("/v1/init").get("fallback_off") or []
    if off:
        typer.echo(f"local fallback: off for {', '.join(off)}, so their mail waits for /ecf-review"
                   f" however long it takes; to turn it on: {FALLBACK_HINT}")  # fmt: skip


def claude_login(p: Paths) -> Login | None:
    """ecf's own Claude login; None when Claude Code is missing or too old, or didn't say."""
    try:
        claude = find_claude()
    except EcfError:
        return None
    return claude_setup.auth_status(claude, layout(p))


def _claude(p: Paths, needed: bool) -> None:
    if not needed:
        typer.echo("claude: not needed yet (no address uses preset B or C)")
        return
    try:
        claude = find_claude()
    except EcfError as exc:
        typer.echo(f"claude: {exc.detail}; install or update it, then: ecf claude --login")
        return
    login = claude_setup.auth_status(claude, layout(p))
    if login is not None and login.logged_in:
        typer.echo(f"claude: ecf's own configuration is {login.describe()}")
        return
    if not typer.confirm("Log in to Claude for `ecf claude` now? (its own login, separate from"
                         " your usual Claude Code)", default=True):  # fmt: skip
        typer.echo("claude: skipped; later: ecf claude --login")
        return
    claude_setup.run_login(p, echo=typer.echo)


def _unit(p: Paths, manager: ServiceManager) -> None:
    s = manager.status()
    if s.installed and s.running:
        typer.echo("service unit: installed and running")
        return
    if _answering(p):  # e.g. `ecf watch`: a second copy would only fail on the instance lock
        typer.echo("service unit: the service is running outside its unit (ecf watch?); run"
                   " `ecf service install` once it stops")  # fmt: skip
        return
    manager.install()
    typer.echo("service unit: installed")
