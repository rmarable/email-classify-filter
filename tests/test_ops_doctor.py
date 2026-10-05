"""`ecf doctor`'s sending, alert-email and backup checks (V1.5 step 13a; OD-394 to OD-398)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf.doctor import Level, check_ops
from ecf.paths import Paths
from ecf_server import alert_mail, export_keys, ops_doctor, scheduled_export
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.test_addresses import call, make_state
from tests.test_export_keys import ApiClient


def _set(conn: sqlite3.Connection, key: str, value: Any, at: str = "t") -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 't') ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                     " updated_at = excluded.updated_at", (key, json.dumps(value), at))  # fmt: skip


def _address(conn: sqlite3.Connection, aid: str = "ap", *, smtp: str | None = "smtp.acme.example",
             outbound: bool = False, probe: dict[str, Any] | None = None,
             probed_at: str = "2026-10-01T00:00:00.000000Z") -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " smtp_host, smtp_port, outbound) VALUES (?, ?, 'standard', 'A', 't', ?, ?,"
                     " ?)", (aid, f"{aid}@acme.example", smtp, 465 if smtp else None,
                             int(outbound)))  # fmt: skip
        conn.execute("INSERT INTO probe (address_id, host, probed_at, smtp)"
                     " VALUES (?, 'imap', ?, ?)",
                     (aid, probed_at, None if probe is None else json.dumps(probe)))  # fmt: skip


def _by_name(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["name"]: r for r in rows}


# ---- Gmail --------------------------------------------------------------------------------------


def test_gmail_rows(conn: sqlite3.Connection) -> None:
    """V1.6 (OD-438, OD-440): only for addresses in Gmail mode; All Mail and the folder limit."""
    for aid in ("plain", "ok", "hidden", "limited"):
        _address(conn, aid)
    rows = {"ok": ('{"\\\\All": "[Gmail]/All Mail"}', [10, 10]),
            "hidden": ("{}", None),
            "limited": ('{"\\\\All": "[Gmail]/All Mail"}', [1000, 4211])}  # fmt: skip
    with write_tx(conn):
        for aid, (roles, counts) in rows.items():
            caps = json.dumps({"gmail": True, "gmail_inbox": counts})
            conn.execute("UPDATE probe SET special_use = ?, capabilities = ? WHERE address_id = ?",
                         (roles, caps, aid))  # fmt: skip
    got = _by_name(ops_doctor.gmail(conn))
    assert set(got) == {"gmail ok", "gmail hidden", "gmail limited"}
    assert got["gmail ok"]["level"] == "ok"
    assert got["gmail hidden"]["level"] == "warn" and "Show in IMAP" in got["gmail hidden"]["fix"]
    assert got["gmail limited"]["detail"] == "IMAP shows 1000 of 4211 inbox messages"


# ---- SMTP ---------------------------------------------------------------------------------------


def test_smtp_rows_warn_or_fail_by_use(conn: sqlite3.Connection) -> None:
    _address(conn, "a1", smtp=None)
    _address(conn, "a2", smtp=None, outbound=True)
    _address(conn, "a3", probe={"ok": False, "error": "535 auth failed"})
    _address(conn, "a4", probe={"ok": True})
    _address(conn, "a5", probe=None)
    _set(conn, alert_mail.FROM_KEY, "a3")
    _set(conn, alert_mail.TO_KEY, "me@home.example")
    rows = _by_name(ops_doctor.smtp(conn))
    assert rows["smtp a1"]["level"] == "warn" and "--smtp-host" in rows["smtp a1"]["fix"]
    assert rows["smtp a2"]["level"] == "FAIL" and "outbound is on" in rows["smtp a2"]["detail"]
    assert rows["smtp a3"]["level"] == "FAIL"
    assert "535 auth failed" in rows["smtp a3"]["detail"]
    assert "sends alert email" in rows["smtp a3"]["detail"]
    assert rows["smtp a4"]["level"] == "ok"
    assert rows["smtp a5"]["level"] == "warn" and "not checked" in rows["smtp a5"]["detail"]


def test_a_send_accepted_after_a_failed_check_is_ok(conn: sqlite3.Connection) -> None:
    _address(conn, probe={"ok": False, "error": "timeout"}, outbound=True)
    with write_tx(conn):
        conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                     " status) VALUES ('m', 'ap', 'c', 'reply', '2026-10-02T09:00:00.000000Z',"
                     " 'sent')")  # fmt: skip
    (row,) = ops_doctor.smtp(conn)
    assert row["level"] == "ok" and "last send accepted 2026-10-02T09:00Z" in row["detail"]


# ---- alert email and reach ----------------------------------------------------------------------


def _outbox(conn: sqlite3.Connection, state: str, created: str, settled: str | None = None) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO alert_outbox (kind, subject, body, address_id, destination,"
                     " created_at, state, next_at, settled_at) VALUES ('System Error', 's', 'b',"
                     " 'ap', 'me@home.example', ?, ?, ?, ?)",
                     (created, state, created, settled))  # fmt: skip


def test_alert_email_rows(conn: sqlite3.Connection, clock: FakeClock) -> None:
    (off,) = ops_doctor.alert_email(conn, clock.now())
    assert off["level"] == "ok" and off["detail"].startswith("off")
    _address(conn, probe={"ok": True})
    _set(conn, alert_mail.FROM_KEY, "ap")
    _set(conn, alert_mail.TO_KEY, "me@home.example")
    (on,) = ops_doctor.alert_email(conn, clock.now())
    assert on["level"] == "ok" and "to me@home.example" in on["detail"]
    assert "none delivered" in on["detail"]
    now = clock.now()
    _outbox(conn, "sent", to_ts(now - timedelta(hours=1)), to_ts(now - timedelta(hours=1)))
    _outbox(conn, "queued", to_ts(now - timedelta(minutes=2)))
    (fresh,) = ops_doctor.alert_email(conn, now)
    assert fresh["level"] == "ok" and "1 queued" in fresh["detail"]
    assert "last delivered" in fresh["detail"]
    (late,) = ops_doctor.alert_email(conn, now + timedelta(minutes=9))
    assert late["level"] == "warn" and "the oldest for 11 min" in late["detail"]


def test_reach_warns_when_nothing_but_slack_is_left(conn: sqlite3.Connection) -> None:
    assert ops_doctor.reach(conn, "macos") == []
    (none,) = ops_doctor.reach(conn, "none")
    assert none["level"] == "warn" and "no desktop notifier" in none["detail"]
    _set(conn, "notifications", "off")
    (off,) = ops_doctor.reach(conn, "macos")
    assert "desktop notifications are off" in off["detail"]
    assert "notifications on" in off["fix"]
    _address(conn, probe={"ok": True})
    _set(conn, alert_mail.FROM_KEY, "ap")
    _set(conn, alert_mail.TO_KEY, "me@home.example")
    assert ops_doctor.reach(conn, "none") == []


# ---- backups ------------------------------------------------------------------------------------


@pytest.fixture
def data_dir(db_path: Path) -> Path:
    return db_path.parent


def _backups(conn: sqlite3.Connection, folder: Path, set_up_at: str) -> None:
    _set(conn, export_keys.KEY_KEY, {"generation": 1, "fingerprint": "F"}, set_up_at)
    _set(conn, export_keys.DIR_KEY, str(folder), set_up_at)


def test_backups_not_set_up(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path) -> None:
    (row,) = ops_doctor.backups(conn, clock.now(), data_dir)
    assert row["level"] == "warn" and "no backup key" in row["detail"]
    _set(conn, export_keys.KEY_KEY, {"generation": 1, "fingerprint": "F"})
    (row,) = ops_doctor.backups(conn, clock.now(), data_dir)
    assert "no backup folder" in row["detail"] and row["fix"] == "ecf export dir set <directory>"


def test_backup_folder_missing_fails_and_same_disk_warns(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _backups(conn, tmp_path / "gone", to_ts(clock.now()))
    folder, _age = ops_doctor.backups(conn, clock.now(), data_dir)
    assert folder["level"] == "FAIL" and "isn't an existing directory" in folder["detail"]
    there = tmp_path / "backups"
    there.mkdir()
    _backups(conn, there, to_ts(clock.now()))
    folder, _age = ops_doctor.backups(conn, clock.now(), data_dir)
    assert folder["level"] == "warn" and "same disk" in folder["detail"]  # tmp_path: one disk
    monkeypatch.setattr(export_keys, "same_volume_or_none", lambda *_a: False)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    folder, _age = ops_doctor.backups(conn, clock.now(), data_dir)
    assert folder["level"] == "ok"


def test_backup_age_levels(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path,
                           tmp_path: Path) -> None:  # fmt: skip
    there = tmp_path / "backups"
    there.mkdir()
    now = clock.now()
    _backups(conn, there, to_ts(now - timedelta(hours=1)))

    def age(when: datetime = now) -> dict[str, str]:
        return ops_doctor.backups(conn, when, data_dir)[-1]

    first = age()
    assert first["level"] == "ok" and "first backup is due" in first["detail"]
    assert age(when=now + timedelta(hours=30))["level"] == "warn"  # 31 h since setup, none yet
    assert age(when=now + timedelta(hours=50))["level"] == "FAIL"
    _set(conn, scheduled_export.LAST_OK, {"at": to_ts(now - timedelta(hours=20))})
    assert age()["level"] == "ok" and "last backup 20 h ago" in age()["detail"]
    _set(conn, scheduled_export.LAST_OK, {"at": to_ts(now - timedelta(hours=30))})
    assert age()["level"] == "warn"
    _set(conn, scheduled_export.LAST_OK, {"at": to_ts(now - timedelta(days=3))})
    late = age()
    assert late["level"] == "FAIL" and "3 days ago" in late["detail"]
    assert "ecf export now" in late["fix"]
    _set(conn, scheduled_export.LAST_OK, {"at": to_ts(now - timedelta(hours=3))})
    _set(conn, scheduled_export.FAILURES, 1)
    _set(conn, scheduled_export.LAST_ERROR, "folder not writable")
    failing = age()
    assert failing["level"] == "warn" and "folder not writable" in failing["detail"]
    _set(conn, scheduled_export.FAILURES, 0)
    _set(conn, scheduled_export.SCHEDULE_KEY, "weekly")
    _set(conn, scheduled_export.LAST_OK, {"at": to_ts(now - timedelta(days=8))})
    assert age()["level"] == "warn"
    _set(conn, scheduled_export.SCHEDULE_KEY, "off")
    off = age()
    assert off["level"] == "warn" and "export_schedule: off" in off["detail"]


# ---- the route and doctor -----------------------------------------------------------------------


def test_the_route_and_doctor_rows(conn: sqlite3.Connection, db_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _address(conn, probe={"ok": True})
    st = make_state(db_path, None)
    r = call(st, "GET", "/v1/doctor/ops")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["checks"]]
    assert names[:2] == ["smtp ap", "alert email"] and "backups" in names

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr("ecf.doctor.LocalClient", client)
    rows = check_ops(Paths("t", db_path.parent))
    assert any(c.name == "backups" and c.level is Level.WARN for c in rows)
