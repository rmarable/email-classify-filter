"""`/v1/corpus` routes and `ecf corpus` (SPEC §16.7; OD-466; R56, R103, R105, R175, R176, R184,
R198)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, cast

import anyio
import httpx
import pytest

from ecf.cli_corpus import require_tty_output
from ecf.errors import InvalidInputError, StepupRequiredError
from ecf_server import corpus, stepup
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from ecf_server.mail.corpus_reader import CorpusReader
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.corpus_fakes import FakeConn
from tests.test_corpus import server_with

AUTH = {"Authorization": "Bearer secret-token"}
CORPUS_ROUTES = ("/v1/corpus/preflight", "/v1/corpus/fetch", "/v1/corpus", "/v1/corpus/stop",
                 "/v1/corpus/info", "/v1/corpus/merge")  # fmt: skip


def call(state: ServiceState, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, json={}, headers=headers)

    return anyio.run(go)


def test_every_corpus_route_needs_the_cli_token() -> None:
    """R176: an undecorated handler would answer anyone holding no token."""
    state = ServiceState(install="t", token="secret-token", started_at="2026-10-01T12:00:00Z")
    app = create_app(state)
    paths = sorted({str(getattr(r, "path", "")) for r in app.routes
                    if str(getattr(r, "path", "")).startswith("/v1/corpus")})  # fmt: skip
    assert paths == sorted(CORPUS_ROUTES)
    for p in CORPUS_ROUTES:
        method = "GET" if p == "/v1/corpus" else "POST"
        r = call(state, method, p, {})
        assert r.status_code == 401 and r.json()["code"] == "unauthorized", p


def test_a_dev_service_refuses_corpus_preflight_and_fetch() -> None:
    """R56, R106: a dev service's step-up is a fake."""
    state = ServiceState(install="t", token="secret-token", started_at="2026-10-01T12:00:00Z",
                         dev=cast(Any, object()))  # fmt: skip
    for p in ("/v1/corpus/preflight", "/v1/corpus/fetch", "/v1/corpus/merge"):
        r = call(state, "POST", p, AUTH)
        assert r.json()["code"] == "policy_denied", p
        assert "ecf-server dev" in r.json()["detail"]


def body(out: Path, **kw: object) -> dict[str, Any]:
    base: dict[str, Any] = {"email": "pat@acme.example", "host": "imap.example",
                            "app_password": "pw", "owner_email": "PAT@acme.example",
                            "out": str(out), "total": 2, "sleep_s": 0}  # fmt: skip
    return base | kw


def no_secret(_name: str) -> str | None:
    return None


def test_the_body_needs_the_owner_typed_and_a_one_off_for_an_unwatched_mailbox(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    req, password = corpus.from_body(conn, body(tmp_path / "c.ecfcorpus"), no_secret)
    assert req.email == "pat@acme.example" and req.address_id is None and password() == "pw"
    with pytest.raises(InvalidInputError, match="type the mailbox's email address exactly"):
        corpus.from_body(conn, body(tmp_path / "c.ecfcorpus", owner_email="x@y.example"),
                         no_secret)  # fmt: skip
    with pytest.raises(InvalidInputError, match="app password"):
        corpus.from_body(conn, body(tmp_path / "c.ecfcorpus", app_password=""), no_secret)
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                 " VALUES ('pat', 'pat@acme.example', 'standard', 'A',"
                 " '2026-10-01T12:00:00Z')")  # fmt: skip
    with pytest.raises(InvalidInputError, match="use --address pat"):
        corpus.from_body(conn, body(tmp_path / "c.ecfcorpus"), no_secret)


def test_begin_does_nothing_without_step_up_then_returns_the_passphrase_once(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, tmp_path: Path
) -> None:
    """R175, R198, R105: no login and no passphrase before step-up; then the passphrase is in the
    answer, never in status."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    server = server_with(3)
    logins: list[int] = []

    def make(req: corpus.Request, password: Any) -> CorpusReader:
        logins.append(1)
        return CorpusReader(req.host, req.email, password, connect=lambda: FakeConn(server))

    req, password = corpus.from_body(conn, body(out_dir / "c.ecfcorpus"), no_secret)

    def connect() -> sqlite3.Connection:
        from ecf_server import db  # noqa: PLC0415

        return db.connect(db_path)

    def go(nonce: str | None) -> dict[str, Any]:
        return corpus.begin(connect, clock, FakeNotifier(), db_path.parent, req, password,
                            nonce=nonce, own_passphrase=None, reader_factory=make,
                            spawn=lambda work: work())  # fmt: skip

    with pytest.raises(StepupRequiredError) as exc:
        go(None)
    assert logins == [] and corpus.RUN.snapshot()["state"] != "running"
    issued = stepup.issue(conn, clock, FakeStepper(), exc.value.extra["purpose"],
                          exc.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    got = go(issued.nonce_id)
    assert len(str(got["passphrase"]).split()) == 6 and logins == [1]
    status = corpus.RUN.snapshot()
    assert status["state"] == "done" and status["fetched"] == 2
    assert "passphrase" not in status and str(got["passphrase"]) not in str(status)
    assert corpus.open_corpus(Path(status["out"]), str(got["passphrase"])).header["count"] == 2


def test_output_must_be_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """R184: `> file` would capture the passphrase or excerpts."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    with pytest.raises(InvalidInputError, match="terminal"):
        require_tty_output()
