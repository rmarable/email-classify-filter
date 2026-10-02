"""Each address's SMTP server (V1.5 step 1a; OD-324): the default, the login check at add, and a
change that needs step-up and sends a Security Notice."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError, StepupRequiredError
from ecf_server import addresses as ad
from ecf_server import db, stepup
from ecf_server.clock import FakeClock
from ecf_server.mail.fake import FakeSender
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import GOOD, Env, Mail, call, make_state, req


class Senders:
    """A SenderFactory that records which server each check went to."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, str]] = []
        self.fake = FakeSender(size=50_000_000)

    def __call__(self, host: str, port: int, user: str, password: Callable[[], str]) -> FakeSender:
        self.calls.append((host, port, user, password()))
        return self.fake


@pytest.fixture
def env(conn: sqlite3.Connection, clock: FakeClock) -> Env:
    return conn, clock, MemorySecretStore(), Mail()


def _add(env: Env, senders: Senders, **kw: Any) -> dict[str, Any]:
    conn, clock, secrets, mail = env
    return ad.add_address(conn, clock, secrets, mail, req(**kw), actor="os_user",
                          sender_factory=senders)  # fmt: skip


def test_default_smtp_host_is_checked_without_sending(env: Env) -> None:
    s = Senders()
    a = _add(env, s)
    assert (a["smtp_host"], a["smtp_port"]) == ("smtp.acme.example", 465)
    assert s.calls == [("smtp.acme.example", 465, "ap@acme.example", GOOD)]
    assert s.fake.sent == []  # a login check only
    assert a["probe"]["smtp"] == {"ok": True, "host": "smtp.fake", "port": 465,
                                  "size": 50_000_000, "eight_bit": True}  # fmt: skip
    assert not any("SMTP" in w for w in a["probe"]["warnings"])


def test_explicit_smtp_server(env: Env) -> None:
    a = _add(env, Senders(), smtp_host="Mail.Acme.Example", smtp_port=587)
    assert (a["smtp_host"], a["smtp_port"]) == ("mail.acme.example", 587)


@pytest.mark.parametrize("port", [25, 2525, 993])
def test_only_submission_ports(env: Env, port: int) -> None:
    with pytest.raises(InvalidInputError, match="465 or 587"):
        _add(env, Senders(), smtp_host="smtp.acme.example", smtp_port=port)


def test_a_failed_smtp_check_is_a_warning_not_an_error(env: Env) -> None:
    s = Senders()
    s.fake.fail = "login"
    a = _add(env, s)
    assert a["probe"]["smtp"]["ok"] is False
    assert any(w.startswith("SMTP: fake: login rejected") for w in a["probe"]["warnings"])
    assert env[2].get("mailbox/ap") == GOOD  # the address was still added


def test_no_derivable_smtp_host_says_how_to_set_one(env: Env) -> None:
    a = _add(env, Senders(), imap_host="mail.acme.example")
    assert a["smtp_host"] is None
    assert any("--smtp-host" in w for w in a["probe"]["warnings"])


def test_changing_the_smtp_server_needs_step_up_bound_to_it(env: Env) -> None:
    conn, clock, secrets, _mail = env
    s = Senders()
    _add(env, s)
    notices: list[str] = []

    def change(host: str, nonce: str | None) -> dict[str, Any]:
        return ad.set_smtp(conn, clock, notices.append, secrets, s, "ap", host, 587,
                           actor="os_user", nonce=nonce)  # fmt: skip

    with pytest.raises(StepupRequiredError) as ei:
        change("smtp.elsewhere.example", None)
    target = ei.value.extra["target"]
    issued = stepup.issue(conn, clock, FakeStepper(), ad.SMTP_SET, target)
    assert issued.prompt.startswith(
        "ecf: send mail for ap@acme.example through"
        " smtp.elsewhere.example:587 (the app password goes there)"
    )
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):  # the nonce is for that server, not another
        change("smtp.evil.example", issued.nonce_id)
    a = change("smtp.elsewhere.example", issued.nonce_id)
    assert (a["smtp_host"], a["smtp_port"]) == ("smtp.elsewhere.example", 587)
    assert s.calls[-1][:2] == ("smtp.elsewhere.example", 587)  # checked after the step-up
    assert notices == ["ap@acme.example now sends mail through smtp.elsewhere.example:587"
                       " (was smtp.acme.example:465)."]  # fmt: skip
    row = conn.execute("SELECT data FROM audit WHERE event = 'address.smtp_set'").fetchone()
    assert json.loads(row["data"]) == {"from": ["smtp.acme.example", 465],
                                       "to": ["smtp.elsewhere.example", 587]}  # fmt: skip


def test_migration_fills_the_default_for_existing_addresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = db.connect(tmp_path / "ecf.db")
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 24])
    db.migrate(conn)
    now = "2026-10-01T00:00:00Z"
    for aid, host in (("ap", "imap.purelymail.com"), ("ops", "mail.acme.example")):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
            " VALUES (?, ?, 'high', 'A', ?)",
            (aid, f"{aid}@acme.example", now),
        )
        conn.execute(
            "INSERT INTO probe (address_id, special_use, permanent_keywords, host, probed_at)"
            " VALUES (?, '{}', 1, ?, ?)",
            (aid, host, now),
        )
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    db.migrate(conn)
    sql = "SELECT address_id, smtp_host, smtp_port FROM addresses"
    rows = {r["address_id"]: (r["smtp_host"], r["smtp_port"]) for r in conn.execute(sql)}
    assert rows == {"ap": ("smtp.purelymail.com", 465), "ops": (None, None)}


def test_api_smtp_change_needs_step_up(conn: sqlite3.Connection, db_path: Path) -> None:
    st = make_state(db_path, MemorySecretStore())
    st.sender_factory = Senders()
    body = {"email": "ap@acme.example", "imap_host": "imap.acme.example", "sensitivity": "high",
            "preset": "A", "app_password": GOOD, "org_domains": ["acme.example"],
            "smtp_port": 587, "smtp_host": "smtp.acme.example"}  # fmt: skip
    r = call(st, "POST", "/v1/addresses", body)
    assert r.status_code == 201 and r.json()["smtp_port"] == 587
    r = call(st, "POST", "/v1/addresses/ap", {"smtp_host": "smtp.other.example"})
    assert r.status_code == 403 and r.json()["code"] == "stepup_required", r.text
    r = call(st, "POST", "/v1/addresses/ap", {"smtp_host": "x.example", "app_password": GOOD})
    assert r.status_code == 400
    r = call(st, "POST", "/v1/addresses/ap", {"smtp_host": "x.example", "smtp_port": "587"})
    assert r.status_code == 400
