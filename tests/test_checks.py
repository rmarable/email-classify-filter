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
        " VALUES ('ap', 'ap@acme.example', 'high', 'C', 'now')"
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


# One mail check of one address: the first run, a busy lease or second check is skipped, and
# a missing password, rejected login or unknown address is reported.


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


def test_an_email_with_no_space_near_the_cut_is_stored(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """Its excerpts fit the table (OD-552): the check doesn't fail on it, and it is pre-checked."""
    from tests.test_message import build  # noqa: PLC0415

    conn, _ = env
    box = Box()
    run(env, clock, box)
    box.fake.deliver(build(text="漢" * 5000))
    r = run(env, clock, box)
    assert (r.status, r.created) == ("ok", 1)
    row = conn.execute(
        "SELECT length(classifier_text), length(actor_text) FROM excerpts"
    ).fetchone()
    assert tuple(row) == (1500, 4000)
    assert conn.execute("SELECT count(*) FROM processing").fetchone()[0] == 0
    assert conn.execute("SELECT prechecked FROM items").fetchone()[0] == 1


def test_a_quarantined_email_is_escalated(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two crashes set it aside unread; it still reaches you (SPEC 5.1, OD-552)."""
    from ecf_server.analysis import MessageAnalyzer  # noqa: PLC0415

    conn, _ = env
    box = Box()
    run(env, clock, box)
    box.fake.deliver(BEC)

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("crafted message crashes the parser")

    monkeypatch.setattr(MessageAnalyzer, "analyze", boom)
    for _ in range(2):
        assert run(env, clock, box).status == "internal_error"
    r = run(env, clock, box)
    row = conn.execute("SELECT facts, prechecked FROM items").fetchone()
    assert json.loads(row["facts"])["quarantined"] and row["prechecked"] == 1
    assert r.escalations == 1
    assert conn.execute("SELECT count(*) FROM escalations").fetchone()[0] == 1


def test_a_quarantined_email_outside_shadow_is_escalated_not_read_again(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No label or flag: that would fetch and parse it again in the check (R1, OD-552)."""
    from ecf_server import actions  # noqa: PLC0415
    from ecf_server.analysis import MessageAnalyzer  # noqa: PLC0415

    conn, _ = env
    conn.execute("UPDATE addresses SET stage = 'assist'")
    box = Box()
    run(env, clock, box)
    box.fake.deliver(BEC)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("crafted message crashes the parser")

    monkeypatch.setattr(MessageAnalyzer, "analyze", boom)
    for _ in range(2):
        assert run(env, clock, box).status == "internal_error"
    monkeypatch.setattr(actions, "execute", boom)  # never called for a quarantined item
    r = run(env, clock, box)
    assert r.status == "ok" and r.escalations == 1 and r.created == 0 and r.quarantined == 1
    facts = json.loads(conn.execute("SELECT facts FROM items").fetchone()[0])
    assert facts["precheck"]["skipped"] == "quarantined: not read again"


def test_an_email_quarantined_before_the_upgrade_is_escalated(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier version quarantined it and never pre-checked it: the next check does (OD-552).
    (The fake mailbox doesn't hold the marker's UID, so closing gone mail is set aside here.)"""
    from ecf_server import fetch  # noqa: PLC0415

    conn, _ = env
    box = Box()
    run(env, clock, box)
    lease = leases.acquire(conn, clock, "ap", "w1")
    assert lease is not None
    fetch._quarantine(conn, clock, lease, 1, 99)  # pyright: ignore[reportPrivateUsage]
    leases.release(conn, lease)
    assert conn.execute("SELECT prechecked FROM items").fetchone()[0] == 0

    def keep(*_a: Any, **_k: Any) -> int:
        return 0

    monkeypatch.setattr(checks, "close_gone", keep)
    r = run(env, clock, box)
    assert r.escalations == 1
    assert conn.execute("SELECT prechecked FROM items").fetchone()[0] == 1


def test_nothing_reads_a_quarantined_message_again(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """Every action path verifies first; for a quarantined item that refuses before any fetch
    (S1, OD-552): the pre-check, approved actions and reprocess alike."""
    from ecf_server import actions, fetch  # noqa: PLC0415

    conn, _ = env
    box = Box()
    run(env, clock, box)
    lease = leases.acquire(conn, clock, "ap", "w1")
    assert lease is not None
    sid = fetch._quarantine(conn, clock, lease, 1, 99)  # pyright: ignore[reportPrivateUsage]
    leases.release(conn, lease)
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()

    class Untouchable(FakeMailSource):
        def inbox(self) -> Any:
            raise AssertionError("the mailbox was read for a quarantined item")

    with pytest.raises(actions.MessageChangedError, match="quarantined"):
        actions.verify(Untouchable(), item, 1024)


def test_a_quarantined_email_from_a_scan_is_old_mail(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """Marked like the scan's other items, so backfill handles it, not the new-mail pre-check."""
    from ecf_server import fetch  # noqa: PLC0415

    conn, _ = env
    box = Box()
    run(env, clock, box)
    lease = leases.acquire(conn, clock, "ap", "w1")
    assert lease is not None
    sid = fetch._quarantine(conn, clock, lease, 1, 98, scan="run1")  # pyright: ignore[reportPrivateUsage]
    leases.release(conn, lease)
    facts = json.loads(conn.execute("SELECT facts FROM items").fetchone()[0])
    assert facts == {"quarantined": True, "content_unscanned": True, "backfill": 1,
                     "scan_run": "run1"}  # fmt: skip
    assert sid not in checks.not_prechecked(conn, "ap", [])


def test_an_email_a_failed_check_left_unchecked_is_checked_next_time(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Created, then the check failed before its pre-check: the next check recovers it (OD-552)."""
    conn, _ = env
    box = Box()
    run(env, clock, box)
    box.fake.deliver(BEC)
    real = checks.close_gone
    calls = {"n": 0}

    def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a failure after the fetch")
        return real(*a, **k)

    monkeypatch.setattr(checks, "close_gone", flaky)
    assert run(env, clock, box).status == "internal_error"
    assert conn.execute("SELECT prechecked FROM items").fetchone()[0] == 0
    r = run(env, clock, box)
    assert conn.execute("SELECT prechecked FROM items").fetchone()[0] == 1
    assert r.escalations == 1


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
    # its own status, so it doesn't pass for a mail outage (V1.1 review)
    assert r.status == "secret_unavailable" and "no app password stored" in (r.error or "")
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


# The checks API streams one check per address; status lists addresses; the CLI prints them;
# and a check runs against a real IMAP server (Dovecot).


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


def test_checks_route_streams_one_check_per_address(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """`--until-empty` and the model round went in v3.0.0 (ADR 0031, P17): one check each; an
    older client's `until_empty` is ignored."""
    _, path = env
    box = Box()
    st = state_for(path, box, clock)
    code, lines = post(st, "/v1/checks", {})
    assert code == 200 and lines[0]["status"] == "first_run" and lines[-1]["done"]
    for i in range(35):
        box.fake.deliver(mail(f"note {i}"))
    code, lines = post(st, "/v1/checks", {"address_id": "ap", "until_empty": True})
    reports = [x for x in lines if not x.get("done")]
    assert [r["created"] for r in reports] == [30] and reports[-1]["remaining"] == 5
    assert not any("model" in x for x in lines)
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


def test_check_completed_carries_imap_and_auth_timings(
    env: tuple[sqlite3.Connection, Path], clock: FakeClock
) -> None:
    """Capture points c and d (OD-507, plan §5): the source's own phase times and the isolation
    children's counts land on `check.completed`; no message content."""
    conn, _ = env
    box = Box()
    run(env, clock, box)
    box.fake.deliver(BEC)
    r = run(env, clock, box)
    t = r.timings
    assert set(t["imap_s"]) == {"connect", "select", "search", "fetch_new", "fetch_backfill",
                                "store"}  # fmt: skip
    assert box.fake.timings()["fetch"] > 0  # measured; the report keeps milliseconds
    assert t["imap_s"]["fetch_new"] >= 0 and t["imap_s"]["fetch_backfill"] == 0
    assert t["auth"]["messages"] == 1 and t["auth"]["child_s"] >= t["auth"]["auth_s"] >= 0
    row = conn.execute("SELECT data FROM audit WHERE event = 'check.completed'"
                       " ORDER BY id DESC LIMIT 1").fetchone()  # fmt: skip
    data = json.loads(row["data"])
    assert data["timings"] == t
    assert "Subject" not in row["data"] and "@" not in json.dumps(data["timings"])
