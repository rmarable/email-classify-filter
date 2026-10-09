"""`ecf upgrade --to <older version>` (V1.5 step 11c; OD-107, OD-331, OD-382): the merged database
after an upgrade settled, the whole snapshot before, putting things back when the older version
won't install, and the CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_upgrade
from ecf import __version__, upgrade_run
from ecf.cli import app
from ecf.errors import EcfError
from ecf.paths import Paths
from ecf_server import db, downgrade, upgrade_snapshot
from ecf_server.db import write_tx
from tests.test_addresses import make_state
from tests.test_export_keys import ApiClient
from tests.test_upgrade_run import _tools  # pyright: ignore[reportPrivateUsage]

LABEL = f"0.0.9-to-{__version__}"


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths("t", tmp_path / "home")
    c = db.connect(p.db)
    db.migrate(c)
    with write_tx(c), db.items_writer("create"):
        c.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                  " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 'A',"
                  " 't')")  # fmt: skip
        for sid, status in (("aa00aa00", "awaiting_approval"), ("bb00bb00", "approved"),
                            ("cc00cc00", "executing"), ("dd00dd00", "executed")):  # fmt: skip
            c.execute("INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"
                      " content_hash, created_at, updated_at) VALUES (?, 'ap', 1, 1, ?, 'h', 't',"
                      " 't')", (sid, status))  # fmt: skip
        c.execute("INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal,"
                  " status, expires_at) VALUES ('g1', 'aa00aa00', 'h', 'h', 'p', 'issued',"
                  " 't')")  # fmt: skip
        c.execute("INSERT INTO claims (stable_id, address_id, session_id, token_hash, fence,"
                  " need, agent, batch_id, claimed_at, expires_at, state) VALUES ('aa00aa00',"
                  " 'ap', 's', 'h', 1, 'classify', 'ecf-classifier', 'b1', 't', 't',"
                  " 'claimed')")  # fmt: skip
    c.close()
    return p


def _live(paths: Paths) -> sqlite3.Connection:
    return db.connect(paths.db)


def test_the_merged_database(paths: Paths) -> None:
    upgrade_snapshot.take(paths, LABEL)
    c = _live(paths)
    with write_tx(c):  # history written after the upgrade, which must survive going back
        c.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at)"
                  " VALUES ('m1', 'ap', 'h', 'reply', 't')")  # fmt: skip
        c.execute("INSERT INTO senders (address_id, sender_hash, dmarc_pass_count,"
                  " confirmed_category) VALUES ('ap', 's1', 3, 'invoice')")  # fmt: skip
        c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                  " VALUES ('new', 'new@acme.example', 'standard', 'A', 't')")  # fmt: skip
        c.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at)"
                  " VALUES ('m2', 'new', 'h', 'reply', 't')")  # fmt: skip
    c.close()
    out = downgrade.prepare(paths, LABEL)
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    r = sqlite3.connect(out)
    r.row_factory = sqlite3.Row
    assert [x[0] for x in r.execute("SELECT message_id_hash FROM sent")] == ["m1"]  # not 'new'
    assert r.execute("SELECT confirmed_category FROM senders").fetchone()[0] == "invoice"
    status = dict(r.execute("SELECT stable_id, status FROM items").fetchall())
    assert status == {"aa00aa00": "expired", "bb00bb00": "failed_unknown",
                      "cc00cc00": "failed_unknown", "dd00dd00": "executed"}  # fmt: skip
    assert r.execute("SELECT status FROM grants").fetchone()[0] == "voided"
    assert r.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    a = r.execute("SELECT paused, stage FROM addresses WHERE address_id = 'ap'").fetchone()
    assert (a["paused"], a["stage"]) == (1, "assist")
    assert r.execute("SELECT count(*) FROM addresses WHERE address_id = 'new'").fetchone()[0] == 0
    r.close()


def _snapshot(paths: Paths) -> Path:
    folder = upgrade_snapshot.take(paths, LABEL)
    (folder / "old-0.0.9.whl").write_bytes(b"old wheel")
    return folder


def test_before_settling_the_whole_snapshot_comes_back(paths: Paths) -> None:
    folder = _snapshot(paths)
    before = (folder / "ecf.db").read_bytes()
    c = _live(paths)
    with write_tx(c):
        c.execute("UPDATE addresses SET stage = 'shadow'")
    c.close()
    Path(str(paths.db) + "-wal").write_bytes(b"stale")
    tools, m, ran = _tools(paths)
    done = upgrade_run.downgrade(paths, tools, version="0.0.9", current=__version__,
                                 settled=False)  # fmt: skip
    assert done["phase"] == "downgraded" and done["settled"] is False
    assert paths.db.read_bytes() == before and not Path(str(paths.db) + "-wal").exists()
    stable = paths.data_dir / "releases" / "v0.0.9" / "old-0.0.9.whl"  # not in upgrades/
    assert ran == [["/usr/bin/uv", "tool", "install", "--force", str(stable)]]
    assert stable.read_bytes() == (folder / "old-0.0.9.whl").read_bytes()
    assert m.calls == ["stop", "start"]
    assert (folder / "before-downgrade" / "ecf.db").is_file()  # the newer data, kept


def test_after_settling_the_merged_copy_comes_back(paths: Paths) -> None:
    folder = _snapshot(paths)
    tools, _m, ran = _tools(paths)
    real_run = tools.run

    def run(args: list[str]) -> int:
        if args[1] == "downgrade-prepare":
            ran.append(args)
            downgrade.prepare(paths, args[-1])
            return 0
        return real_run(args)

    tools.run = run
    upgrade_run.downgrade(paths, tools, version="0.0.9", current=__version__, settled=True)
    assert ran[0][1:] == ["downgrade-prepare", "--install", "t", "--label", LABEL]
    c = _live(paths)
    assert c.execute("SELECT paused FROM addresses").fetchone()[0] == 1
    c.close()
    assert (folder / "ecf.rollback.db").is_file()


def test_a_failed_install_puts_this_version_back(paths: Paths) -> None:
    folder = _snapshot(paths)
    c = _live(paths)
    with write_tx(c):
        c.execute("UPDATE addresses SET display_name = 'newer'")
    c.close()
    tools, m, _ran = _tools(paths, {"install:old-0.0.9.whl": 1})
    with pytest.raises(EcfError, match="didn't install"):
        upgrade_run.downgrade(paths, tools, version="0.0.9", current=__version__, settled=False)
    c = _live(paths)
    assert c.execute("SELECT display_name FROM addresses").fetchone()[0] == "newer"
    c.close()
    assert m.calls == ["stop", "start"] and folder.is_dir()


def test_the_older_wheel_is_checked_by_hash_before_anything_stops(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _snapshot(paths)
    stale = paths.data_dir / "releases" / "v0.0.9" / "old-0.0.9.whl"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"something else")  # replaced by the kept wheel
    assert upgrade_run.keep_wheel(paths, "0.0.9", folder / "old-0.0.9.whl") == stale
    assert stale.read_bytes() == b"old wheel"
    real = upgrade_run.release_source.sha256

    def sha256(p: Path) -> str:
        return "0" * 64 if "releases" in p.parts else real(p)

    monkeypatch.setattr(upgrade_run.release_source, "sha256", sha256)
    tools, m, ran = _tools(paths)
    with pytest.raises(EcfError, match="doesn't match the kept wheel's sha256"):
        upgrade_run.downgrade(paths, tools, version="0.0.9", current=__version__, settled=False)
    assert m.calls == [] and ran == []


def test_without_a_snapshot_nothing_happens(paths: Paths) -> None:
    tools, m, _ran = _tools(paths)
    with pytest.raises(EcfError, match=r"no copy from 0\.0\.9"):
        upgrade_run.downgrade(paths, tools, version="0.0.9", current=__version__, settled=False)
    assert m.calls == []


def test_cli(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECF_HOME", str(paths.root))
    st = make_state(paths.db, None)

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_upgrade, "LocalClient", client)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "upgrade", "--to", "0.0.9"])
    assert r.exit_code == 1 and "no copy from 0.0.9" in r.output
    _snapshot(paths)
    c = _live(paths)
    rec: dict[str, Any] = {"from": "0.0.9", "to": __version__, "settled_at": None}
    with write_tx(c):
        c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                  " ('upgrade.current', ?, 't', 't')", (json.dumps(rec),))  # fmt: skip
        c.execute("DELETE FROM claims")
        c.execute("DELETE FROM leases")
    with write_tx(c), db.items_writer("transition"):
        c.execute("UPDATE items SET status = 'executed' WHERE status = 'executing'")
    c.close()
    r = runner.invoke(app, ["--install", "t", "upgrade", "--to", "0.0.9"], input="n\n")
    assert r.exit_code == 1 and "hasn't settled" in r.output and "Go back?" in r.output
