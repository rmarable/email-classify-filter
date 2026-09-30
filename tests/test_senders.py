"""`ecf sender confirm|set-reply-to|set-verified|show` (V1.2 step 10c)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf.schema import load_schema_v1
from ecf_server import db, facts, precheck, rules, senders, stepup
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.dnscache import Answer, DnsCache
from ecf_server.message import parse
from ecf_server.stepper import FakeStepper

VENDOR = "Billing@Vendor-A.example"


def _setup(conn: sqlite3.Connection, clock: FakeClock, *, second: bool = False) -> None:
    now = to_ts(clock.now())
    rows = [("ap", "ap@acme.example")] + ([("info", "info@acme.example")] if second else [])
    with write_tx(conn):
        for aid, email in rows:
            conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                         " created_at) VALUES (?, ?, 'standard', 'A', ?)",
                         (aid, email, now))  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('org_domains', ?, ?, 'test')",
                     ('["acme.example"]', now))  # fmt: skip


def _nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


def _with_step_up(
    conn: sqlite3.Connection, clock: FakeClock, fn: Callable[[str | None], dict[str, Any]]
) -> dict[str, Any]:
    with pytest.raises(StepupRequiredError) as ei:
        fn(None)
    result: dict[str, Any] = fn(_nonce(conn, clock, ei.value))
    return result


def _mail(body: str, *, reply_to: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = VENDOR
    m["To"] = "ap@acme.example"
    m["Subject"] = "Invoice INV-7"
    m["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    if reply_to:
        m["Reply-To"] = reply_to
    m.set_content(body)
    return m.as_bytes()


def _analyze(conn: sqlite3.Connection, clock: FakeClock, raw: bytes) -> dict[str, Any]:
    dns = DnsCache(conn, clock, lookup=lambda _n, _t: Answer("error"))  # auth_result none
    found: dict[str, Any] = MessageAnalyzer.for_address(conn, clock, "ap", dns).analyze(
        parse(raw), raw
    )
    return found


PAYMENT = "Please pay invoice INV-7 by Friday.\n"


def test_confirm_needs_step_up_and_makes_the_sender_known(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    before = _analyze(conn, clock, _mail(PAYMENT))
    assert not before["sender_seen_before"] and before["triggers"]["fraud_weak"]
    with pytest.raises(StepupRequiredError) as ei:
        senders.confirm(conn, clock, VENDOR, "invoice", address=None, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "sender_confirm", ei.value.extra["target"])
    assert issued.prompt.startswith(
        "ecf: confirm category invoice for billing@vendor-a.example at ap@acme.example"
    )
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = senders.confirm(conn, clock, VENDOR, "invoice", address=None, nonce=issued.nonce_id)
    assert (r["confirmed_category"], r["known"], r["shared_platform"]) == ("invoice", True, False)
    after = _analyze(conn, clock, _mail(PAYMENT))
    assert after["sender_seen_before"] and after["sender_confirmed"]
    assert not after["triggers"]["fraud_weak"]  # a known sender with a payment keyword is ordinary
    assert facts.known_vendor_domains(conn, "ap") == ["vendor-a.example"]
    [data] = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'sender.confirmed'")]  # fmt: skip
    assert data["domain"] == "vendor-a.example" and "billing" not in json.dumps(data)


def test_a_nonce_fits_one_sender_one_value_and_the_record_as_it_was(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(StepupRequiredError) as ei:
        senders.confirm(conn, clock, VENDOR, "invoice", address="ap", nonce=None)
    nonce = _nonce(conn, clock, ei.value)
    with pytest.raises(StepupRequiredError):
        senders.confirm(conn, clock, VENDOR, "remittance", address="ap", nonce=nonce)
    with pytest.raises(StepupRequiredError):
        senders.confirm(conn, clock, "other@vendor-a.example", "invoice", address="ap",
                        nonce=nonce)  # fmt: skip
    senders.set_verified(conn, clock, VENDOR, False, address="ap", nonce=None)  # record changes
    with pytest.raises(StepupRequiredError):
        senders.confirm(conn, clock, VENDOR, "invoice", address="ap", nonce=nonce)


def test_bad_input_and_several_addresses(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock, second=True)
    with pytest.raises(InvalidInputError, match="category is one of"):
        senders.confirm(conn, clock, VENDOR, "bills", address="ap", nonce=None)
    with pytest.raises(InvalidInputError, match="--address"):
        senders.show(conn, VENDOR, None)
    with pytest.raises(InvalidInputError, match="not an email"):
        senders.show(conn, "vendor-a.example", "ap")
    assert senders.show(conn, VENDOR, "info")["known"] is False
    assert senders.show(conn, "x@docusign.net", "ap")["shared_platform"] is True


def test_expected_reply_to_domain(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    raw = _mail(PAYMENT, reply_to="ar@payments.vendor-a-group.example")
    assert _analyze(conn, clock, raw)["reply_to_mismatch"]
    r = _with_step_up(conn, clock, lambda n: senders.set_reply_to(
        conn, clock, VENDOR, "Payments.Vendor-A-Group.example", address=None, nonce=n))  # fmt: skip
    assert r["expected_reply_to_domain"] == "payments.vendor-a-group.example"
    assert not _analyze(conn, clock, raw)["reply_to_mismatch"]
    r = senders.set_reply_to(conn, clock, VENDOR, None, address=None, nonce=None)  # no step-up
    assert r["expected_reply_to_domain"] is None
    assert _analyze(conn, clock, raw)["reply_to_mismatch"]


def test_human_verified_turns_off_rule_1a_but_not_fraud_triggers(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    starter = rules.load_starter_rules(load_schema_v1())

    def outcome(body: str) -> tuple[set[str], str]:
        found = _analyze(conn, clock, _mail(body))
        inp = rules.RuleInput({"category": "invoice", "payment_related": True}, found,
                              frozenset(precheck.fired(found)))  # fmt: skip
        return precheck.fired(found), starter.evaluate(inp).rule_id

    assert outcome(PAYMENT) == ({"fraud_weak", "unverified_payment"}, "fraud_weak")
    r = _with_step_up(conn, clock, lambda n: senders.set_verified(
        conn, clock, VENDOR, True, address=None, nonce=n))  # fmt: skip
    assert r["verified"] is True
    # still first-time (verified isn't confirmed), so the weak signal stays; rule 1a is off
    assert outcome(PAYMENT) == ({"fraud_weak"}, "fraud_weak")
    fraud = "Our bank account has changed. Please send payment to the new account number.\n"
    assert "fraud" in outcome(fraud)[0]  # fraud triggers stay on
    with pytest.raises(StepupRequiredError):  # confirm the sender too: then only rule 1a is left
        senders.confirm(conn, clock, VENDOR, "invoice", address=None, nonce=None)
    _with_step_up(conn, clock, lambda n: senders.confirm(
        conn, clock, VENDOR, "invoice", address=None, nonce=n))  # fmt: skip
    assert outcome(PAYMENT) == (set(), "invoice")
    senders.set_verified(conn, clock, VENDOR, False, address=None, nonce=None)  # no step-up
    assert outcome(PAYMENT) == ({"unverified_payment"}, "unverified_payment_sender")


def test_cli_show_and_turning_scrutiny_back_on(running: Paths) -> None:
    conn = db.connect(running.db)
    try:
        _setup(conn, FakeClock())
    finally:
        conn.close()
    r = CliRunner().invoke(app, ["--install", "t", "sender", "show", VENDOR])
    assert r.exit_code == 0, r.output
    assert "category:        not confirmed" in r.output and "human-verified:  no" in r.output
    r = CliRunner().invoke(app, ["--install", "t", "sender", "set-verified", VENDOR, "--off"])
    assert r.exit_code == 0, r.output  # no step-up needed to add scrutiny back
    r = CliRunner().invoke(app, ["--install", "t", "sender", "set-reply-to", VENDOR])
    assert r.exit_code == 2  # usage error: a domain or --clear (the text is styled by Rich)
