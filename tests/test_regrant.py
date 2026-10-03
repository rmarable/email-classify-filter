"""The Keychain re-grant (V1.5 step 8c; OD-163, OD-348): `ecf-server regrant` reads every secret
and records the interpreter only when all reads succeed; `ecf service regrant` stops and restarts
the service around it; Linux needs none. The macOS test uses a real Keychain item under an
`ecf-test-*` install and deletes it."""

from __future__ import annotations

import json
import secrets
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ecf.paths import Paths
from ecf.service_unit import UnitStatus, regrant
from ecf_server import regrant as server_regrant
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.secretstore.select import interpreter_changed


def _addresses(conn: sqlite3.Connection) -> None:
    with write_tx(conn):
        for aid, removed in (("ap", None), ("old", "t")):
            conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                         " created_at, removed_at) VALUES (?, ?, 'standard', 'A', 't', ?)",
                         (aid, f"{aid}@acme.example", removed))  # fmt: skip


def _events(conn: sqlite3.Connection) -> list[tuple[str, dict[str, object]]]:
    rows = conn.execute("SELECT outcome, data FROM audit WHERE event = 'secret.regranted'")
    return [(r[0], json.loads(r[1])) for r in rows]


def test_names_cover_every_secret_ecf_keeps(conn: sqlite3.Connection) -> None:
    _addresses(conn)
    assert server_regrant.names(conn) == ["mailbox/ap", "slack/bot", "slack/app",
                                          "models-api-key", "export-signing-seed"]  # fmt: skip


def test_reads_all_then_records_the_interpreter(conn: sqlite3.Connection,
                                               clock: FakeClock) -> None:  # fmt: skip
    _addresses(conn)
    store = MemorySecretStore()
    store.set("mailbox/ap", "pw")
    store.set("slack/bot", "xoxb-x")
    said: list[str] = []
    got = server_regrant.run(conn, clock, store, echo=said.append, interpreter=lambda: "new")
    assert got.failed is None
    assert got.read == ["mailbox/ap", "slack/bot"]
    assert got.absent == ["slack/app", "models-api-key", "export-signing-seed"]
    assert said[0] == "reading mailbox/ap ..."
    assert not interpreter_changed(conn, "new")
    row = conn.execute("SELECT updated_by FROM settings WHERE key ="
                       " 'secret_store.interpreter_sha256'").fetchone()  # fmt: skip
    assert row[0] == "regrant"
    assert _events(conn) == [("ok", {"read": 2, "absent": 3})]


def test_a_refused_read_records_nothing(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _addresses(conn)
    from ecf_server.secretstore.select import record_interpreter  # noqa: PLC0415

    record_interpreter(conn, clock, "old", by="service")
    store = MemorySecretStore()
    store.locked = True
    got = server_regrant.run(conn, clock, store, echo=lambda _s: None, interpreter=lambda: "new")
    assert got.failed == "mailbox/ap" and got.read == []
    assert interpreter_changed(conn, "new")  # still flagged
    assert _events(conn) == [("error", {"read": 0, "failed": "mailbox/ap"})]


class _Manager:
    unit_path = Path("/dev/null")

    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[str] = []

    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def restart(self) -> None: ...

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def status(self) -> UnitStatus:
        return UnitStatus(installed=True, running=self.running)


def test_service_regrant_stops_runs_and_starts(tmp_path: Path) -> None:
    paths = Paths("t", tmp_path)
    m = _Manager(running=True)
    ran: list[list[str]] = []

    def call(args: list[str]) -> int:
        ran.append(args)
        m.calls.append("regrant")
        return 0

    code = regrant(paths, m, platform="darwin", call=call, server=lambda: Path("/x/ecf-server"),
                   echo=lambda _s: None)  # fmt: skip
    assert code == 0 and m.calls == ["stop", "regrant", "start"]
    assert ran == [["/x/ecf-server", "regrant", "--install", "t"]]


def test_service_regrant_restarts_even_when_the_regrant_fails(tmp_path: Path) -> None:
    m = _Manager(running=True)

    def boom(_args: list[str]) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        regrant(Paths("t", tmp_path), m, platform="darwin", call=boom,
                server=lambda: Path("/x/ecf-server"), echo=lambda _s: None)  # fmt: skip
    assert m.calls == ["stop", "start"]


def test_service_regrant_leaves_a_stopped_service_stopped_and_linux_alone(tmp_path: Path) -> None:
    m = _Manager(running=False)
    code = regrant(Paths("t", tmp_path), m, platform="darwin", call=lambda _a: 1,
                   server=lambda: Path("/x/ecf-server"), echo=lambda _s: None)  # fmt: skip
    assert code == 1 and m.calls == []
    said: list[str] = []
    assert regrant(Paths("t", tmp_path), m, platform="linux", call=lambda _a: 9,
                   echo=said.append) == 0  # fmt: skip
    assert "no re-grant needed" in said[0]


def test_server_regrant_refuses_while_the_service_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ecf_server.secretstore import select  # noqa: PLC0415
    from ecf_server.service import acquire_lock  # noqa: PLC0415

    monkeypatch.setenv("ECF_HOME", str(tmp_path))

    def backend(name: str) -> Callable[[select.Probe], str]:
        return lambda _probe: name

    monkeypatch.setattr(select, "choose_backend", backend("secret-service"))
    assert server_regrant.main("t") == 0
    assert "no re-grant needed" in capsys.readouterr().out
    monkeypatch.setattr(select, "choose_backend", backend("keychain"))
    from ecf.paths import paths_for  # noqa: PLC0415

    lock = acquire_lock(paths_for("t", for_service=True))
    try:
        assert server_regrant.main("t") == 3
    finally:
        lock.close()
    assert "the service is running" in capsys.readouterr().err


# ---- macOS: a real Keychain item ----------------------------------------------------------------


@pytest.fixture
def keychain_install() -> Iterator[str]:
    from ecf_server.secretstore.keyring_store import macos_keychain  # noqa: PLC0415
    from ecf_server.secretstore.macos_interaction import set_interaction_allowed  # noqa: PLC0415

    name = f"ecf-test-{secrets.token_hex(4)}"
    try:
        yield name
    finally:
        try:
            macos_keychain(name, interactive=False).delete("slack/bot")
        finally:
            set_interaction_allowed(True)


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain")
def test_regrant_reads_a_real_item_this_python_wrote(
    conn: sqlite3.Connection, clock: FakeClock, keychain_install: str
) -> None:
    """The item was written by this interpreter, so the prompts-on read is silent; what a dialog
    and Always Allow do for a different interpreter was measured by hand (§21.2, 2026-10-02)."""
    from ecf_server.secretstore.keyring_store import macos_keychain  # noqa: PLC0415

    macos_keychain(keychain_install, interactive=False).set("slack/bot", "dummy-" + "x")
    store = macos_keychain(keychain_install, interactive=True)
    got = server_regrant.run(conn, clock, store, echo=lambda _s: None, interpreter=lambda: "h")
    assert got.failed is None and got.read == ["slack/bot"]
