"""ecf's own login item for Ollama (SPEC §7.5; OD-246; V1.3 step 1c).

`ollama serve` runs from a LaunchAgent (macOS) or a systemd user unit (Linux, unverified until
M5) with a fixed environment, so the settings ecf relies on don't depend on how Ollama was
installed: loopback only, one request at a time (the prompt cache), the cloud feature off, and none
of flash attention, the q8 cache or debug logging (measured 2026-09-30: no gain, and request
logging writes email text to disk). One item per user, shared by every install. `ProcessType` is
Interactive, as for ecf's own unit: model work is where launchd's "light resource limits" would
matter (OD-226 measures it in V1.3 step 8). Ollama's stderr goes to `<data root>/ollama/ollama.log`;
at its default level it holds no request content (measured 2026-09-30).

Like `service_unit.py` this is operating-system plumbing, so it lives in the client; the service
still checks the running server itself before any model work (`ecf_server.ollama.readiness`).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
from pathlib import Path

from ecf.errors import ServiceUnavailableError
from ecf.service_unit import STOP_TIMEOUT_S, Runner, UnitStatus, run_command

LABEL = "com.email-classify-filter.ollama"
SYSTEMD_UNIT = "ecf-ollama.service"
PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
ENV = {
    "OLLAMA_HOST": "127.0.0.1:11434",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_NO_CLOUD": "1",
}


def ollama_path() -> Path:
    found = shutil.which("ollama") or next(
        (p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama") if Path(p).exists()),
        None,
    )
    if not found:
        raise ServiceUnavailableError(
            "Ollama isn't installed (macOS: `brew install ollama && brew pin ollama mlx-c`)"
        )
    # not resolved: Homebrew's /opt/homebrew/bin/ollama links into a versioned Cellar folder that
    # an upgrade deletes, so the login item keeps the link
    return Path(found).absolute()


def program_gone(program: str | None) -> str:
    """The status detail when the login item's program no longer exists (Ollama upgraded or
    removed since the item was written); empty when it's there."""
    if program is None or Path(program).exists():
        return ""
    return (f"its program {program} is gone (Ollama upgraded or removed?): run `ecf models serve"
            " install` again")  # fmt: skip


def environment(home: Path) -> dict[str, str]:
    return {"HOME": str(home), "PATH": PATH, **ENV}


def log_path(root: Path) -> Path:
    return root / "ollama" / "ollama.log"


# ---------------------------------------------------------------------------- launchd


def render_plist(ollama: Path, home: Path, root: Path, label: str = LABEL) -> bytes:
    plist: dict[str, object] = {
        "Label": label,
        "ProgramArguments": [str(ollama), "serve"],
        "EnvironmentVariables": environment(home),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ExitTimeOut": STOP_TIMEOUT_S,
        "ProcessType": "Interactive",
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(log_path(root)),
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)


class LaunchdOllama:
    def __init__(
        self,
        root: Path,
        *,
        runner: Runner = run_command,
        ollama: Path | None = None,
        agents_dir: Path | None = None,
        home: Path | None = None,
        label: str = LABEL,  # tests use an ecf-test-* label
    ) -> None:
        self.root = root
        self.label = label
        self._runner = runner
        self._ollama = ollama
        self._home = home or Path.home()
        self._target = f"gui/{os.getuid()}/{label}"
        self.unit_path = (agents_dir or self._home / "Library" / "LaunchAgents") / f"{label}.plist"

    def _launchctl(self, *args: str) -> None:
        out = self._runner(["launchctl", *args])
        if out.returncode != 0:
            raise ServiceUnavailableError(
                f"launchctl {args[0]} failed: {out.stderr.strip() or out.stdout.strip()}"
            )

    def loaded(self) -> bool:
        return self._runner(["launchctl", "print", self._target]).returncode == 0

    def install(self) -> None:
        ollama = self._ollama or ollama_path()
        log_path(self.root).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        if self.loaded():
            self._launchctl("bootout", self._target)
        self.unit_path.write_bytes(render_plist(ollama, self._home, self.root, self.label))
        self.unit_path.chmod(0o644)
        self._launchctl("bootstrap", f"gui/{os.getuid()}", str(self.unit_path))

    def uninstall(self) -> None:
        if self.loaded():
            self._launchctl("bootout", self._target)
        self.unit_path.unlink(missing_ok=True)

    def program(self) -> str | None:
        try:
            plist = plistlib.loads(self.unit_path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None
        args = plist.get("ProgramArguments") or [None]
        return str(args[0]) if args[0] else None

    def status(self) -> UnitStatus:
        installed = self.unit_path.exists()
        gone = program_gone(self.program()) if installed else ""
        out = self._runner(["launchctl", "print", self._target])
        if out.returncode != 0:
            return UnitStatus(installed, False, detail=gone or "not loaded")
        running = any(line.strip() == "state = running" for line in out.stdout.splitlines())
        return UnitStatus(installed, running,
                          detail=gone or ("running" if running else "loaded"))  # fmt: skip


# ---------------------------------------------------------------------------- systemd (Linux)


def render_systemd(ollama: Path, home: Path, root: Path) -> str:
    lines = [
        "[Unit]",
        "Description=Ollama for email-classify-filter",
        "",
        "[Service]",
        f'ExecStart="{ollama}" serve',
        "Restart=on-failure",
        f"TimeoutStopSec={STOP_TIMEOUT_S}",
        "UMask=0077",
        *(f'Environment="{k}={v}"' for k, v in environment(home).items()),
        f"StandardError=append:{log_path(root)}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


class SystemdOllama:
    """Unverified until M5."""

    def __init__(
        self,
        root: Path,
        *,
        runner: Runner = run_command,
        ollama: Path | None = None,
        units_dir: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.root = root
        self._runner = runner
        self._ollama = ollama
        self._home = home or Path.home()
        self.unit_path = (units_dir or self._home / ".config" / "systemd" / "user") / SYSTEMD_UNIT

    def _systemctl(self, *args: str) -> None:
        out = self._runner(["systemctl", "--user", *args])
        if out.returncode != 0:
            raise ServiceUnavailableError(f"systemctl {args[0]} failed: {out.stderr.strip()}")

    def install(self) -> None:
        ollama = self._ollama or ollama_path()
        log_path(self.root).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(render_systemd(ollama, self._home, self.root))
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", SYSTEMD_UNIT)
        self._systemctl("restart", SYSTEMD_UNIT)

    def uninstall(self) -> None:
        self._runner(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        self.unit_path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")

    def program(self) -> str | None:
        try:
            text = self.unit_path.read_text()
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith("ExecStart="):
                return line.removeprefix("ExecStart=").split('"')[1] if '"' in line else None
        return None

    def status(self) -> UnitStatus:
        out = self._runner(["systemctl", "--user", "show", SYSTEMD_UNIT, "-p", "ActiveState"])
        active = "ActiveState=active" in out.stdout
        installed = self.unit_path.exists()
        gone = program_gone(self.program()) if installed else ""
        return UnitStatus(installed, active, detail=gone or ("running" if active else ""))


def manager_for(root: Path) -> LaunchdOllama | SystemdOllama:
    if sys.platform == "darwin":
        return LaunchdOllama(root)
    if sys.platform.startswith("linux"):
        return SystemdOllama(root)
    raise ServiceUnavailableError(f"platform {sys.platform} is not supported in v1")
