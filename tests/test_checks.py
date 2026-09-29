"""Running a check, `POST /v1/checks` and per-address status (V1.1 step 11a)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.cli import _check_line  # pyright: ignore[reportPrivateUsage]
from ecf.errors import NotFoundError
from ecf_server import checks, db, leases
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from ecf_server.mail import MailSource
from ecf_server.mail.fake import FakeMailSource
from ecf_server.mail.imap import MailLoginRejectedError
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_precheck import BEC
from tests.test_triggers import mail

TOKEN = "cli-token"


class Box:
    """One fake mailbox shared by every connection the service opens."""

    def __init__(self) -> None:
        self.fake = FakeMailSource()
        self.reject = False
        self.logins = 0

    def factory(self, _host: str, _user: str, password: Callable[[], str]) -> MailSource:
        password()  # the real adapter reads it at connect
        self.logins += 1
        if self.reject:
            raise MailLoginRejectedError("imap.acme.example rejected the login")
        return self.fake


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> tuple[sqlite3.Connection, Path]:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')"
    )
    conn.execute(
        "INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'imap.acme.example', 'now')"
    )
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('org_domains', '[\"acme.example\"]', 'now', 'test')"
    )
    return conn, db_path


def secrets(pw: str | None = "pw") -> MemorySecretStore:
    s = MemorySecretStore()
    if pw:
        s.set("mailbox/ap", pw)
    return s


def run(
    env: tuple[sqlite3.Connection, Path],
    clock: FakeClock,
    box: Box,
    store: MemorySecretStore | None = None,
) -> checks.CheckReport:
    conn, path = env
    return checks.run_check(
        conn,
        clock,
        address_id="ap",
        install="default",
        secrets=store or secrets(),
        factory=box.factory,
        connect=lambda: db.connect(path),
    )


# ---- one check ------------------------------------------------------------------------------


def test_first_run_then_a_fraud_mail(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    conn, _ = env
    box = Box()
    assert run(env, clock, box).status == "first_run"
    box.fake.deliver(BEC)
    r = run(env, clock, box)
    assert (r.status, r.created, r.escalations) == ("ok", 1, 1)
    state = checks.states(conn)[0]
    assert state["last_status"] == "ok" and state["backlog"] == 0 and state["last_error"] is None
    events = [x["event"] for x in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert events.count("check.completed") == 2
    assert conn.execute("SELECT count(*) FROM leases").fetchone()[0] == 0  # released


def test_busy_lease_is_skipped(env: tuple[sqlite3.Connection, Path], clock: FakeClock) -> None:
    conn, _ = env
    assert leases.acquire(conn, clock, "ap", "someone-else") is not None
    box = Box()
    assert run(env, clock, box).status == "busy" and box.logins == 0
    assert checks.states(conn)[0]["last_status"] is None
    assert conn.execute("SELECT event FROM audit").fetchone()["event"] == "check.lease_skipped"


def test_a_second_check_in_this_service_is_busy(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """Found in the V1.1 shadow run (2026-09-29): a scheduled check and `ecf check` ran together
    in one service, and the second took over the first's lease."""
    conn, _ = env
    box = Box()
    with leases.local_lock("ap"):  # a check of this address is running in this process
        assert run(env, clock, box).status == "busy" and box.logins == 0
    first = leases.acquire(conn, clock, "ap", checks.holder())  # its lease, if it got that far
    assert first is not None
    assert run(env, clock, box).status == "busy" and box.logins == 0
    assert leases.held(conn, clock, first)  # untouched
    leases.release(conn, first)
    assert run(env, clock, box).status == "first_run"


def test_every_check_has_its_own_holder_name() -> None:
    assert checks.holder() != checks.holder()


def test_no_password_and_rejected_login(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    conn, _ = env
    r = run(env, clock, Box(), secrets(None))
    assert r.status == "error" and "no app password stored" in (r.error or "")
    box = Box()
    box.reject = True
    r = run(env, clock, box)
    assert r.status == "login_rejected"
    state = checks.states(conn)[0]
    assert state["last_status"] == "login_rejected" and "rejected" in state["last_error"]
    assert conn.execute("SELECT count(*) FROM leases").fetchone()[0] == 0


def test_unknown_address(env: tuple[sqlite3.Connection, Path], clock: FakeClock) -> None:
    conn, path = env
    with pytest.raises(NotFoundError):
        checks.run_check(
            conn,
            clock,
            address_id="nope",
            install="default",
            secrets=secrets(),
            factory=Box().factory,
            connect=lambda: db.connect(path),
        )


# ---- the API --------------------------------------------------------------------------------


def state_for(path: Path, box: Box, clock: FakeClock) -> ServiceState:
    return ServiceState(
        install="default",
        token=TOKEN,
        started_at="t",
        clock=clock,
        db_path=path,
        secrets=secrets(),
        mail_factory=box.factory,
    )


def post(st: ServiceState, path: str, body: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    async def go() -> tuple[int, list[dict[str, Any]]]:
        transport = httpx.ASGITransport(app=create_app(st))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            r = await c.post(path, json=body, headers={"Authorization": f"Bearer {TOKEN}"})
            if r.status_code != 200:
                return r.status_code, [r.json()]
            return 200, [json.loads(x) for x in r.text.splitlines() if x.strip()]

    return anyio.run(go)


def test_checks_route_streams_and_until_empty_drains_the_backlog(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    _, path = env
    box = Box()
    st = state_for(path, box, clock)
    code, lines = post(st, "/v1/checks", {})
    assert code == 200 and lines[0]["status"] == "first_run" and lines[-1]["done"]
    for i in range(35):
        box.fake.deliver(mail(f"note {i}"))
    code, lines = post(st, "/v1/checks", {"address_id": "ap", "until_empty": True})
    reports = [x for x in lines if not x.get("done")]
    assert [r["created"] for r in reports] == [30, 5] and reports[-1]["remaining"] == 0
    code, lines = post(st, "/v1/checks", {"address_id": "nope"})
    assert code == 404


def test_status_lists_addresses(env: tuple[sqlite3.Connection, Path], clock: FakeClock) -> None:
    _, path = env
    box = Box()
    st = state_for(path, box, clock)
    post(st, "/v1/checks", {})

    async def go() -> dict[str, Any]:
        transport = httpx.ASGITransport(app=create_app(st))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            r = await c.get("/v1/status", headers={"Authorization": f"Bearer {TOKEN}"})
            data: dict[str, Any] = r.json()
            return data

    (a,) = anyio.run(go)["addresses"]
    assert a["address_id"] == "ap" and a["last_status"] == "first_run" and a["stage"] == "shadow"


def test_cli_lines() -> None:
    base: dict[str, Any] = {
        "address_id": "ap",
        "status": "ok",
        "error": None,
        "created": 3,
        "escalations": 1,
        "digest": 0,
        "duplicates": 0,
        "quarantined": 0,
        "large_done": 0,
        "deferred": 0,
        "remaining": 2,
    }
    assert _check_line(base) == "ap               3 new, 1 to escalate, 2 still waiting"
    assert "first check" in _check_line(base | {"status": "first_run"})
    assert "login rejected: nope" in _check_line(
        base | {"status": "login_rejected", "error": "nope"}
    )


@pytest.mark.imap
def test_a_check_against_dovecot(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock, dovecot_server: Any
) -> None:
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf_server.mail.imap import ImapSource  # noqa: PLC0415
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server
    user = f"ecf-t-{uuid.uuid4().hex[:12]}"
    conn, path = env
    conn.execute("UPDATE addresses SET email = ? WHERE address_id = 'ap'", (user,))

    def factory(_host: str, login: str, password: Callable[[], str]) -> MailSource:
        return ImapSource(dv.host, login, password, port=dv.port, ssl_context=dv.context())

    def check() -> checks.CheckReport:
        return checks.run_check(
            conn,
            clock,
            address_id="ap",
            install="default",
            secrets=secrets(dovecot.PASSWORD),
            factory=factory,
            connect=lambda: db.connect(path),
        )

    admin = dv.admin(user)
    try:
        assert check().status == "first_run"
        dovecot.append(admin, BEC, datetime.now(UTC))
        r = check()
        assert (r.status, r.created, r.escalations) == ("ok", 1, 1)
        dovecot.expunge(admin, 1)  # the person deletes it in their mail client
        assert check().resolved_by_mailbox == 1
        assert conn.execute("SELECT status FROM items").fetchone()[0] == "resolved_by_mailbox"
        wrong = checks.run_check(
            conn,
            clock,
            address_id="ap",
            install="default",
            secrets=secrets("wrong"),
            factory=factory,
            connect=lambda: db.connect(path),
        )
        assert wrong.status == "login_rejected"
    finally:
        admin.logout()
