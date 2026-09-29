"""Addresses, app passwords and org_domains (V1.1 step 3): service module, API routes, prompts."""

from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.errors import ConflictError, InvalidInputError, NotFoundError, ServiceUnavailableError
from ecf.ids import AddressId, StableId
from ecf.prompts import NO_TERMINAL, hidden
from ecf_server import addresses as ad
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from ecf_server.items import create_item
from ecf_server.mail import MailSource
from ecf_server.mail.fake import FakeMailSource
from ecf_server.mail.imap import MailLoginRejectedError
from ecf_server.secretstore.memory import MemorySecretStore

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
    with pytest.raises(InvalidInputError, match="first address must set it"):
        add(env, req(org_domains=None))


@pytest.mark.parametrize("domain", ["gmail.com", "Outlook.com", "purelymail.com"])
def test_public_mail_domains_are_refused(env: Env, domain: str) -> None:
    with pytest.raises(InvalidInputError, match="public mailbox domains"):
        add(env, req(org_domains=["acme.example", domain]))
    assert env[2].get("mailbox/ap") is None


def test_later_addresses_cant_change_org_domains(env: Env) -> None:
    add(env, req())
    add(env, req("billing@acme.example", org_domains=None))
    add(env, req("info@acme.example", org_domains=["ACME.example"]))  # same set: fine
    with pytest.raises(InvalidInputError, match="config apply"):
        add(env, req("x@acme.example", org_domains=["other.example"]))
    assert ad.get_org_domains(env[0]) == ["acme.example"]


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


def test_remove_refuses_while_items_are_open(env: Env) -> None:
    conn, clock, secrets, _mail = env
    add(env, req())
    create_item(
        conn, clock, stable_id=StableId("a" * 64), address_id=AddressId("ap"), content_hash="h"
    )
    with pytest.raises(ConflictError, match="open item"):
        ad.remove_address(conn, clock, secrets, "ap", actor="os_user")
    assert secrets.get("mailbox/ap") == GOOD


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
    r = call(st, "POST", "/v1/addresses/ap", {"app_password": "wrong"})
    assert r.status_code == 503 and r.json()["code"] == "mail_unavailable"
    assert "wrong" not in r.text
    r = call(st, "POST", "/v1/addresses/ap", {"stage": "live"})
    assert r.status_code == 400
    r = call(st, "DELETE", "/v1/addresses/ap")
    assert r.status_code == 200 and secrets.get("mailbox/ap") is None


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
