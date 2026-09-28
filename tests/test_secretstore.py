import sqlite3
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import keyring.errors
import pytest

from ecf.errors import InvalidInputError, ServiceUnavailableError
from ecf_server.clock import FakeClock
from ecf_server.secretstore import SecretStore, SecretStoreNeedsYouError, check_name
from ecf_server.secretstore.keyring_store import KeyringSecretStore
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.secretstore.select import (
    Probe,
    choose_backend,
    interpreter_changed,
    interpreter_sha256,
    record_interpreter,
)
from ecf_server.secretstore.systemd_creds import SystemdCredsStore, cred_name, systemd_version


class FakeSystemdCreds:
    """Stands in for `systemd-creds`: `encrypt` writes the plaintext so tests can inspect it."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        if args[:2] == ["systemd-creds", "--version"]:
            return subprocess.CompletedProcess(args, 0, b"systemd 257 (257.9)\n+PAM", b"")
        if self.fail:
            return subprocess.CompletedProcess(args, 1, b"", b"No TPM2 and no host key")
        Path(args[-1]).write_bytes(stdin or b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")


def _memory(_: Path) -> SecretStore:
    return MemorySecretStore()


def _systemd(tmp: Path) -> SecretStore:
    return SystemdCredsStore(
        tmp / "creds", env={"CREDENTIALS_DIRECTORY": str(tmp / "loaded")}, runner=FakeSystemdCreds()
    )


STORES: dict[str, Callable[[Path], SecretStore]] = {"memory": _memory, "systemd-creds": _systemd}


@pytest.mark.parametrize("make", STORES.values(), ids=STORES.keys())
def test_contract(make: Callable[[Path], SecretStore], tmp_path: Path) -> None:
    s = make(tmp_path)
    assert s.get("slack/bot") is None
    s.set("slack/bot", "xoxb-dummy")
    s.set("mailbox/billing", "one")
    s.set("mailbox/billing", "two")
    assert (s.get("slack/bot"), s.get("mailbox/billing")) == ("xoxb-dummy", "two")
    s.delete("mailbox/billing")
    s.delete("mailbox/billing")  # deleting twice is fine
    assert s.get("mailbox/billing") is None
    with pytest.raises(InvalidInputError):
        s.set("../etc/passwd", "x")


@pytest.mark.parametrize(
    "name",
    [
        "mailbox/billing",
        "mailbox/accounts-payable",
        "slack/bot",
        "slack/app",
        "models-api-key",
        "export-signing-seed",
    ],
)
def test_allowed_names(name: str) -> None:
    assert check_name(name) == name


@pytest.mark.parametrize(
    "name", ["mailbox/", "mailbox/Billing", "slack/user", "aws", "mailbox/a/b"]
)
def test_rejected_names(name: str) -> None:
    with pytest.raises(InvalidInputError):
        check_name(name)


def test_locked_store_needs_you() -> None:
    s = MemorySecretStore()
    s.locked = True
    with pytest.raises(SecretStoreNeedsYouError):
        s.get("slack/bot")
    assert issubclass(SecretStoreNeedsYouError, ServiceUnavailableError)


def test_systemd_creds_reads_loaded_and_writes_encrypted(tmp_path: Path) -> None:
    loaded = tmp_path / "loaded"
    loaded.mkdir()
    (loaded / "slack.bot").write_text("from-systemd")
    fake = FakeSystemdCreds()
    s = SystemdCredsStore(
        tmp_path / "creds", env={"CREDENTIALS_DIRECTORY": str(loaded)}, runner=fake
    )
    assert s.get("slack/bot") == "from-systemd"
    s.set("mailbox/billing", "pw")
    assert fake.calls[-1][:4] == ["systemd-creds", "encrypt", "--user", "--name=mailbox.billing"]
    cred = tmp_path / "creds" / "mailbox.billing.cred"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert s.restart_required and s.credential_files() == [cred]
    assert cred_name("mailbox/billing") == "mailbox.billing"


def test_systemd_creds_failure_needs_you(tmp_path: Path) -> None:
    s = SystemdCredsStore(tmp_path / "creds", env={}, runner=FakeSystemdCreds(fail=True))
    with pytest.raises(SecretStoreNeedsYouError, match="No TPM2"):
        s.set("slack/bot", "x")


def test_systemd_version() -> None:
    assert systemd_version(FakeSystemdCreds()) == 257


def _probe(
    platform: str, dbus: bool = False, ss: bool = False, systemd: int | None = None
) -> Probe:
    return Probe(platform, dbus, lambda: ss, lambda: systemd)


def test_backend_choice() -> None:
    assert choose_backend(_probe("darwin")) == "keychain"
    assert choose_backend(_probe("linux", dbus=True, ss=True)) == "secret-service"
    assert choose_backend(_probe("linux", dbus=True, ss=False, systemd=257)) == "systemd-creds"
    assert choose_backend(_probe("linux", systemd=256)) == "systemd-creds"
    for p in (_probe("linux", systemd=255), _probe("linux"), _probe("win32")):
        with pytest.raises(ServiceUnavailableError):
            choose_backend(p)


class FakeKeyring:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def get_password(self, service: str, user: str) -> Any:
        if self.exc:
            raise self.exc
        return None

    def set_password(self, service: str, user: str, pw: str) -> None:
        if self.exc:
            raise self.exc

    def delete_password(self, service: str, user: str) -> None:
        raise keyring.errors.PasswordDeleteError("absent")


@pytest.mark.parametrize(
    "exc", [keyring.errors.KeyringLocked("locked"), keyring.errors.KeyringError("-25293")]
)
def test_keyring_errors_map_to_needs_you(exc: Exception) -> None:
    s = KeyringSecretStore(FakeKeyring(exc), "default", backend_name="fake")  # pyright: ignore[reportArgumentType]
    with pytest.raises(SecretStoreNeedsYouError):
        s.get("slack/bot")
    with pytest.raises(SecretStoreNeedsYouError):
        s.set("slack/bot", "x")


def test_keyring_delete_of_absent_is_fine() -> None:
    KeyringSecretStore(FakeKeyring(), "default", backend_name="fake").delete("slack/bot")  # pyright: ignore[reportArgumentType]


def test_interpreter_tracking(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    a, b = tmp_path / "py-a", tmp_path / "py-b"
    a.write_bytes(b"interpreter A")
    b.write_bytes(b"interpreter B")
    ha, hb = interpreter_sha256(str(a)), interpreter_sha256(str(b))
    assert ha != hb and len(ha) == 64
    assert not interpreter_changed(conn, ha)  # nothing recorded yet
    record_interpreter(conn, clock, ha, by="test")
    assert not interpreter_changed(conn, ha)
    assert interpreter_changed(conn, hb)
    record_interpreter(conn, clock, hb, by="test")
    assert not interpreter_changed(conn, hb)
