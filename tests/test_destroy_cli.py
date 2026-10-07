"""`ecf destroy`, the CLI's part (V1.5 step 12b; OD-383 to OD-393): the whole run against the
service, the export offer, the typed name, refusals, the foreground fallback, resuming from the
record, the residue, deleting only this install's folder, and `ecf init`'s note."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import ecf.cli_destroy
from ecf import cli_destroy, cli_init, upgrade_run
from ecf.cli import app
from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths
from ecf.service_unit import UnitStatus
from ecf_server import db, destroy
from ecf_server.api import ServiceState
from ecf_server.clock import to_ts
from ecf_server.db import write_tx
from ecf_server.stepper import FakeStepper
from tests.test_addresses import make_state
from tests.test_destroy import Env, make_env
from tests.test_export_keys import ApiClient


class Manager:
    unit_path = Path("/dev/null")

    def __init__(self, *, running: bool = False) -> None:
        self.calls: list[str] = []
        self.running = running

    def install(self) -> None: ...
    def start(self) -> None: ...
    def restart(self) -> None: ...

    def uninstall(self) -> None:
        self.calls.append("uninstall")

    def stop(self) -> None:
        self.calls.append("stop")

    def status(self) -> UnitStatus:
        return UnitStatus(installed=True, running=self.running)


class Fakes:
    def __init__(self, answers: list[str], confirms: list[bool]) -> None:
        self.out: list[str] = []
        self.answers, self.confirms = answers, confirms
        self.exported: list[str] = []
        self.ran: list[tuple[list[str], dict[str, str]]] = []
        self.fg: list[list[str]] = []
        self.fg_code = 0
        self.manager = Manager()

    def tools(self) -> cli_destroy.Tools:
        return cli_destroy.Tools(
            self.manager, echo=self.out.append, prompt=lambda _t: self.answers.pop(0),
            confirm=lambda _t, _d: self.confirms.pop(0), secret=lambda _t: "xoxe-config",
            export=lambda _p, to: self.exported.append(to), foreground=self._fg,
            run=self._run, server=lambda: Path("/env/bin/ecf-server"),
            claude=lambda: "/bin/claude", wait_s=0,
        )  # fmt: skip

    def _fg(self, args: list[str]) -> int:
        self.fg.append(args)
        return self.fg_code

    def _run(self, args: list[str], env: dict[str, str]) -> int:
        self.ran.append((args, env))
        return 0

    def text(self) -> str:
        return "\n".join(self.out)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths("t", tmp_path)


@pytest.fixture
def tconn(paths: Paths) -> Iterator[sqlite3.Connection]:
    c = db.connect(paths.db)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def env(tconn: sqlite3.Connection, paths: Paths) -> Env:
    return make_env(tconn, paths.root)


@pytest.fixture
def st(env: Env, paths: Paths, monkeypatch: pytest.MonkeyPatch) -> ServiceState:
    s = make_state(paths.db, env.store)
    s.install, s.clock, s.slack_web, s.stepper = "t", env.clock, env.web, FakeStepper()
    s.request_stop = lambda: None

    def client(_p: Paths) -> ApiClient:
        return ApiClient(s)

    monkeypatch.setattr(ecf.cli_destroy, "LocalClient", client)
    return s


def test_whole_run(st: ServiceState, env: Env, paths: Paths) -> None:
    del st
    (paths.data_dir / "claude-config").mkdir()
    f = Fakes(answers=["t"], confirms=[False])  # no export
    assert cli_destroy.run(paths, f.tools(), ask_token=False)
    assert not paths.data_dir.exists()
    assert f.manager.calls == ["uninstall"]
    assert f.ran[0][0] == ["/bin/claude", "auth", "logout"]
    assert f.ran[0][1]["CLAUDE_CONFIG_DIR"] == str(paths.data_dir / "claude-config")
    rec = cli_destroy.read_record(paths)
    assert rec is not None and rec["phase"] == cli_destroy.DONE
    assert rec["cli"] == {"unit": "removed", "claude_logout": "ok", "data_dir": "deleted"}
    assert rec["steps"]["secrets"]["deleted"]
    assert ".ecfcorpus files you made" in f.text()  # kept; the operator deletes them (R93)
    out = f.text()
    assert "This deletes the ecf install t (role not set: init wasn't run)" in out
    assert "watching ap@acme.example" in out
    assert "No export in the last 24 hours." in out
    assert "Slack app A1 still exists" in out
    assert "revoke the app password for ap@acme.example" in out
    assert "no other ecf install is left: `ecf models serve uninstall`" in out
    assert f"kept at {cli_destroy.record_path(paths)}" in out
    assert env.store.get("slack/bot") is None
    # run again: it says so and prints what's left
    g = Fakes(answers=[], confirms=[])
    assert cli_destroy.run(paths, g.tools(), ask_token=False)
    assert "t was destroyed on" in g.text() and "Slack app A1" in g.text()


def test_config_token_and_export(st: ServiceState, env: Env, paths: Paths) -> None:
    del st
    f = Fakes(answers=["/backups", "t"], confirms=[True])
    assert cli_destroy.run(paths, f.tools(), ask_token=True)
    assert f.exported == ["/backups"]
    assert env.webs["xoxe-config"].methods() == ["apps.manifest.delete"]
    assert "still exists" not in f.text()


def test_recent_export_isnt_offered_again(st: ServiceState, env: Env, paths: Paths) -> None:
    del st
    with write_tx(env.conn):
        env.conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                         " 'export.completed', 'os_user', 'ok', '{}')",
                         (to_ts(datetime.now(UTC)),))  # fmt: skip
    f = Fakes(answers=["t"], confirms=[])
    assert cli_destroy.run(paths, f.tools(), ask_token=False)
    assert "Newest export:" in f.text() and f.exported == []


def test_wrong_name_destroys_nothing(st: ServiceState, env: Env, paths: Paths) -> None:
    del st
    f = Fakes(answers=["prod"], confirms=[False])
    assert not cli_destroy.run(paths, f.tools(), ask_token=False)
    assert "nothing was destroyed" in f.text()
    assert paths.db.exists() and cli_destroy.read_record(paths) is None
    assert env.store.get("slack/bot") is not None and f.manager.calls == []


def test_refuses_mid_upgrade_and_while_busy(st: ServiceState, paths: Paths) -> None:
    upgrade_run.write_state(paths, {"from": "0.1.0", "to": "0.2.0", "phase": "migrating"})
    st.sessions["s"] = object()  # type: ignore[assignment]
    f = Fakes(answers=[], confirms=[])
    assert not cli_destroy.run(paths, f.tools(), ask_token=False)
    assert "can't destroy: an upgrade to 0.2.0 is in progress" in f.text()
    assert "can't destroy: 1 `ecf claude` session(s) are open" in f.text()
    assert paths.db.exists()


def test_unsettled_upgrade_waits(st: ServiceState, env: Env, paths: Paths) -> None:
    del st
    with write_tx(env.conn):
        env.conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                         " ('upgrade.current', ?, 't', 'upgrade')",
                         (json.dumps({"to": "0.2.0", "settled_at": None}),))  # fmt: skip
    f = Fakes(answers=[], confirms=[])
    assert not cli_destroy.run(paths, f.tools(), ask_token=False)
    assert "hasn't settled yet" in f.text()


def _down(_p: Paths) -> Any:
    raise ServiceUnavailableError("ecf isn't running")


def test_foreground_when_the_service_is_down(
    env: Env, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    del env
    monkeypatch.setattr(ecf.cli_destroy, "LocalClient", _down)
    f = Fakes(answers=["t"], confirms=[True])
    f.manager.running = True  # running but not answering: stopped first
    assert cli_destroy.run(paths, f.tools(), ask_token=True)
    assert f.fg == [["/env/bin/ecf-server", "destroy", "--install", "t", "--confirm", "t",
                     "--config-token"]]  # fmt: skip
    assert f.manager.calls == ["stop", "uninstall"]
    assert "no export can be made now" in f.text()
    assert not paths.data_dir.exists()


def test_a_failed_foreground_keeps_everything(
    env: Env, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    del env
    monkeypatch.setattr(ecf.cli_destroy, "LocalClient", _down)
    f = Fakes(answers=["t"], confirms=[True])
    f.fg_code = 1
    assert not cli_destroy.run(paths, f.tools(), ask_token=False)
    assert "didn't finish (exit 1); run ecf destroy again" in f.text()
    assert paths.db.exists() and f.manager.calls == []


def test_declining_the_foreground_changes_nothing(
    env: Env, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    del env
    monkeypatch.setattr(ecf.cli_destroy, "LocalClient", _down)
    f = Fakes(answers=[], confirms=[False])
    assert not cli_destroy.run(paths, f.tools(), ask_token=False)
    assert f.fg == [] and paths.db.exists()


def test_resumes_after_the_service_part(
    env: Env, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    del env
    destroy.write_record(
        paths.root,
        "t",
        {
            "install": "t",
            "phase": destroy.SERVICE_DONE,
            "steps": {},
            "residue": ["a line from before"],
        },
    )
    monkeypatch.setattr(ecf.cli_destroy, "LocalClient", _down)  # never asked
    (paths.root / "other").mkdir()
    (paths.root / "other" / "ecf.db").touch()
    (paths.root / "ollama").mkdir()
    f = Fakes(answers=[], confirms=[])
    assert cli_destroy.run(paths, f.tools(), ask_token=False)
    assert not paths.data_dir.exists()
    assert f.ran == []  # no claude-config folder: nothing to sign out of
    out = f.text()
    assert "a line from before" in out
    assert "shared with other, so they stay" in out


def test_a_symlinked_data_folder_is_unlinked_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").touch()
    root = tmp_path / "root"
    root.mkdir()
    (root / "t").symlink_to(target)
    p = Paths("t", root)
    destroy.write_record(root, "t", {"install": "t", "phase": destroy.SERVICE_DONE})
    f = Fakes(answers=[], confirms=[])
    assert cli_destroy.run(p, f.tools(), ask_token=False)
    assert not (root / "t").exists() and (target / "keep").exists()


def test_init_notes_or_refuses(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    cli_init._destroyed_before(paths)  # pyright: ignore[reportPrivateUsage]
    destroy.write_record(paths.root, "t", {"install": "t", "phase": destroy.SERVICE_DONE})
    with pytest.raises(typer.Exit):
        cli_init._destroyed_before(paths)  # pyright: ignore[reportPrivateUsage]
    destroy.write_record(
        paths.root,
        "t",
        {"install": "t", "phase": destroy.DONE, "done_at": "2026-10-03T12:00:00+00:00"},
    )
    cli_init._destroyed_before(paths)  # pyright: ignore[reportPrivateUsage]
    assert "was destroyed here on 2026-10-03" in capsys.readouterr().out


def test_help_marks_step_up() -> None:
    r = CliRunner().invoke(app, ["destroy", "--help"])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", r.output)  # Rich colors help on GitHub Actions
    assert r.exit_code == 0
    assert "(step-up)" in plain and "--config-token" in plain
