"""Step-up nonces (V1.2 step 2; SPEC §9.6). The Stepper is a fake: no test shows a real dialog."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.errors import (
    InvalidInputError,
    RateLimitedError,
    StepupFailedError,
    StepupRequiredError,
)
from ecf.stepup import step_up, with_step_up
from ecf_server import stepup
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from ecf_server.stepper import FakeStepper, PamStepper


@stepup.purpose("test.setting")
def _setting(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """A purpose whose hash depends on the database, like a real grant or setting."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (target["key"],)).fetchone()
    current = row["value"] if row else None
    return stepup.Bound(
        stepup.digest("setting", target["key"], target["value"], current),
        f"ecf: set {target['key']} to {target['value']}",
    )


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 'now', 't')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


TARGET = {"key": "slack_member_id", "value": "U999"}


def _verified(conn: sqlite3.Connection, clock: FakeClock, s: FakeStepper) -> str:
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    assert stepup.verify(conn, clock, s, n.nonce_id) == "verified"
    return n.nonce_id


def test_issue_verify_consume_once(conn: sqlite3.Connection, clock: FakeClock) -> None:
    s = FakeStepper()
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    assert len(n.code) == 4 and n.code in n.prompt and "slack_member_id to U999" in n.prompt
    assert stepup.verify(conn, clock, s, n.nonce_id) == "verified"
    assert s.reasons == [n.prompt]  # the dialog shows what the service computed, with the code
    stepup.consume(conn, clock, "test.setting", TARGET, n.nonce_id)
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "test.setting", TARGET, n.nonce_id)  # single use


def test_bound_to_the_exact_change(conn: sqlite3.Connection, clock: FakeClock) -> None:
    nonce = _verified(conn, clock, FakeStepper())
    with pytest.raises(StepupRequiredError) as info:
        stepup.consume(conn, clock, "test.setting", {**TARGET, "value": "U666"}, nonce)
    assert info.value.extra["purpose"] == "test.setting"
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "test", TARGET, nonce)  # another purpose
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "test.setting", TARGET, None)


def test_a_verified_nonce_lapses_after_two_minutes(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    nonce = _verified(conn, clock, FakeStepper())
    clock.advance(121)
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "test.setting", TARGET, nonce)


def test_an_expired_nonce_cant_be_verified(conn: sqlite3.Connection, clock: FakeClock) -> None:
    s = FakeStepper()
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    clock.advance(11 * 60)
    with pytest.raises(StepupFailedError, match="expired"):
        stepup.verify(conn, clock, s, n.nonce_id)


def test_a_changed_target_is_refused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """The dialog must show what will happen: if the record changed since, ask again."""
    s = FakeStepper()
    _set(conn, "slack_member_id", "U111")
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    _set(conn, "slack_member_id", "U222")
    with pytest.raises(StepupFailedError, match="changed"):
        stepup.verify(conn, clock, s, n.nonce_id)
    assert s.reasons == []  # no dialog was shown


def test_declined_and_unavailable_never_verify(conn: sqlite3.Connection, clock: FakeClock) -> None:
    s = FakeStepper(["declined"])
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    assert stepup.verify(conn, clock, s, n.nonce_id) == "declined"
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "test.setting", TARGET, n.nonce_id)
    n2 = stepup.issue(conn, clock, None, "test.setting", TARGET)
    assert stepup.verify(conn, clock, None, n2.nonce_id) == "unavailable"


def test_tries_are_rate_limited(conn: sqlite3.Connection, clock: FakeClock) -> None:
    s = FakeStepper(["failed"])
    for _ in range(stepup.MAX_TRIES):
        n = stepup.issue(conn, clock, s, "test.setting", TARGET)
        assert stepup.verify(conn, clock, s, n.nonce_id) == "failed"
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    with pytest.raises(RateLimitedError):
        stepup.verify(conn, clock, s, n.nonce_id)
    clock.advance(10 * 60)
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    assert stepup.verify(conn, clock, s, n.nonce_id) == "failed"  # a new window


def test_unknown_purpose(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(InvalidInputError):
        stepup.issue(conn, clock, FakeStepper(), "nope", {})


def test_audit_has_no_password(conn: sqlite3.Connection, clock: FakeClock) -> None:
    s = PamStepper(lambda _u, p: p == "hunter2-secret")
    n = stepup.issue(conn, clock, s, "test.setting", TARGET)
    assert n.needs_password
    assert stepup.verify(conn, clock, s, n.nonce_id, password="hunter2-secret") == "verified"
    rows = [dict(r) for r in conn.execute("SELECT event, data FROM audit ORDER BY id")]
    assert [r["event"] for r in rows] == ["stepup.requested", "stepup.verified"]
    assert "hunter2" not in json.dumps(rows)


# ---- API and client -------------------------------------------------------------------------


def _state(db_path: Path, clock: FakeClock, s: FakeStepper) -> ServiceState:
    return ServiceState(install="t", token="cli-token", started_at="t", clock=clock,
                        db_path=db_path, stepper=s)  # fmt: skip


def _call(st: ServiceState, method: str, path: str, token: str, body: Any = None) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(st))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, json=body,
                                   headers={"Authorization": f"Bearer {token}"})  # fmt: skip

    return anyio.run(go)


def test_routes_refuse_mcp_profile_tokens(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    st = _state(db_path, clock, FakeStepper())
    work = _call(st, "POST", "/v1/sessions", "cli-token").json()["profile_token"]
    r = _call(st, "POST", "/v1/stepup/nonces", work, {"purpose": "test", "target": {}})
    assert r.status_code == 403 and r.json()["code"] == "forbidden_profile"
    r = _call(st, "POST", "/v1/stepup/nonces", "cli-token", {"purpose": "test", "target": {}})
    assert r.status_code == 200
    nonce = r.json()["nonce_id"]
    assert _call(st, "POST", f"/v1/stepup/{nonce}/verify", work).status_code == 403
    v = _call(st, "POST", f"/v1/stepup/{nonce}/verify", "cli-token", {})
    assert v.json() == {"verified": True, "outcome": "verified"}


class _Client:
    """A LocalClient stand-in that talks to the app in-process."""

    def __init__(self, st: ServiceState) -> None:
        self.st = st

    def request(self, method: str, path: str, json: Any = None, **_kw: Any) -> Any:
        r = _call(self.st, method, path, "cli-token", json)
        if r.is_success:
            return r.json()
        from ecf.errors import EcfError  # noqa: PLC0415

        raise EcfError.from_problem(r.json())


def test_client_steps_up_for_what_the_service_names(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    s = FakeStepper()
    st = _state(db_path, clock, s)
    c: Any = _Client(st)
    printed: list[str] = []
    seen: list[str | None] = []

    def action(nonce: str | None) -> str:
        seen.append(nonce)
        db = st.connect()
        try:
            stepup.consume(db, clock, "test.setting", TARGET, nonce)
        finally:
            db.close()
        return "done"

    assert with_step_up(c, action, echo=printed.append) == "done"
    assert seen[0] is None and seen[1] is not None
    assert printed[0].startswith("Step-up: ecf: set slack_member_id") and "code" in printed[1]

    refusing: Any = _Client(_state(db_path, clock, FakeStepper(["declined"])))
    with pytest.raises(StepupFailedError, match="you declined"):
        step_up(refusing, "test", {}, echo=printed.append)


def test_dialog_text_from_email_is_cleaned() -> None:
    """Subjects and senders reach the Touch ID dialog and the terminal (V1.2 review, 2026-09-30)."""
    from ecf.text import one_line, plain  # noqa: PLC0415

    rlo, esc, zwsp, bell = chr(0x202E), chr(0x1B), chr(0x200B), chr(0x07)
    evil = f"Invoice{rlo} fdp.exe{esc}[2K\r\n\nApprove: label{zwsp}"
    bound = stepup.Bound("h", f"ecf: archive the email from x@a.example, {evil}")
    shown = stepup._dialog(bound, "ABCD")  # pyright: ignore[reportPrivateUsage]
    assert not {rlo, esc, zwsp, "\n", "\r"} & set(shown)
    assert shown.endswith("Approve: label (code ABCD)")
    assert plain(f"a{rlo}b{bell}c\nd") == "abc\nd"
    assert one_line("a  \n b" + "x" * 500, 10) == "a bxxxxxx…"
