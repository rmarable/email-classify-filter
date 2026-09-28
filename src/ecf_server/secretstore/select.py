"""Choose the secret-store backend (SPEC §11.6) and track the interpreter (OD-163)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ecf.errors import ServiceUnavailableError
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.secretstore import SecretStore
from ecf_server.secretstore.systemd_creds import MIN_SYSTEMD, SystemdCredsStore, systemd_version

INTERPRETER_KEY = "secret_store.interpreter_sha256"


@dataclass(frozen=True)
class Probe:
    """What the host offers. Injected in tests."""

    platform: str
    dbus_session: bool
    secret_service_ok: Callable[[], bool]
    systemd: Callable[[], int | None]


def host_probe(env: Mapping[str, str] | None = None) -> Probe:
    e = env or os.environ

    def secret_service_ok() -> bool:
        try:
            from keyring.backends import SecretService  # noqa: PLC0415

            return SecretService.Keyring.priority > 0  # pyright: ignore[reportOperatorIssue]
        except Exception:  # any failure means "not available"
            return False

    return Probe(
        sys.platform, bool(e.get("DBUS_SESSION_BUS_ADDRESS")), secret_service_ok, systemd_version
    )


def choose_backend(probe: Probe) -> str:
    if probe.platform == "darwin":
        return "keychain"
    if probe.platform.startswith("linux"):
        if probe.dbus_session and probe.secret_service_ok():
            return "secret-service"
        version = probe.systemd()
        if version is not None and version >= MIN_SYSTEMD:
            return "systemd-creds"
        raise ServiceUnavailableError(
            "no usable secret store: Secret Service needs a desktop session with an unlocked "
            f"keyring, and systemd-creds --user needs systemd {MIN_SYSTEMD}+ "
            f"(found {version or 'none'})"
        )
    raise ServiceUnavailableError(f"platform {probe.platform} is not supported in v1")


def open_store(install: str, data_dir: Path, *, interactive: bool, probe: Probe) -> SecretStore:
    backend = choose_backend(probe)
    if backend == "keychain":
        from ecf_server.secretstore.keyring_store import macos_keychain  # noqa: PLC0415

        return macos_keychain(install, interactive=interactive)
    if backend == "secret-service":
        from ecf_server.secretstore.keyring_store import secret_service  # noqa: PLC0415

        return secret_service(install)
    return SystemdCredsStore(data_dir / "creds")


def interpreter_sha256(executable: str | None = None) -> str:
    """Hash of the real interpreter binary. The Keychain trusts it by code hash (ADR 0001)."""
    path = Path(os.path.realpath(executable or sys.executable))
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def interpreter_changed(conn: sqlite3.Connection, current: str) -> bool:
    """True when a hash is recorded and differs: a foreground re-grant is needed (OD-163)."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (INTERPRETER_KEY,)).fetchone()
    return row is not None and row["value"] != current


def record_interpreter(conn: sqlite3.Connection, clock: Clock, current: str, *, by: str) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (INTERPRETER_KEY, current, to_ts(clock.now()), by),
        )
