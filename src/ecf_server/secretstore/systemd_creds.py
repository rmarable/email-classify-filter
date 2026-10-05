"""`systemd-creds --user` secrets for headless Linux (SPEC §11.6). Unverified until M5.

systemd decrypts each credential when the unit starts (`LoadCredentialEncrypted=`) and exposes it
under `$CREDENTIALS_DIRECTORY`. A write encrypts a new `.cred` file with `systemd-creds encrypt
--user`; the unit's credential list then has to be updated and the service restarted. Nothing
acts on `restart_required` yet: that is wired up with address management (V1.1) and verified on
Linux in M5. Until then the new value is kept in memory for this run.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from ecf_server.secretstore import SecretStoreNeedsYouError, check_name

MIN_SYSTEMD = 256
Runner = Callable[[list[str], bytes | None], subprocess.CompletedProcess[bytes]]


def _run(args: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, input=stdin, capture_output=True, check=False, timeout=30)  # noqa: S603


def cred_name(name: str) -> str:
    return check_name(name).replace("/", ".")


def systemd_version(runner: Runner = _run) -> int | None:
    try:
        out = runner(["systemd-creds", "--version"], None)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    m = re.search(rb"systemd (\d+)", out.stdout)
    return int(m.group(1)) if out.returncode == 0 and m else None


class SystemdCredsStore:
    backend = "systemd-creds"

    def __init__(
        self,
        creds_dir: Path,
        *,
        env: Mapping[str, str] | None = None,
        runner: Runner = _run,
    ) -> None:
        self._creds_dir = creds_dir
        self._loaded = Path((env or os.environ).get("CREDENTIALS_DIRECTORY", "/nonexistent"))
        self._runner = runner
        self._overlay: dict[str, str | None] = {}
        self.restart_required = False

    def get(self, name: str) -> str | None:
        key = cred_name(name)
        if key in self._overlay:
            return self._overlay[key]
        path = self._loaded / key
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            raise SecretStoreNeedsYouError(f"cannot read credential {key}") from exc

    def set(self, name: str, value: str) -> None:
        key = cred_name(name)
        self._creds_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._creds_dir / f"{key}.cred"
        out = self._runner(
            ["systemd-creds", "encrypt", "--user", f"--name={key}", "-", str(target)],
            value.encode("utf-8"),
        )
        if out.returncode != 0:
            msg = out.stderr.decode("utf-8", "replace").strip()[:200]
            raise SecretStoreNeedsYouError(f"systemd-creds encrypt failed: {msg}")
        target.chmod(0o600)
        self._overlay[key] = value
        self.restart_required = True

    def delete(self, name: str) -> None:
        key = cred_name(name)
        (self._creds_dir / f"{key}.cred").unlink(missing_ok=True)
        self._overlay[key] = None
        self.restart_required = True

    def credential_files(self) -> list[Path]:
        """The `.cred` files the unit must list with `LoadCredentialEncrypted=` (step 8)."""
        return sorted(self._creds_dir.glob("*.cred"))
