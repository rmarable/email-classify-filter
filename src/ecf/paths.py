"""Per-install file locations (SPEC §11.7, §11.12). Shared by the CLI and the service.

`ECF_HOME` overrides the data root only; each install gets `<root>/<install>`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ecf.errors import InvalidInputError

# sun_path is 104 bytes on macOS and 108 on Linux, including the terminating NUL.
SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108


def data_root() -> Path:
    if home := os.environ.get("ECF_HOME"):
        return Path(home).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ecf"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "ecf"


@dataclass(frozen=True)
class Paths:
    install: str
    root: Path
    honor_ecf_socket: bool = True  # clients only; the service always uses its own socket

    @property
    def data_dir(self) -> Path:
        return self.root / self.install

    @property
    def run_dir(self) -> Path:
        if self.honor_ecf_socket and (sock := os.environ.get("ECF_SOCKET")):
            return Path(sock).expanduser().parent
        return self.data_dir / "run"

    @property
    def socket(self) -> Path:
        """`ECF_SOCKET` points the CLI and MCP at a socket directly (SPEC §17.3)."""
        if self.honor_ecf_socket and (sock := os.environ.get("ECF_SOCKET")):
            return Path(sock).expanduser()
        return self.run_dir / "ecf.sock"

    @property
    def token(self) -> Path:
        return self.run_dir / "cli.token"

    @property
    def lock(self) -> Path:
        return self.run_dir / "ecf-server.lock"

    @property
    def db(self) -> Path:
        return self.data_dir / "ecf.db"

    @property
    def log(self) -> Path:
        return self.data_dir / "logs" / "ecf.log"

    @property
    def crash_state(self) -> Path:
        return self.data_dir / "crash.json"

    @property
    def running_marker(self) -> Path:
        return self.run_dir / "running"

    def check_socket_length(self) -> None:
        if len(os.fsencode(self.socket)) >= SUN_PATH_MAX:
            raise InvalidInputError(
                f"socket path is {len(os.fsencode(self.socket))} bytes; the limit here is "
                f"{SUN_PATH_MAX - 1}. Use a shorter ECF_HOME or install name."
            )


def paths_for(install: str = "default", *, for_service: bool = False) -> Paths:
    return Paths(install, data_root(), honor_ecf_socket=not for_service)
