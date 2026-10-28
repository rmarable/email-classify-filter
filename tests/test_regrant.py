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

from ecf.errors import ServiceUnavailableError, UnauthorizedError
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
                         " created_at, removed_at) VALUES (?, ?, 'standard', 'C', 't', ?)",
                         (aid, f"{aid}@acme.example", removed))  # fmt: skip


def _events(conn: sqlite3.Connection) -> list[tuple[str, dict[str, object]]]:
    rows = conn.execute("SELECT outcome, data FROM audit WHERE event = 'secret.regranted'")
    return [(r[0], json.loads(r[1])) for r in rows]


def test_names_cover_every_secret_ecf_keeps(conn: sqlite3.Connection) -> None:
    _addresses(conn)
    assert server_regrant.names(conn) == ["mailbox/ap", "slack/bot", "slack/app",
                                          "models-api-key", "jev-api-key",
                                          "mistral-api-key", "export-signing-seed"]  # fmt: skip


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
    assert got.absent == ["slack/app", "models-api-key", "jev-api-key", "mistral-api-key",
                          "export-signing-seed"]  # fmt: skip
    assert said[0] == "reading mailbox/ap ..."
    assert not interpreter_changed(conn, "new")
    row = conn.execute("SELECT updated_by FROM settings WHERE key ="
                       " 'secret_store.interpreter_sha256'").fetchone()  # fmt: skip
    assert row[0] == "regrant"
    assert _events(conn) == [("ok", {"read": 2, "absent": 5})]


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


def _cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, launchd: bool,
         pid: int | None) -> tuple[int, str, list[str]]:  # fmt: skip
    """`ecf service regrant` on macOS with launchd running the service or not, and the service
    answering with `pid` or not: its exit code, its error output, and what it did."""
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf import cli  # noqa: PLC0415

    did: list[str] = []
    m = _Manager(running=launchd)
    monkeypatch.setenv("ECF_HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    def manager_for(_p: Paths) -> _Manager:
        return m

    def service_pid(_p: Paths) -> int | None:
        return pid

    monkeypatch.setattr(cli, "manager_for", manager_for)
    monkeypatch.setattr(cli, "_service_pid", service_pid)
    monkeypatch.setattr(cli, "_stopping_on_purpose", lambda: did.append("stopping"))

    def fake_regrant(_paths: Paths, manager: _Manager, **_kw: object) -> int:
        assert manager is m
        did.append("regrant")
        return 0

    monkeypatch.setattr(cli.service_unit, "regrant", fake_regrant)
    r = CliRunner().invoke(cli.app, ["--install", "t", "service", "regrant"])
    return r.exit_code, r.stderr, did


def test_service_regrant_refuses_a_service_launchd_isnt_running_naming_its_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It used to send the stop signal, which turned the running service's dead-man's switch
    off, then fail on the service's lock with "stop it first (`ecf service regrant` does this for
    you)" (SPEC §21.2, 2026-10-28)."""
    code, err, did = _cli(monkeypatch, tmp_path, launchd=False, pid=4242)
    assert code == 3 and did == []  # no stop signal, no re-grant
    assert err.startswith("launchd isn't running the service (pid 4242); stop it (Ctrl-C in its"
                          " terminal, or `kill 4242`), run this again, then start it again from"
                          " this ecf's environment (")  # fmt: skip


def test_service_regrant_signals_the_stop_only_when_launchd_will_stop_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _cli(monkeypatch, tmp_path, launchd=True, pid=4242)[2] == ["stopping", "regrant"]
    assert _cli(monkeypatch, tmp_path, launchd=False, pid=None)[2] == ["regrant"]  # not running


class _Status:
    def __init__(self, reply: object) -> None:
        self.reply = reply

    def __enter__(self) -> _Status:
        return self

    def __exit__(self, *_a: object) -> None: ...

    def get(self, _path: str) -> object:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.parametrize(("reply", "pid"), [
    ({"pid": 4242}, 4242),
    (ServiceUnavailableError("not running"), None),
    (UnauthorizedError("can't read the CLI token file"), None),
    ({}, None),
    ({"pid": "x"}, None),
])  # fmt: skip
def test_the_cli_asks_the_service_for_its_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reply: object, pid: int | None
) -> None:
    from ecf import cli  # noqa: PLC0415

    def client(_p: Paths) -> _Status:
        return _Status(reply)

    monkeypatch.setattr(cli, "LocalClient", client)
    assert cli._service_pid(Paths("t", tmp_path)) == pid  # pyright: ignore[reportPrivateUsage]


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
    assert capsys.readouterr().err == ("ecf-server: the service is running; stop it first (`ecf"
                                       " service regrant` stops it for you only when launchd"
                                       " runs it)\n")  # fmt: skip


# macOS only: regrant reads a real Keychain item this Python wrote.


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
