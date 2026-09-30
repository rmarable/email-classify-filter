"""`ecf init` and `ecf init status` (V1.2 step 11c)."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from ecf import cli_init, doctor
from ecf.client import LocalClient
from ecf.errors import ConflictError, InvalidInputError
from ecf.paths import Paths
from ecf.service_unit import UnitStatus
from ecf_server import initsetup
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx


def test_the_role_is_set_once(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert initsetup.role(conn) is None
    with pytest.raises(InvalidInputError, match="prod or test"):
        initsetup.set_role(conn, clock, "staging")
    assert initsetup.set_role(conn, clock, "test") == {"install_role": "test", "changed": True}
    assert initsetup.set_role(conn, clock, "test")["changed"] is False
    with pytest.raises(ConflictError, match="fixed"):
        initsetup.set_role(conn, clock, "prod")
    events = [r[0] for r in conn.execute("SELECT event FROM audit")]
    assert events == ["init.role_set"]


def test_status_reads_the_service_state(conn: sqlite3.Connection, clock: FakeClock) -> None:
    st = initsetup.status(conn)
    assert st == {"install_role": None, "slack_installed": False, "slack_member": None,
                  "slack_pending_app": None, "org_domains": [], "addresses": []}  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')")  # fmt: skip
    assert initsetup.status(conn)["addresses"] == ["ap"]


def test_describe() -> None:
    st = {"install_role": "prod", "slack_installed": True, "slack_member": None,
          "slack_pending_app": None, "org_domains": ["acme.example"], "addresses": []}  # fmt: skip
    lines = cli_init.describe(st, installed=True, running=True)
    assert lines[0].split()[:2] == ["service", "done"]
    assert "click Confirm" in lines[2] and "to do" in lines[2]
    assert lines[3].endswith("acme.example") and "ecf address add" in lines[4]


def _write(paths: Paths, sql: str, *args: Any) -> None:
    """Change the running test service's database, as a real step would through the service."""
    db = sqlite3.connect(paths.db, autocommit=True)
    try:
        with write_tx(db):
            db.execute(sql, args)
    finally:
        db.close()


class FakeManager:
    def __init__(self, *, installed: bool = False, running: bool = False) -> None:
        self.s = UnitStatus(installed=installed, running=running)
        self.calls: list[str] = []

    def status(self) -> UnitStatus:
        return self.s

    def install(self) -> None:
        self.calls.append("install")
        self.s = UnitStatus(installed=True, running=True)

    def start(self) -> None:
        self.calls.append("start")


def _app(paths: Paths, added: list[tuple[Any, ...]]) -> typer.Typer:
    app = typer.Typer()

    @app.command("noop")
    def _noop() -> None: ...

    def add(c: LocalClient, *args: Any) -> None:
        added.append(args)
        _write(paths, "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                      " VALUES ('ap', ?, 'high', 'A', 'now')", args[0])  # fmt: skip

    cli_init.make_commands(app, lambda: paths, add)
    return app


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    m = FakeManager()

    def manager_for(_p: Paths) -> FakeManager:
        return m

    def secret_store(_c: LocalClient) -> tuple[str | None, str]:
        return "test-store", ""  # the host's own store varies (CI's Linux runners have none)

    monkeypatch.setattr(cli_init, "manager_for", manager_for)
    monkeypatch.setattr(cli_init, "secret_store", secret_store)
    monkeypatch.setattr(cli_init, "require_terminal", lambda: None)
    monkeypatch.setattr(cli_init.doctor, "check_disk_encryption",
                        lambda: doctor.Check("disk encryption", doctor.Level.OK, "On"))  # fmt: skip
    return m


def test_init_runs_the_steps_then_resumes_without_repeating_them(
    running: Paths, quiet: FakeManager
) -> None:
    quiet.s = UnitStatus(installed=True, running=True)  # the service answering is the unit
    added: list[tuple[Any, ...]] = []
    app = _app(running, added)
    # checklist, Ready, role, skip Slack, add the first mailbox
    r = CliRunner().invoke(
        app, ["init"], input="\ntest\nn\ny\nap@acme.example\nimap.acme.example\n"
    )
    assert r.exit_code == 0, r.output
    assert "Have ready" in r.output and "service: running" in r.output
    assert "secret store:" in r.output and "install role: test" in r.output
    assert "slack: skipped" in r.output
    assert added == [("ap@acme.example", "imap.acme.example", None, None, None)]
    assert "service unit: installed and running" in r.output and quiet.calls == []
    r = CliRunner().invoke(app, ["init", "--resume"], input="n\n")  # only Slack is left to ask
    assert r.exit_code == 0, r.output
    assert "Have ready" not in r.output and "first address: added" in r.output
    assert len(added) == 1 and quiet.calls == []  # nothing done twice
    r = CliRunner().invoke(app, ["init", "status"])
    assert r.exit_code == 0, r.output
    assert "install role  done   test" in r.output and "first address done   ap" in r.output
    assert "slack         to do  ecf slack install" in r.output


def test_init_refuses_other_modes_and_unencrypted_disks_unless_you_say_so(
    running: Paths, quiet: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(running, [])
    r = CliRunner().invoke(app, ["init", "--mode", "aws"])
    assert r.exit_code == 2  # usage error (the text is styled by Rich)
    monkeypatch.setattr(cli_init.doctor, "check_disk_encryption",
                        lambda: doctor.Check("disk encryption", doctor.Level.FAIL, "Off",
                                             "turn on FileVault"))  # fmt: skip
    r = CliRunner().invoke(app, ["init", "--resume"], input="n\n")
    assert r.exit_code == 1 and "Full-disk encryption is required" in r.output


def test_a_foreground_service_is_left_alone(running: Paths, quiet: FakeManager) -> None:
    app = _app(running, [])
    _write(running, "INSERT INTO settings (key, value, updated_at, updated_by)"
                    " VALUES ('install_role', '\"test\"', 'now', 't')")  # fmt: skip
    _write(running, "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                    " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')")  # fmt: skip
    r = CliRunner().invoke(app, ["init", "--resume"], input="n\n")
    assert r.exit_code == 0, r.output
    assert "outside its unit" in r.output and quiet.calls == []


def test_no_usable_secret_store_stops_init(
    running: Paths, quiet: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none(_c: LocalClient) -> tuple[str | None, str]:
        return None, "no keyring"

    monkeypatch.setattr(cli_init, "secret_store", none)
    r = CliRunner().invoke(_app(running, []), ["init", "--resume"])
    assert r.exit_code == 3 and "secret store: unavailable (no keyring)" in r.output
