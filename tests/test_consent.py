"""Resuming an address v3.0.0 converted from preset A or B to C is the consent act (ADR 0031, P3;
operator decision 2026-10-14; OD-427): the service refuses it unless the request says the
account's owner agreed, `--all` and the Slack buttons leave it paused, and `ecf resume <address>`
asks in a terminal (no flag skips the question). Ordinary addresses resume as before."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from typer.testing import CliRunner

import ecf.cli_items
from ecf.cli import app
from ecf.client import parse_reply
from ecf.doctor import Level, judge_converted
from ecf.errors import ConflictError, InvalidInputError
from ecf.paths import Paths
from ecf.prompts import NO_TERMINAL
from ecf_server import pause, slack_in, stages
from ecf_server.api import ServiceState
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.helpers import add_address
from tests.test_addresses import TOKEN, call, make_state

QUESTION = "Has the account owner agreed to their mail and derived signals going to Anthropic?"


@pytest.fixture
def converted(conn: sqlite3.Connection, clock: FakeClock) -> sqlite3.Connection:
    """`a1` converted from A and paused (as migration 0038 leaves it); `plain` paused by hand."""
    add_address(conn, clock, "a1", paused=True)
    add_address(conn, clock, "plain", paused=True)
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 'migration')", (stages.CONVERTED, json.dumps({"a1": "A"}),
                                       to_ts(clock.now())))  # fmt: skip
    return conn


def _events(conn: sqlite3.Connection, event: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM audit WHERE event = ?", (event,)).fetchall()


# The service side: a converted address resumes only with the owner's agreement, through the
# route, never through resume-all or Slack's button, and doctor names it until then.


def test_a_converted_address_resumes_only_with_the_owners_agreement(
    converted: sqlite3.Connection, clock: FakeClock
) -> None:
    conn = converted
    assert pause.needs_consent(conn, "a1") and not pause.needs_consent(conn, "plain")
    with pytest.raises(ConflictError, match="ecf resume a1"):
        pause.set_paused(conn, clock, "a1", False, actor="os_user")
    with pytest.raises(ConflictError):  # by email too
        pause.set_paused(conn, clock, "a1@acme.example", False, actor="os_user")
    assert pause.is_paused(conn, "a1") and _events(conn, "address.consent_confirmed") == []
    assert pause.set_paused(conn, clock, "a1", False, actor="os_user", owner_agreed=True) == ["a1"]
    assert not pause.is_paused(conn, "a1") and not pause.needs_consent(conn, "a1")
    (row,) = _events(conn, "address.consent_confirmed")
    assert (row["address_id"], row["actor"], row["data"]) == ("a1", "os_user", "{}")
    assert row["ts"] == to_ts(clock.now())  # the address and time only
    assert stages.converted(conn)[0]["consented"] is True
    # once agreed, a later pause and resume is ordinary
    pause.set_paused(conn, clock, "a1", True, actor="os_user")
    assert pause.set_paused(conn, clock, "a1", False, actor="os_user") == ["a1"]
    assert len(_events(conn, "address.consent_confirmed")) == 1


def test_resume_all_leaves_a_converted_address_paused(
    converted: sqlite3.Connection, clock: FakeClock
) -> None:
    conn = converted
    changed = pause.set_paused(conn, clock, pause.ALL, False, actor="os_user")
    assert changed == ["plain"] and pause.paused_addresses(conn) == ["a1"]
    assert pause.awaiting_consent(conn) == ["a1"]
    text = pause.describe(changed, False, pause.awaiting_consent(conn))
    assert text.startswith("Resumed plain.") and "ecf resume <address> asks): a1." in text
    # even saying the owner agreed doesn't resume it with --all: consent is per address
    pause.set_paused(conn, clock, pause.ALL, False, actor="os_user", owner_agreed=True)
    assert pause.paused_addresses(conn) == ["a1"]


def test_slacks_resume_button_never_resumes_a_converted_address(
    converted: sqlite3.Connection, clock: FakeClock
) -> None:
    click = slack_in.Click("button", pause.RESUME, "a1", None, "U0ME1")
    with pytest.raises(ConflictError):
        slack_in.HANDLERS[pause.RESUME](converted, clock, click)
    assert pause.is_paused(converted, "a1")


def test_the_route_needs_owner_agreed(converted: sqlite3.Connection, db_path: Path) -> None:
    state = make_state(db_path, None)
    r = call(state, "POST", "/v1/addresses/a1/resume", {}, TOKEN)
    assert r.status_code == 409 and "asks whether the account's owner agreed" in r.text
    r = call(state, "POST", "/v1/addresses/a1/resume", {"owner_agreed": "yes"}, TOKEN)
    assert r.status_code == 409  # only `true` counts
    r = call(state, "POST", "/v1/addresses/a1/resume", {"owner_agreed": True}, TOKEN)
    assert r.status_code == 200 and r.json()["changed"] == ["a1"]
    r = call(state, "POST", "/v1/addresses/plain/resume", None, TOKEN)  # an older client's call
    assert r.status_code == 200 and r.json()["changed"] == ["plain"]


def test_doctor_names_paused_converted_addresses_until_resumed() -> None:
    row = {"address_id": "a1", "from": "A", "paused": True, "consented": False, "waiting": 3}
    rows = [row, row | {"address_id": "b1", "paused": False, "consented": True},
            row | {"address_id": "c1", "consented": True}]  # fmt: skip
    (check,) = judge_converted(rows)
    assert (check.name, check.level) == ("converted a1", Level.WARN)
    assert "was preset A" in check.detail and "3 emails waiting" in check.detail
    assert check.fix.startswith("ecf resume a1 once the account's owner agrees (OD-427)")
    assert "ecf address remove a1" in check.fix
    assert judge_converted([]) == []


# The CLI: `ecf resume` asks before resuming a converted address and only y resumes; resume-all
# never asks, and the question needs a terminal.


class Fake:
    """LocalClient over the ASGI app, recording what the CLI sends."""

    def __init__(self, st: ServiceState) -> None:
        self.st = st
        self.sent: list[tuple[str, str, Any]] = []

    def __call__(self, _p: Paths) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
        self.sent.append((method, path, json))
        return parse_reply(call(self.st, method, path, json, TOKEN))

    def get(self, path: str, **_: Any) -> Any:
        return self.request("GET", path)


@pytest.fixture
def cli(converted: sqlite3.Connection, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    del converted
    fake = Fake(make_state(db_path, None))
    monkeypatch.setattr(ecf.cli_items, "LocalClient", fake)
    monkeypatch.setattr(ecf.cli_items, "require_terminal", lambda: None)
    return fake


def _posts(fake: Fake) -> list[tuple[str, Any]]:
    return [(p, b) for m, p, b in fake.sent if m == "POST"]


def test_ecf_resume_asks_and_y_resumes(cli: Fake, conn: sqlite3.Connection) -> None:
    r = CliRunner().invoke(app, ["resume", "a1"], input="y\n")
    assert r.exit_code == 0, r.output
    assert QUESTION in r.output and "[y/N]" in r.output and "Resumed a1." in r.output
    assert _posts(cli) == [("/v1/addresses/a1/resume", {"owner_agreed": True})]
    assert not pause.is_paused(conn, "a1")


@pytest.mark.parametrize("answer", ["n", "", "yes", "Y es"])
def test_anything_but_y_leaves_it_paused_and_says_how_to_remove_it(
    cli: Fake, conn: sqlite3.Connection, answer: str
) -> None:
    r = CliRunner().invoke(app, ["resume", "a1"], input=f"{answer}\n")
    assert r.exit_code == 1
    assert "a1 stays paused" in r.output and "ecf address remove a1" in r.output
    assert _posts(cli) == [] and pause.is_paused(conn, "a1")


def test_an_email_ref_is_asked_too(cli: Fake, conn: sqlite3.Connection) -> None:
    r = CliRunner().invoke(app, ["resume", "a1@acme.example"], input="y\n")
    assert r.exit_code == 0, r.output
    assert QUESTION in r.output
    assert not pause.is_paused(conn, "a1")


def test_an_ordinary_address_resumes_without_asking(cli: Fake, conn: sqlite3.Connection) -> None:
    r = CliRunner().invoke(app, ["resume", "plain"])
    assert r.exit_code == 0, r.output
    assert QUESTION not in r.output and "Resumed plain." in r.output
    assert _posts(cli) == [("/v1/addresses/plain/resume", {})]


def test_resume_all_never_asks_and_names_whats_left(cli: Fake, conn: sqlite3.Connection) -> None:
    r = CliRunner().invoke(app, ["resume", "--all"])
    assert r.exit_code == 0, r.output
    assert QUESTION not in r.output and "Resumed plain." in r.output and ": a1." in r.output
    assert pause.paused_addresses(conn) == ["a1"]


def test_the_question_needs_a_terminal_and_no_flag_skips_it(
    cli: Fake, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    def no_terminal() -> None:
        raise InvalidInputError(NO_TERMINAL)

    monkeypatch.setattr(ecf.cli_items, "require_terminal", no_terminal)
    r = CliRunner().invoke(app, ["resume", "a1"], input="y\n")
    assert r.exit_code != 0 and QUESTION not in r.output and pause.is_paused(conn, "a1")
    r = CliRunner().invoke(app, ["resume", "a1", "--yes"])
    assert r.exit_code == 2 and "No such option" in r.output  # consent is typed, never a flag
