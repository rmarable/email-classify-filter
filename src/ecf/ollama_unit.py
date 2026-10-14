"""Retiring ecf's Ollama login item (v3.0.0; ADR 0031, R-D13, P13; SPEC §7.5, §11.10).

Before v3.0.0 ecf ran `ollama serve` from its own login item (a LaunchAgent on macOS, a systemd
user unit on Linux), one per user and shared by every install, logging to `<data root>/ollama/`.
v3.0.0 removed the local model, so `retire` takes both away: the new service calls it from
`upgrade_state.settle`, once the rollback window has closed (a rollback to v2.2.x still needs
them), and it is idempotent, so a later settle, or an install that never had them, changes
nothing. The model copies stay for the operator (`ollama rm`, release notes); Ollama itself is
never touched. `cli_destroy.SHARED` keeps the `ollama` folder name until v4.

Like `service_unit.py` this is operating-system plumbing, so it lives in the client package.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from ecf.service_unit import Runner, run_command

LABEL = "com.email-classify-filter.ollama"
SYSTEMD_UNIT = "ecf-ollama.service"
FOLDER = "ollama"  # in the data root: the login item's log


def retire(root: Path, *, runner: Runner = run_command, home: Path | None = None,
           platform: str = sys.platform, label: str = LABEL) -> list[str]:  # fmt: skip
    """Unload and remove ecf's Ollama login item and delete `<root>/ollama/`; what it removed
    (empty when nothing was there). A command that fails is not retried: nothing depends on it.
    `label` is for tests (`ecf-test-*`)."""
    home = home or Path.home()
    done: list[str] = []
    if platform == "darwin":
        target = f"gui/{os.getuid()}/{label}"
        if runner(["launchctl", "print", target]).returncode == 0:
            runner(["launchctl", "bootout", target])
            done.append(f"login item {label} unloaded")
        plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            plist.unlink()
            done.append(str(plist))
    elif platform.startswith("linux"):  # unverified until M5
        unit = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        if unit.exists():
            runner(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
            unit.unlink()
            runner(["systemctl", "--user", "daemon-reload"])
            done.append(str(unit))
    folder = root / FOLDER
    if folder.is_symlink():  # never followed
        folder.unlink()
        done.append(str(folder))
    elif folder.is_dir():
        shutil.rmtree(folder)
        done.append(str(folder))
    return done
