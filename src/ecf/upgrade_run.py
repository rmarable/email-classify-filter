"""Carrying out `ecf upgrade --wheel <file>` (SPEC §11.10; OD-107, OD-111, OD-331, OD-376 to OD-381;
V1.5 step 11b).

**Phase 1, the old CLI** (`start`): after the checks (cli_upgrade.preflight) and a yes, it tells
the service the stop is deliberate, stops it, has the old `ecf-server snapshot` copy the database
to `<data>/upgrades/<from>-to-<to>/`, copies the running version's wheel beside it, writes
`<data>/upgrade.json` and runs `uv tool install --force <new wheel>`. `uv tool` replaces the files
of this environment in place, so the old CLI imports nothing more and `execv`s the new
`ecf upgrade --continue`. If the install fails, the old wheel goes back and the service starts
again; the database was never touched.

**Phase 2, the new CLI** (`resume`): the new `ecf-server migrate`, then on macOS the Keychain
re-grant (a refusal doesn't roll back: the service waits with "secret store needs you" and
`ecf service regrant` finishes it; OD-378), then the service starts and must answer within
`START_S`. A failed migration or start restores the whole snapshot (the database file, without
its `-wal`/`-shm`) and the old wheel, and starts the old service (OD-331, OD-378). On success the
service records the upgrade (`POST /v1/upgrade/finish`) and marks it settled at its first timer
pass whose work all succeeds (OD-377). Addresses whose pinned model changed drop to `assist` at
the service's first tick (stages.tick, §9.3; OD-381).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ecf.client import LocalClient
from ecf.errors import EcfError
from ecf.paths import Paths
from ecf.service_unit import ServiceManager, ecf_server_path

START_S = 60.0
Run = Callable[[list[str]], int]  # a foreground command; returns its exit code


def call(args: list[str]) -> int:
    return subprocess.call(args)  # noqa: S603 - uv and our own ecf-server


def state_file(paths: Paths) -> Path:
    return paths.data_dir / "upgrade.json"


def read_state(paths: Paths) -> dict[str, Any] | None:
    try:
        return json.loads(state_file(paths).read_text())
    except (OSError, ValueError):
        return None


def write_state(paths: Paths, state: dict[str, Any]) -> None:
    f = state_file(paths)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.chmod(0o600)
    os.replace(tmp, f)


@dataclass
class Tools:
    """What the upgrade runs; fakes in tests."""

    manager: ServiceManager
    run: Run = call
    server: Callable[[], Path] = ecf_server_path
    uv: Callable[[], str | None] = lambda: shutil.which("uv")
    execv: Callable[[str, list[str]], None] = os.execv
    answering: Callable[[Paths], bool] | None = None
    sleep: Callable[[float], None] = time.sleep
    echo: Callable[[str], None] = print


def start(paths: Paths, tools: Tools, *, old_version: str, new_version: str, new_wheel: Path,
          old_wheel: Path, pin_changes: list[str], affected: list[str]) -> None:  # fmt: skip
    """Phase 1. Ends in `execv` of the new CLI, or raises after putting things back."""
    uv = tools.uv()
    if uv is None:
        raise EcfError("uv isn't on PATH; ecf upgrade installs with `uv tool install`")
    label = f"{old_version}-to-{new_version}"
    _stopping_on_purpose(paths)
    tools.echo("stopping the service")
    tools.manager.stop()
    tools.echo("copying the database")
    if tools.run([str(tools.server()), "snapshot", "--install", paths.install,
                  "--label", label]) != 0:  # fmt: skip
        tools.manager.start()
        raise EcfError("the database copy failed; nothing changed and the service is started"
                       " again")  # fmt: skip
    folder = paths.data_dir / "upgrades" / label
    kept = folder / old_wheel.name
    shutil.copy2(old_wheel, kept)
    state = {"from": old_version, "to": new_version, "label": label, "folder": str(folder),
             "old_wheel": str(kept), "new_wheel": str(new_wheel), "phase": "installing",
             "started_at": _now(), "pin_changes": pin_changes, "affected": affected}  # fmt: skip
    write_state(paths, state)
    tools.echo(f"installing {new_wheel.name}")
    if tools.run([uv, "tool", "install", "--force", str(new_wheel)]) != 0:
        tools.echo("the install failed; putting the old version back")
        tools.run([uv, "tool", "install", "--force", str(kept)])
        tools.manager.start()
        write_state(paths, state | {"phase": "failed", "why": "install"})
        raise EcfError("the new version didn't install; the old one is back and running")
    write_state(paths, state | {"phase": "migrating"})
    ecf = Path(sys.prefix) / "bin" / "ecf"
    tools.execv(str(ecf), [str(ecf), "--install", paths.install, "upgrade", "--continue"])


def resume(paths: Paths, tools: Tools, *, regrant: bool) -> dict[str, Any]:
    """Phase 2, run by the new CLI."""
    state = read_state(paths)
    if state is None or state.get("phase") != "migrating":
        raise EcfError("no upgrade is waiting to continue")
    server = str(tools.server())
    tools.echo("migrating the database")
    if tools.run([server, "migrate", "--install", paths.install]) != 0:
        return _roll_back(paths, tools, state, "migrate")
    if regrant:
        tools.echo("re-granting Keychain access (choose Always Allow at each dialog)")
        if tools.run([server, "regrant", "--install", paths.install]) != 0:
            tools.echo("the Keychain re-grant didn't finish; the service will wait until you"
                       " run `ecf service regrant`")  # fmt: skip
    tools.echo("starting the service")
    tools.manager.start()
    if not _wait(paths, tools):
        return _roll_back(paths, tools, state, "start")
    done = state | {"phase": "started", "finished_at": _now()}
    write_state(paths, done)
    with LocalClient(paths) as c:
        c.request("POST", "/v1/upgrade/finish", {k: done[k] for k in (
            "from", "to", "label", "started_at", "pin_changes", "affected")})  # fmt: skip
    return done


def _roll_back(paths: Paths, tools: Tools, state: dict[str, Any], why: str) -> dict[str, Any]:
    """Before the new service ever ran: the whole snapshot and the old wheel (OD-331)."""
    tools.echo(f"the {why} step failed; restoring the database copy and the old version")
    tools.manager.stop()
    folder = Path(state["folder"])
    for suffix in ("-wal", "-shm"):
        Path(str(paths.db) + suffix).unlink(missing_ok=True)
    shutil.copy2(folder / "ecf.db", paths.db)
    paths.db.chmod(0o600)
    uv = tools.uv() or "uv"
    tools.run([uv, "tool", "install", "--force", state["old_wheel"]])
    tools.manager.start()
    rolled = state | {"phase": "rolled_back", "why": why, "finished_at": _now()}
    write_state(paths, rolled)
    return rolled


def _wait(paths: Paths, tools: Tools) -> bool:
    check = tools.answering or _answering
    deadline = time.monotonic() + START_S
    while time.monotonic() < deadline:
        if check(paths):
            return True
        tools.sleep(0.5)
    return check(paths)


def _answering(paths: Paths) -> bool:
    try:
        with LocalClient(paths) as c:
            c.get("/v1/health", auth=False)
    except EcfError:
        return False
    return True


def _stopping_on_purpose(paths: Paths) -> None:
    try:
        with LocalClient(paths) as c:
            c.request("POST", "/v1/service/stopping", {})
    except EcfError:
        pass


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
