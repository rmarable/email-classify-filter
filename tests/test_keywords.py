"""ecf's keywords on incoming mail and `ecf init --restore` (V1.5 step 10b; SPEC §13.6; OD-318,
OD-371, OD-372): sorting keywords, the restore window, what a check does with each kind, the
send guardrail, the alert, and the init path."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_import
import ecf.cli_init
from ecf.cli import app
from ecf.paths import Paths
from ecf_server import health, keywords, policy
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.fetch import address_config, fetch_page
from ecf_server.mail.fake import FakeMailSource
from ecf_server.notify import FakeNotifier
from tests.mail_contract import message
from tests.test_fetch import ADDR, run, setup, started, take

__all__ = ["setup"]  # the fixture, used by name


def test_sort() -> None:
    flags = frozenset({"\\Seen", "$ecf_default_invoice", "$ECF_Default_Fraud", "$ecf_shop_x",
                       "$ecf_", "$ecf_noLabel", "$other_kw"})  # fmt: skip
    assert keywords.sort(flags, "default") == (["fraud", "invoice"], ["shop"])
    assert keywords.sort(frozenset(), "default") == ([], [])


def _restored(conn: sqlite3.Connection, clock: FakeClock, last_uid: int, uv: int = 1) -> None:
    with write_tx(conn):
        for k, v in ((keywords.RESTORE_AT_KEY, to_ts(clock.now())),
                     (keywords.RESTORE_CURSORS_KEY, {ADDR: [uv, last_uid]})):  # fmt: skip
            conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                         " (?, ?, 't', 't')", (k, json.dumps(v)))  # fmt: skip


def test_restore_window(setup: sqlite3.Connection, clock: FakeClock) -> None:
    assert not keywords.in_restore_window(setup, clock, ADDR, 1, 5)
    _restored(setup, clock, last_uid=3)
    assert keywords.in_restore_window(setup, clock, ADDR, 1, 4)
    assert not keywords.in_restore_window(setup, clock, ADDR, 1, 3)
    assert not keywords.in_restore_window(setup, clock, ADDR, 2, 4)  # another UIDVALIDITY
    assert not keywords.in_restore_window(setup, clock, "other", 1, 4)
    clock.advance(keywords.WINDOW.total_seconds() + 1)
    assert not keywords.in_restore_window(setup, clock, ADDR, 1, 4)


def _fetch(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource) -> Any:
    return fetch_page(conn, clock, src, address_config(conn, ADDR), take(conn, clock),
                      install="default")  # fmt: skip


def _paused(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT paused FROM addresses WHERE address_id = ?",
                             (ADDR,)).fetchone()[0])  # fmt: skip


def test_own_keywords_are_adopted_and_block_sends(setup: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    src = FakeMailSource()
    started(setup, clock, src)
    uid = src.deliver(message(1))
    src.add_keyword(uid, "$ecf_default_invoice")
    r = _fetch(setup, clock, src)
    assert len(r.created) == 1 and not r.second_install and not r.restored_keywords
    facts = json.loads(setup.execute("SELECT facts FROM items").fetchone()[0])
    assert facts["ecf_keywords"] == ["invoice"]
    assert not _paused(setup)
    for name in ("reply_template", "draft_reply", "forward_internal"):
        why = policy.send_refusal(name, {**facts, "from_count": 1, "auth_result": "pass"},
                                  fraud_signal=False)  # fmt: skip
        assert why == "ecf handled this email before (its labels are on it)"


def test_another_installs_keywords_pause(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    uid = src.deliver(message(1))
    src.add_keyword(uid, "$ecf_laptop_invoice")
    r = _fetch(setup, clock, src)
    assert r.second_install and len(r.created) == 1 and _paused(setup)
    why = setup.execute("SELECT data FROM audit WHERE event = 'address.paused'").fetchone()[0]
    assert json.loads(why) == {"why": "second_install"}


def test_own_keywords_on_new_mail_after_a_restore_pause(setup: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    src = FakeMailSource()
    started(setup, clock, src)
    old = src.deliver(message(1))
    run(setup, clock, src)  # the backup's cursor: up to `old`
    _restored(setup, clock, last_uid=old)
    new = src.deliver(message(2))
    src.add_keyword(new, "$ecf_default_invoice")  # labelled by a copy still running elsewhere
    r = _fetch(setup, clock, src)
    assert r.restored_keywords and _paused(setup) and len(r.created) == 1
    n = FakeNotifier()
    report = CheckReport(ADDR, "ok", to_ts(clock.now()))
    report.restored_keywords = True
    health.after_check(setup, clock, n, report)
    alerts = health.open_alerts(setup)
    assert [a["kind"] for a in alerts] == ["restored_keywords"]
    assert "another copy of this install may still be running" in alerts[0]["detail"]
    with write_tx(setup):
        setup.execute("UPDATE addresses SET paused = 0")
    health.after_check(setup, clock, n, CheckReport(ADDR, "ok", to_ts(clock.now())))
    assert not health.open_alerts(setup)  # resolved at the first check after resume


def test_without_the_install_name_keywords_are_not_read(setup: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    src = FakeMailSource()
    started(setup, clock, src)
    uid = src.deliver(message(1))
    src.add_keyword(uid, "$ecf_laptop_invoice")
    r = run(setup, clock, src)
    assert not r.second_install and not _paused(setup)


def test_init_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def service(_p: Paths, _m: Any) -> None:
        calls.append("service")

    def restore(_p: Paths, path: str) -> dict[str, Any]:
        calls.append(f"restore {path}")
        return {"addresses": ["ap", "billing"]}

    monkeypatch.setattr(ecf.cli_init, "_service", service)
    monkeypatch.setattr(ecf.cli_init, "require_terminal", lambda: None)
    monkeypatch.setattr(ecf.cli_import, "restore", restore)
    bundle = tmp_path / "b.ecfb"
    r = CliRunner().invoke(app, ["--install", "t", "init", "--restore", str(bundle)])
    assert r.exit_code == 0, r.output
    assert calls == ["service", f"restore {bundle}"]
    assert "ecf address set billing --app-password, ecf check billing, then ecf resume" in r.output
    assert "ecf doctor" in r.output
