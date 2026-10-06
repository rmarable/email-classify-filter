"""Addresses, app passwords and org_domains (V1.1 step 3): service module, API routes, prompts."""

from __future__ import annotations

import dataclasses
import io
import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
    StepupRequiredError,
)
from ecf.ids import AddressId, StableId
from ecf.prompts import NO_TERMINAL, hidden
from ecf.status import Status
from ecf_server import addresses as ad
from ecf_server import claude_queue, items, modelq, send_limits, slack_admin, slack_routes, stepup
from ecf_server.api import ServiceState, create_app
from ecf_server.chat import FakeChat
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.items import create_item
from ecf_server.mail import MailSource
from ecf_server.mail.fake import FakeMailSource
from ecf_server.mail.imap import MailLoginRejectedError
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

GOOD = "right-password"


class Mail:
    """A MailFactory that accepts GOOD and records every login attempt."""

    def __init__(self) -> None:
        self.logins: list[tuple[str, str]] = []

    def __call__(self, host: str, user: str, password: Callable[[], str]) -> MailSource:
        pw = password()
        self.logins.append((host, user))
        if pw != GOOD:
            raise MailLoginRejectedError(f"{host} rejected the login for {user}")
        return FakeMailSource()


def req(email: str = "ap@acme.example", **kw: Any) -> ad.AddRequest:
    base: dict[str, Any] = {
        "email": email,
        "imap_host": "imap.acme.example",
        "sensitivity": "high",
        "preset": "A",
        "app_password": GOOD,
        "org_domains": ["acme.example"],
    }
    return ad.AddRequest(**(base | kw))


@pytest.fixture
def env(
    conn: sqlite3.Connection, clock: FakeClock
) -> tuple[sqlite3.Connection, FakeClock, MemorySecretStore, Mail]:
    return conn, clock, MemorySecretStore(), Mail()


Env = tuple[sqlite3.Connection, FakeClock, MemorySecretStore, Mail]


def add(env: Env, r: ad.AddRequest) -> dict[str, Any]:
    conn, clock, secrets, mail = env
    return ad.add_address(conn, clock, secrets, mail, r, actor="os_user")


# ---- adding ---------------------------------------------------------------------------------


def test_first_address_sets_org_domains_and_stores_the_secret(env: Env) -> None:
    conn, _clock, secrets, mail = env
    a = add(env, req())
    assert a["address_id"] == "ap" and a["stage"] == "shadow" and a["outbound"] is False
    assert a["imap_host"] == "imap.acme.example" and a["sensitivity"] == "high"
    assert secrets.get("mailbox/ap") == GOOD
    assert ad.get_org_domains(conn) == ["acme.example"]
    assert mail.logins == [("imap.acme.example", "ap@acme.example")]
    events = [r["event"] for r in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert events == ["config.applied", "address.added"]
    assert GOOD not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM audit")])


def test_first_address_must_set_org_domains(env: Env) -> None:
    with pytest.raises(InvalidInputError, match="must set it"):
        add(env, req(org_domains=None))


@pytest.mark.parametrize("domain", ["gmail.com", "Outlook.com", "purelymail.com"])
def test_public_mail_domains_are_refused(env: Env, domain: str) -> None:
    with pytest.raises(InvalidInputError, match=r"public mailbox domains.* in org_addresses"):
        add(env, req(org_domains=["acme.example", domain]))  # pointed to org_addresses (V1.6)
    assert env[2].get("mailbox/ap") is None


def test_later_addresses_cant_change_org_domains(env: Env) -> None:
    add(env, req())
    add(env, req("billing@acme.example", org_domains=None))
    add(env, req("info@acme.example", org_domains=["ACME.example"]))  # same set: fine
    with pytest.raises(InvalidInputError, match="config apply"):
        add(env, req("x@acme.example", org_domains=["other.example"]))
    assert ad.get_org_domains(env[0]) == ["acme.example"]


def test_a_gmail_address_needs_no_org_domains_or_imap_host(env: Env) -> None:
    """V1.6 (OD-441): a public-provider address sets no org domains; Gmail's servers are known
    and its daily send limit starts at 100 (§14.2)."""
    conn, _clock, _secrets, mail = env
    a = add(env, req("Pat.Lee@gmail.com", imap_host="", org_domains=None))
    assert (a["imap_host"], a["smtp_host"], a["smtp_port"]) == ("imap.gmail.com", "smtp.gmail.com",
                                                               465)  # fmt: skip
    assert a["max_sends_per_day"] == ad.GMAIL_SENDS_PER_DAY == 100
    assert send_limits.limits(conn, a["address_id"]) == {"max_sends_per_hour": 25,
                                                          "max_sends_per_day": 100}  # fmt: skip
    assert ad.get_org_domains(conn) == []
    assert mail.logins == [("imap.gmail.com", "Pat.Lee@gmail.com")]
    # the first address at your own domain must still set them
    with pytest.raises(InvalidInputError, match="must set it"):
        add(env, req(org_domains=None))
    assert add(env, req())["max_sends_per_day"] is None  # the usual default
    assert ad.get_org_domains(conn) == ["acme.example"]


def test_address_add_help_says_what_a_gmail_address_needs() -> None:
    """V1.6 step 8: no --imap-host, an app password (2-Step Verification), the send default."""
    r = CliRunner().invoke(app, ["address", "add", "--help"])
    plain = " ".join(re.sub(r"\x1b\[[0-9;]*m|[│╭╮╰╯─]", " ", r.output).split())  # Rich boxes
    assert r.exit_code == 0
    assert "(gmail.com or googlemail.com) needs no --imap-host and a Google app password" in plain
    assert "2-Step Verification" in plain
    assert f"at most {ad.GMAIL_SENDS_PER_DAY} emails a day" in plain


def test_an_unknown_providers_imap_server_is_needed(env: Env) -> None:
    with pytest.raises(InvalidInputError, match="--imap-host"):
        add(env, req(imap_host=""))


def test_a_revived_gmail_address_keeps_the_send_limit_it_had(env: Env) -> None:
    conn = env[0]
    a = add(env, req("pat@gmail.com", imap_host="", org_domains=None))
    with write_tx(conn):
        conn.execute("UPDATE addresses SET overrides = '{\"max_sends_per_day\": 40}'")
    ad.remove_address(conn, env[1], env[2], a["address_id"], actor="os_user")
    assert add(env, req("pat@gmail.com", imap_host="", org_domains=None))["max_sends_per_day"] == 40


def test_rejected_login_stores_nothing(env: Env) -> None:
    conn, _clock, secrets, _mail = env
    with pytest.raises(MailLoginRejectedError):
        add(env, req(app_password="wrong"))
    assert secrets.get("mailbox/ap") is None
    assert ad.list_addresses(conn) == [] and ad.get_org_domains(conn) == []


def test_duplicates_are_refused_before_logging_in(env: Env) -> None:
    add(env, req())
    mail = env[3]
    for r in (
        req(),
        req("AP@acme.example", address_id="other"),
        req("b@acme.example", address_id="ap"),
    ):
        with pytest.raises(ConflictError):
            add(env, r)
    assert len(mail.logins) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-address"),
        ("sensitivity", "medium"),
        ("preset", "D"),
        ("imap_host", "bad host"),
        ("address_id", "Has Caps"),
        ("app_password", "two\nlines"),
        ("app_password", ""),
    ],
)
def test_invalid_input(env: Env, field: str, value: str) -> None:
    with pytest.raises(InvalidInputError):
        add(env, req(**{field: value}))
    assert env[3].logins == []


def test_domains_are_idna_normalized() -> None:
    assert ad.normalize_domain("Bücher.Example.") == "xn--bcher-kva.example"
    assert ad.default_address_id("Accounts.Payable@acme.example") == "accounts-payable"


# ---- rotating and removing ------------------------------------------------------------------


def test_new_app_password_is_checked_before_it_replaces_the_old_one(env: Env) -> None:
    conn, clock, secrets, mail = env
    add(env, req())
    with pytest.raises(MailLoginRejectedError):
        ad.set_app_password(conn, clock, secrets, mail, "ap@acme.example", "wrong", actor="os_user")
    assert secrets.get("mailbox/ap") == GOOD


def test_remove_then_add_again_revives_the_row(env: Env) -> None:
    conn, clock, secrets, _mail = env
    add(env, req())
    out = ad.remove_address(conn, clock, secrets, "ap", actor="os_user")
    assert out["residue"] and secrets.get("mailbox/ap") is None
    assert ad.list_addresses(conn) == []
    with pytest.raises(NotFoundError):
        ad.get_address(conn, "ap")
    again = add(env, req(sensitivity="standard", org_domains=None))
    assert again["sensitivity"] == "standard" and again["stage"] == "shadow"
    assert conn.execute("SELECT count(*) FROM addresses").fetchone()[0] == 1


@pytest.mark.parametrize(("old", "new"), [("C", "A"), ("B", "A"), ("A", "C")])
def test_a_preset_changes_only_with_nothing_left_waiting(env: Env, old: str, new: str) -> None:
    """V1.4 step 1: removal resolves the queue under the old preset, so nothing is stranded at
    `awaiting_claude` (no edge back to the local model) or at `new` for the wrong model."""
    conn, clock, secrets, _mail = env
    add(env, req(preset=old))
    sid = StableId("a" * 64)
    create_item(conn, clock, stable_id=sid, address_id=AddressId("ap"), content_hash="h")
    if old == "C":
        items.transition(conn, clock, sid, Status.AWAITING_CLAUDE, TransitionContext(),
                         actor="service")  # fmt: skip
    ad.remove_address(conn, clock, secrets, "ap", actor="os_user")
    again = add(env, req(preset=new, org_domains=None))
    assert again["preset"] == new
    assert conn.execute("SELECT status FROM items").fetchone()[0] == Status.RESOLVED_MANUAL.value
    assert modelq.waiting(conn) == {} and claude_queue.waiting(conn) == {}


def test_adding_again_is_refused_while_items_are_open(env: Env) -> None:
    conn, clock, _secrets, mail = env
    add(env, req(preset="C"))
    create_item(conn, clock, stable_id=StableId("a" * 64), address_id=AddressId("ap"),
                content_hash="h")  # fmt: skip
    with write_tx(conn):  # removed without resolving: never happens; the guard holds anyway
        conn.execute("UPDATE addresses SET removed_at = 'now'")
    logins = len(mail.logins)
    with pytest.raises(ConflictError, match="open items"):
        add(env, req(preset="A", org_domains=None))
    assert len(mail.logins) == logins  # refused before logging in
    assert conn.execute("SELECT preset FROM addresses").fetchone()[0] == "C"


def test_remove_resolves_ordinary_open_items_without_step_up(env: Env) -> None:
    conn, clock, secrets, _mail = env
    add(env, req())
    create_item(
        conn, clock, stable_id=StableId("a" * 64), address_id=AddressId("ap"), content_hash="h"
    )
    a = ad.remove_address(conn, clock, secrets, "ap", actor="os_user")
    assert a["resolved"] == 1 and secrets.get("mailbox/ap") is None
    row = conn.execute("SELECT status FROM items").fetchone()
    assert row["status"] == Status.RESOLVED_MANUAL.value


def test_remove_with_a_fraud_item_needs_step_up_for_that_exact_set(env: Env) -> None:
    conn, clock, secrets, _mail = env
    add(env, req())
    fraud = json.dumps({"triggers": {"fraud": ["bank change"]}})
    create_item(conn, clock, stable_id=StableId("a" * 64), address_id=AddressId("ap"),
                content_hash="h", facts=fraud)  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        ad.remove_address(conn, clock, secrets, "ap", actor="os_user")
    assert secrets.get("mailbox/ap") == GOOD  # nothing changed
    issued = stepup.issue(conn, clock, FakeStepper(), "address_remove", ei.value.extra["target"])
    assert "1 open item(s), 1 of them payment or fraud" in issued.prompt
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    create_item(conn, clock, stable_id=StableId("b" * 64), address_id=AddressId("ap"),
                content_hash="h2")  # arrives after the step-up  # fmt: skip
    with pytest.raises(StepupRequiredError):
        ad.remove_address(conn, clock, secrets, "ap", actor="os_user", nonce=issued.nonce_id)
    issued = stepup.issue(conn, clock, FakeStepper(), "address_remove", ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    a = ad.remove_address(conn, clock, secrets, "ap", actor="os_user", nonce=issued.nonce_id)
    assert a["resolved"] == 2


# ---- the API --------------------------------------------------------------------------------

TOKEN = "cli-token"


def make_state(db_path: Path, secrets: MemorySecretStore | None) -> ServiceState:
    return ServiceState(
        install="t",
        token=TOKEN,
        started_at="2026-10-01T12:00:00.000000Z",
        clock=FakeClock(),
        db_path=db_path,
        secrets=secrets,
        mail_factory=Mail(),
    )


def call(
    state: ServiceState, method: str, path: str, body: Any = None, token: str = TOKEN
) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(
                method, path, json=body, headers={"Authorization": f"Bearer {token}"}
            )

    return anyio.run(go)


def test_api_add_list_rotate_remove(conn: sqlite3.Connection, db_path: Path) -> None:
    secrets = MemorySecretStore()
    st = make_state(db_path, secrets)
    body = {
        "email": "ap@acme.example",
        "imap_host": "imap.acme.example",
        "sensitivity": "high",
        "preset": "A",
        "app_password": GOOD,
        "org_domains": ["acme.example"],
    }
    r = call(st, "POST", "/v1/addresses", body)
    assert r.status_code == 201, r.text
    assert GOOD not in r.text
    listed = call(st, "GET", "/v1/addresses").json()
    assert listed["org_domains"] == ["acme.example"] and len(listed["addresses"]) == 1
    assert listed["org_addresses"] == 0 and "gmail.com" in listed["public_domains"]
    assert listed["imap_defaults"] == {"gmail.com": "imap.gmail.com",
                                       "googlemail.com": "imap.gmail.com"}  # fmt: skip
    r = call(st, "POST", "/v1/addresses/ap", {"app_password": "wrong"})
    assert r.status_code == 503 and r.json()["code"] == "mail_unavailable"
    assert "wrong" not in r.text
    r = call(st, "POST", "/v1/addresses/ap", {"stage": "live"})
    assert r.status_code == 400
    r = call(st, "DELETE", "/v1/addresses/ap")
    assert r.status_code == 200 and secrets.get("mailbox/ap") is None
    assert r.json()["slack_channel_archived"] is None  # no Slack: nothing recorded to archive


def test_api_names_the_slack_channel_and_archives_it_on_removal(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    st = make_state(db_path, MemorySecretStore())
    now = "2026-10-01T12:00:00.000000Z"
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"),
                     ("slack_member_id", "U0ME1")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")
    body = {"email": "ap@acme.example", "imap_host": "imap.acme.example",
            "sensitivity": "standard", "preset": "A", "app_password": GOOD,
            "org_domains": ["acme.example"]}  # fmt: skip
    r = call(st, "POST", "/v1/addresses", body)
    assert r.status_code == 201 and r.json()["slack_channel"] == "ecf-t-ap"
    slack_routes.ensure(conn, FakeClock(), FakeChat(), "t")  # what the Slack thread does
    r = call(st, "DELETE", "/v1/addresses/ap")
    assert r.status_code == 200 and r.json()["slack_channel_archived"] == "ecf-t-ap"
    job = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out'").fetchone()
    assert json.loads(job["payload"])["op"] == "archive"


def test_api_rejects_bad_bodies_and_non_cli_callers(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    st = make_state(db_path, MemorySecretStore())
    assert call(st, "POST", "/v1/addresses", ["not", "an", "object"]).status_code == 400
    assert call(st, "POST", "/v1/addresses", {"email": 5}).status_code == 400
    session = call(st, "POST", "/v1/sessions").json()["profile_token"]
    r = call(st, "GET", "/v1/addresses", token=session)
    assert r.status_code == 403 and r.json()["code"] == "forbidden_profile"


def test_api_without_a_secret_store_says_why(conn: sqlite3.Connection, db_path: Path) -> None:
    st = make_state(db_path, None)
    st.secret_store = {"backend": None, "detail": "no usable secret store: test"}
    r = call(
        st,
        "POST",
        "/v1/addresses",
        {
            "email": "ap@acme.example",
            "imap_host": "h.example",
            "sensitivity": "high",
            "preset": "A",
            "app_password": GOOD,
            "org_domains": ["acme.example"],
        },
    )
    assert r.status_code == 503 and "test" in r.json()["detail"]
    assert ServiceUnavailableError.code.value == r.json()["code"]


# ---- hidden prompts -------------------------------------------------------------------------


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_hidden_prompt_refuses_without_a_terminal() -> None:
    with pytest.raises(InvalidInputError) as info:
        hidden("pw: ", stdin=io.StringIO(), ask=lambda _p: "secret")
    assert info.value.detail == NO_TERMINAL


def test_hidden_prompt_with_a_terminal() -> None:
    answers = iter(["s3cret", "s3cret"])
    assert hidden("pw: ", confirm=True, stdin=Tty(), ask=lambda _p: next(answers)) == "s3cret"
    answers = iter(["a", "b"])
    with pytest.raises(InvalidInputError, match="don't match"):
        hidden("pw: ", confirm=True, stdin=Tty(), ask=lambda _p: next(answers))
    with pytest.raises(InvalidInputError, match="nothing entered"):
        hidden("pw: ", stdin=Tty(), ask=lambda _p: "")


# ---- against Dovecot ------------------------------------------------------------------------


@pytest.mark.imap
def test_add_address_logs_in_over_imap(
    conn: sqlite3.Connection, clock: FakeClock, dovecot_server: Any
) -> None:
    from ecf_server.mail.imap import ImapSource  # noqa: PLC0415
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server

    def factory(_host: str, user: str, password: Callable[[], str]) -> MailSource:
        # the stored host is nominal here; every login goes to the test container
        return ImapSource(dv.host, user, password, port=dv.port, ssl_context=dv.context())

    secrets = MemorySecretStore()
    r = req(
        "ap@localhost.example",
        imap_host="imap.localhost.example",
        app_password=dovecot.PASSWORD,
        org_domains=["localhost.example"],
    )
    wrong = dataclasses.replace(r, app_password="wrong")
    with pytest.raises(MailLoginRejectedError):
        ad.add_address(conn, clock, secrets, factory, wrong, actor="os_user")
    a = ad.add_address(conn, clock, secrets, factory, r, actor="os_user")
    assert a["address_id"] == "ap" and secrets.get("mailbox/ap") == dovecot.PASSWORD


def test_cli_refuses_before_asking_anything_without_a_terminal() -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    result = CliRunner().invoke(app, ["address", "add", "ap@acme.example", "--imap-host", "h.x"])
    assert isinstance(result.exception, InvalidInputError)
    assert result.exception.detail == NO_TERMINAL
    assert "Sensitivity" not in result.output
