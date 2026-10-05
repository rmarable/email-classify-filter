"""`ecf destroy` (SPEC §11.11; OD-383 to OD-393; V1.5 step 12b): the CLI's part.

With the service running: refuse while it's busy, `ecf watch` runs or an upgrade hasn't settled
(OD-388); show what goes; offer an export when none was made in the last 24 hours (OD-389); ask
for the install name; step-up; `POST /v1/destroy` does the service's part (§11.11, step 12a) and
the service stops. When the service can't run, `ecf-server destroy` does that part in the
foreground (OD-387). Then: remove the service unit, sign `ecf claude` out of its own config
(`claude auth logout`; a failure is residue only, OD-390), delete the data directory, mark the
record `<data root>/destroyed/<install>.json` done and print what's left to do by hand. Shared
things (Ollama's folder, models and login item, the `uv tool` package) are left; removing them is
suggested only when no other install is left (OD-391). Run again, it carries on from the record.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from ecf import upgrade_run, watch
from ecf.claude_wrapper import base_env, layout
from ecf.client import LocalClient
from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths
from ecf.prompts import hidden, require_terminal
from ecf.service_unit import STOP_TIMEOUT_S, ServiceManager, ecf_server_path, manager_for
from ecf.stepup import with_step_up

RECORD_DIR = "destroyed"  # as ecf_server.destroy (the client never imports the service)
SERVICE_DONE, DONE = "service_done", "done"
SHARED = frozenset({"ollama", RECORD_DIR})  # folders in the data root that aren't installs
EXPORT_AGE = timedelta(hours=24)
UNSETTLED = ("installing", "migrating")  # upgrade.json phases mid-upgrade


def _export(p: Paths, to: str) -> None:
    from ecf.cli_export import manual  # noqa: PLC0415 - cli_export imports the CLI's helpers

    manual(p, to)


def _call(args: list[str]) -> int:
    """With this terminal attached: step-up and the Keychain prompts need a person."""
    return subprocess.call(args)  # noqa: S603 - our own ecf-server


def _run(args: list[str], env: dict[str, str]) -> int:
    return subprocess.run(args, env=env, capture_output=True, check=False,  # noqa: S603
                          timeout=60).returncode  # fmt: skip


def _prompt(text: str) -> str:
    return str(typer.prompt(text))


def _confirm(text: str, default: bool) -> bool:
    return typer.confirm(text, default=default)


def _which_claude() -> str | None:
    return shutil.which("claude")


@dataclass
class Tools:
    """What `ecf destroy` touches outside itself; fakes in tests."""

    manager: ServiceManager
    echo: Callable[[str], None] = typer.echo
    prompt: Callable[[str], str] = _prompt
    confirm: Callable[[str, bool], bool] = _confirm
    secret: Callable[[str], str] = hidden
    export: Callable[[Paths, str], None] = _export
    foreground: Callable[[list[str]], int] = _call
    run: Callable[[list[str], dict[str, str]], int] = _run
    server: Callable[[], Path] = ecf_server_path
    claude: Callable[[], str | None] = _which_claude
    wait_s: float = STOP_TIMEOUT_S


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("destroy")
    def destroy_command(
        config_token: Annotated[
            bool,
            typer.Option(
                "--config-token",
                help="Ask for a Slack configuration token, to delete the Slack app too.",
            ),
        ] = False,
    ) -> None:
        """Delete this install: its service, data, secrets and Slack channels. (step-up)"""
        require_terminal()
        p = paths()
        if not run(p, Tools(manager_for(p)), ask_token=config_token):
            raise typer.Exit(1)


def record_path(p: Paths) -> Path:
    return p.root / RECORD_DIR / f"{p.install}.json"


def read_record(p: Paths) -> dict[str, Any] | None:
    try:
        return json.loads(record_path(p).read_text())
    except (OSError, ValueError):
        return None


def run(p: Paths, t: Tools, *, ask_token: bool) -> bool:
    """The whole command; False when it stopped without finishing (the reason was printed)."""
    rec = read_record(p)
    if rec is not None and rec.get("phase") == DONE:
        t.echo(f"{p.install} was destroyed on {str(rec.get('done_at', ''))[:10]}")
        _residue(p, t, rec)
        return True
    pending = rec is None or rec.get("phase") != SERVICE_DONE
    if pending and p.db.exists() and not _service_part(p, t, ask_token=ask_token):
        return False
    _cli_part(p, t)
    return True


# ---- the service's part -------------------------------------------------------------------------


def _service_part(p: Paths, t: Tools, *, ask_token: bool) -> bool:
    try:
        with LocalClient(p) as c:
            pre: dict[str, Any] = c.get("/v1/destroy")
            state: dict[str, Any] = c.get("/v1/upgrade/state")
    except ServiceUnavailableError:
        return _foreground(p, t, ask_token=ask_token)
    problems = [*pre["busy"], *_local_problems(p, state)]
    for why in problems:
        t.echo(f"  can't destroy: {why}")
    if problems:
        return False
    _show(p, t, pre)
    if not _offer_export(p, t, pre):
        return False
    typed = _typed(p, t)
    if typed is None:
        return False
    body: dict[str, Any] = {"install": typed}
    if ask_token:
        body["config_token"] = t.secret("Slack configuration token (hidden; Enter to skip): ")
        body["config_token"] = body["config_token"].strip() or None
    try:
        with LocalClient(p) as c:
            with_step_up(c, lambda n: c.request("POST", "/v1/destroy", body | {"nonce_id": n},
                                                timeout=300), echo=t.echo)  # fmt: skip
    except ServiceUnavailableError as exc:
        if not exc.detail.startswith("no usable secret store"):
            raise
        t.echo(f"the service can't use the secret store ({exc.detail}); finishing in the"
               " foreground instead")  # fmt: skip
        t.manager.stop()
        return _run_foreground(p, t, typed, ask_token=ask_token)
    _wait_stopped(p, t)
    return True


def _local_problems(p: Paths, state: dict[str, Any]) -> list[str]:
    out: list[str] = []
    m = watch.marker(p)
    if m and m["alive"]:
        out.append(f"`ecf watch` is running the service (pid {m['pid']}); stop it first")
    up = upgrade_run.read_state(p)
    current: dict[str, Any] = state.get("upgrade") or {}
    if up and up.get("phase") in UNSETTLED:
        out.append(f"an upgrade to {up.get('to')} is in progress; finish it first (ecf upgrade"
                   " --continue)")  # fmt: skip
    elif current and not current.get("settled_at"):
        out.append(f"the upgrade to {current.get('to')} hasn't settled yet (the service's first"
                   " full timer pass); try again in a minute")  # fmt: skip
    return out


def _show(p: Paths, t: Tools, pre: dict[str, Any]) -> None:
    role = pre.get("role") or "role not set: init wasn't run"
    t.echo(f"This deletes the ecf install {p.install} ({role}) on this computer:")
    t.echo(f"  - its service and its data folder {p.data_dir}")
    t.echo("  - every secret it keeps (app passwords, Slack tokens, the backup signing key)")
    for a in pre["addresses"]:
        t.echo(f"  - watching {a['email']} (the mailbox itself is untouched)")
    slack = pre["slack"]
    if slack.get("app_id"):
        t.echo(f"  - its {slack['channels']} Slack channel(s), archived; the Slack app"
               f" {slack['app_id']} is deleted with --config-token, otherwise its bot is turned"
               " off")  # fmt: skip
    if pre.get("export_dir"):
        t.echo(f"Backups in {pre['export_dir']} are kept.")


def _offer_export(p: Paths, t: Tools, pre: dict[str, Any]) -> bool:
    """OD-389: an export first, unless one was made in the last 24 hours."""
    last = pre.get("last_export_at")
    if last and datetime.now(UTC) - _parse(last) < EXPORT_AGE:
        t.echo(f"Newest export: {last[:16].replace('T', ' ')} UTC.")
        return True
    t.echo("No export in the last 24 hours" + (f" (newest {last[:10]})." if last else "."))
    if not t.confirm("Export now? (needs a passphrase you keep)", True):
        return True
    to = pre.get("export_dir") or t.prompt("Folder or file to export to")
    t.export(p, str(to))
    return True


def _typed(p: Paths, t: Tools) -> str | None:
    typed = t.prompt(f"Type the install name ({p.install}) to destroy it").strip()
    if typed != p.install:
        t.echo("that isn't this install's name; nothing was destroyed")
        return None
    return typed


def _foreground(p: Paths, t: Tools, *, ask_token: bool) -> bool:
    """OD-387: the service isn't answering, so its part runs here, under the instance lock."""
    t.echo("The service isn't running, so no export can be made now (start it with `ecf service"
           " start`, then `ecf export --to <folder>`, if you need one).")  # fmt: skip
    if not t.confirm("Destroy without the service?", False):
        return False
    typed = _typed(p, t)
    if typed is None:
        return False
    if t.manager.status().running:  # running but not answering
        t.manager.stop()
    return _run_foreground(p, t, typed, ask_token=ask_token)


def _run_foreground(p: Paths, t: Tools, typed: str, *, ask_token: bool) -> bool:
    args = [str(t.server()), "destroy", "--install", p.install, "--confirm", typed]
    code = t.foreground([*args, "--config-token"] if ask_token else args)
    if code != 0:
        t.echo(f"the service's part didn't finish (exit {code}); run ecf destroy again")
        return False
    return True


def _wait_stopped(p: Paths, t: Tools) -> None:
    deadline = time.monotonic() + t.wait_s
    while p.socket.exists() and time.monotonic() < deadline:
        time.sleep(0.2)


# ---- the CLI's part -----------------------------------------------------------------------------


def _cli_part(p: Paths, t: Tools) -> None:
    rec: dict[str, Any] = read_record(p) or {"install": p.install, "steps": {}}
    cli: dict[str, Any] = rec.setdefault("cli", {})
    t.manager.uninstall()
    cli["unit"] = "removed"
    cli["claude_logout"] = _logout(p, t)
    _delete_data(p)
    cli["data_dir"] = "deleted"
    rec["phase"], rec["done_at"] = DONE, datetime.now(UTC).isoformat(timespec="seconds")
    rec["others"] = other_installs(p)
    _write(p, rec)
    t.echo(f"{p.install} is destroyed.")
    _residue(p, t, rec)


def _logout(p: Paths, t: Tools) -> str:
    lay = layout(p)
    if not lay.config_dir.exists():
        return "none"
    claude = t.claude()
    if claude is None:
        return "no_claude"
    try:
        code = t.run([claude, "auth", "logout"], base_env(lay))
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "ok" if code == 0 else "failed"


def _delete_data(p: Paths) -> None:
    d = p.data_dir
    if d.is_symlink():  # never follow it out of the data root
        d.unlink()
        return
    if not d.exists():
        return
    if d.resolve().parent != p.root.resolve() or d.name != p.install:
        raise ServiceUnavailableError(f"refusing to delete {d}: not this install's data folder")
    shutil.rmtree(d)


def other_installs(p: Paths) -> list[str]:
    try:
        entries = sorted(p.root.iterdir())
    except OSError:
        return []
    return [e.name for e in entries if e.is_dir() and e.name not in SHARED
            and e.name != p.install and (e / "ecf.db").exists()]  # fmt: skip


def _residue(p: Paths, t: Tools, rec: dict[str, Any]) -> None:
    lines: list[str] = list(rec.get("residue") or [])
    if rec.get("cli", {}).get("claude_logout") in ("failed", "no_claude"):
        lines.append("`ecf claude`'s Claude login may remain (Claude Code keeps it in the Keychain"
                     " on macOS): remove it there if you like")  # fmt: skip
    others = cast("list[str]", rec.get("others") or [])
    if others:
        lines.append(f"Ollama, its models and ecf's Ollama login item are shared with"
                     f" {', '.join(others)}, so they stay")  # fmt: skip
    else:
        lines.append("no other ecf install is left: `ecf models serve uninstall` removes ecf's"
                     " Ollama login item, `ollama rm <model>` a model, and `uv tool uninstall"
                     " email-classify-filter` ecf itself")  # fmt: skip
    t.echo("Left for you to do:")
    for line in lines:
        t.echo(f"  - {line}")
    t.echo(f"The record of this destroy is kept at {record_path(p)} (no secrets); delete it when"
           " you no longer need it.")  # fmt: skip


def _write(p: Paths, rec: dict[str, Any]) -> None:
    path = record_path(p)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
