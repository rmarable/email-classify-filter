"""launchd / systemd units for the local service and the `ecf service` commands (SPEC §11.1).

This is operating-system plumbing, not a security decision, so it lives in the client. The unit
runs the absolute `ecf-server` path installed next to this `ecf`.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths

STOP_TIMEOUT_S = 60
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)  # noqa: S603
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not found")


def ecf_server_path() -> Path:
    """`ecf-server` from the same environment as the running interpreter."""
    candidate = Path(sys.executable).parent / "ecf-server"
    if not candidate.exists():
        raise ServiceUnavailableError(f"ecf-server not found next to {sys.executable}")
    return candidate.absolute()


def _env() -> dict[str, str]:
    return {"ECF_HOME": os.environ["ECF_HOME"]} if os.environ.get("ECF_HOME") else {}


@dataclass(frozen=True)
class UnitStatus:
    installed: bool
    running: bool
    pid: int | None = None
    last_exit: int | None = None
    detail: str = ""


class ServiceManager(Protocol):
    unit_path: Path

    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
    def status(self) -> UnitStatus: ...


def _reset_breaker(server: Path, install: str, runner: Runner) -> None:
    out = runner([str(server), "reset-breaker", "--install", install])
    if out.returncode != 0:
        raise ServiceUnavailableError(
            f"could not reset the crash-loop breaker: {out.stderr.strip()}"
        )


# ---------------------------------------------------------------------------- launchd (macOS)


def launchd_label(install: str) -> str:
    return f"com.email-classify-filter.{install}"


def render_launchd_plist(install: str, server: Path, paths: Paths) -> bytes:
    plist: dict[str, object] = {
        "Label": launchd_label(install),
        "ProgramArguments": [str(server), "local", "--install", install],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ExitTimeOut": STOP_TIMEOUT_S,
        "ProcessType": "Interactive",
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(paths.data_dir / "logs" / "launchd-stderr.log"),
    }
    if env := _env():
        plist["EnvironmentVariables"] = env
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)


class LaunchdManager:
    def __init__(
        self,
        paths: Paths,
        *,
        runner: Runner = run_command,
        server: Path | None = None,
        agents_dir: Path | None = None,
    ) -> None:
        self.paths = paths
        self.label = launchd_label(paths.install)
        self._runner = runner
        self._server = server
        self._domain = f"gui/{os.getuid()}"
        self.unit_path = (
            agents_dir or Path.home() / "Library" / "LaunchAgents"
        ) / f"{self.label}.plist"

    @property
    def server(self) -> Path:
        return self._server or ecf_server_path()

    def _launchctl(
        self, *args: str, ok: tuple[int, ...] = (0,)
    ) -> subprocess.CompletedProcess[str]:
        out = self._runner(["launchctl", *args])
        if out.returncode not in ok:
            raise ServiceUnavailableError(
                f"launchctl {args[0]} failed: {out.stderr.strip() or out.stdout.strip()}"
            )
        return out

    def _loaded(self) -> bool:
        return self._runner(["launchctl", "print", f"{self._domain}/{self.label}"]).returncode == 0

    def _bootout(self) -> None:
        """`launchctl bootout` returns before the job is gone; wait until launchd has removed it."""
        self._launchctl("bootout", f"{self._domain}/{self.label}")
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while self._loaded():
            if time.monotonic() > deadline:
                raise ServiceUnavailableError(
                    f"{self.label} did not stop within {STOP_TIMEOUT_S} s"
                )
            time.sleep(0.2)

    def install(self) -> None:
        (self.paths.data_dir / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        if self._loaded():
            self._bootout()
        self.unit_path.write_bytes(
            render_launchd_plist(self.paths.install, self.server, self.paths)
        )
        self.unit_path.chmod(0o644)
        self._launchctl("bootstrap", self._domain, str(self.unit_path))

    def uninstall(self) -> None:
        if self._loaded():
            self._bootout()
        self.unit_path.unlink(missing_ok=True)

    def start(self) -> None:
        if not self.unit_path.exists():
            raise ServiceUnavailableError("the service isn't installed: run `ecf service install`")
        _reset_breaker(self.server, self.paths.install, self._runner)
        if self._loaded():
            self._launchctl("kickstart", f"{self._domain}/{self.label}")
        else:
            self._launchctl("bootstrap", self._domain, str(self.unit_path))

    def stop(self) -> None:
        if self._loaded():
            self._bootout()

    def restart(self) -> None:
        _reset_breaker(self.server, self.paths.install, self._runner)
        if self._loaded():
            self._launchctl("kickstart", "-k", f"{self._domain}/{self.label}")
        else:
            self.start()

    def status(self) -> UnitStatus:
        installed = self.unit_path.exists()
        out = self._runner(["launchctl", "print", f"{self._domain}/{self.label}"])
        if out.returncode != 0:
            return UnitStatus(installed, False, detail="not loaded")
        text = out.stdout
        state = re.search(r"^\s*state = (\S+)", text, re.M)
        pid = re.search(r"^\s*pid = (\d+)", text, re.M)
        last = re.search(r"^\s*last exit code = (\d+)", text, re.M)
        return UnitStatus(
            installed,
            bool(state and state.group(1) == "running"),
            int(pid.group(1)) if pid else None,
            int(last.group(1)) if last else None,
            state.group(1) if state else "",
        )


# ---------------------------------------------------------------------------- systemd (Linux)


def systemd_unit_name(install: str) -> str:
    return f"ecf-{install}.service"


def render_systemd_unit(
    install: str, server: Path, credential_files: list[Path] | None = None
) -> str:
    lines = [
        "[Unit]",
        f"Description=email-classify-filter ({install})",
        "StartLimitIntervalSec=600",
        "StartLimitBurst=5",
        "",
        "[Service]",
        f'ExecStart="{server}" local --install {install}',
        "Restart=on-failure",
        f"TimeoutStopSec={STOP_TIMEOUT_S}",
        "UMask=0077",
    ]
    lines += [f'Environment="{k}={v}"' for k, v in _env().items()]
    for cred in credential_files or []:
        # not quoted: quoting support for this setting is unverified until the V1.6 Linux test
        lines.append(f"LoadCredentialEncrypted={cred.name.removesuffix('.cred')}:{cred}")
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


class SystemdManager:
    def __init__(
        self,
        paths: Paths,
        *,
        runner: Runner = run_command,
        server: Path | None = None,
        units_dir: Path | None = None,
    ) -> None:
        self.paths = paths
        self.unit = systemd_unit_name(paths.install)
        self._runner = runner
        self._server = server
        self.unit_path = (units_dir or Path.home() / ".config" / "systemd" / "user") / self.unit

    @property
    def server(self) -> Path:
        return self._server or ecf_server_path()

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        out = self._runner(["systemctl", "--user", *args])
        if out.returncode != 0:
            raise ServiceUnavailableError(f"systemctl {args[0]} failed: {out.stderr.strip()}")
        return out

    def install(self) -> None:
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        creds = sorted((self.paths.data_dir / "creds").glob("*.cred"))
        self.unit_path.write_text(render_systemd_unit(self.paths.install, self.server, creds))
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", self.unit)

    def uninstall(self) -> None:
        self._runner(["systemctl", "--user", "disable", "--now", self.unit])
        self.unit_path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")

    def start(self) -> None:
        _reset_breaker(self.server, self.paths.install, self._runner)
        self._runner(["systemctl", "--user", "reset-failed", self.unit])
        self._systemctl("start", self.unit)

    def stop(self) -> None:
        self._systemctl("stop", self.unit)

    def restart(self) -> None:
        _reset_breaker(self.server, self.paths.install, self._runner)
        self._systemctl("restart", self.unit)

    def status(self) -> UnitStatus:
        out = self._runner(
            [
                "systemctl",
                "--user",
                "show",
                self.unit,
                "-p",
                "ActiveState,MainPID,ExecMainStatus,LoadState",
            ]
        )
        props = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
        pid = int(props.get("MainPID", "0") or 0)
        return UnitStatus(
            self.unit_path.exists() and props.get("LoadState") != "not-found",
            props.get("ActiveState") == "active",
            pid or None,
            int(props["ExecMainStatus"]) if props.get("ExecMainStatus", "").isdigit() else None,
            props.get("ActiveState", ""),
        )


def manager_for(paths: Paths) -> ServiceManager:
    if sys.platform == "darwin":
        return LaunchdManager(paths)
    if sys.platform.startswith("linux"):
        return SystemdManager(paths)
    raise ServiceUnavailableError(f"platform {sys.platform} is not supported in v1")
