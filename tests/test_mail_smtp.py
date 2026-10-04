"""The SMTP Sender against an in-process server (aiosmtpd; OD-308): TLS both ways, login, and how
each failure is classified (not sent and retryable, not sent, or outcome unknown; OD-322)."""

from __future__ import annotations

import socket
import ssl
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer
from aiosmtpd.smtp import AuthResult, Envelope, LoginPassword, Session

from ecf_server.mail.imap import MailLoginRejectedError
from ecf_server.mail.smtp import SendNotSentError, SendOutcomeUnknownError, SmtpSender
from tests.dovecot import _cert  # pyright: ignore[reportPrivateUsage]

PASSWORD = "smtp-test-pass"
RAW = (
    b"From: ap@acme.example\r\nTo: lead@acme.example\r\nSubject: hi\r\n"
    b"Message-ID: <t1@acme.example>\r\n\r\nline one\r\n.starts with a dot\r\n"
)


@dataclass
class Handler:
    mode: str = "ok"  # ok | rcpt550 | rcpt451 | data554 | data451 | drop
    got: list[tuple[str, list[str], bytes]] = field(
        default_factory=list[tuple[str, list[str], bytes]]
    )

    async def handle_RCPT(self, server: SMTPServer, session: Session, envelope: Envelope,
                          address: str, rcpt_options: list[str]) -> str:  # fmt: skip
        if self.mode == "rcpt550":
            return "550 no such user"
        if self.mode == "rcpt451":
            return "451 try later"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server: SMTPServer, session: Session, envelope: Envelope) -> str:
        if self.mode == "drop":
            assert server.transport is not None
            server.transport.close()  # the client never gets a final reply
            return "250 OK"
        if self.mode == "data554":
            return "554 rejected"
        if self.mode == "data451":
            return "451 later"
        content = envelope.original_content or b""
        self.got.append((str(envelope.mail_from), list(envelope.rcpt_tos), content))
        return "250 OK"


def _auth(server: SMTPServer, session: Session, envelope: Envelope, mechanism: str,
          data: Any) -> AuthResult:  # fmt: skip
    ok = isinstance(data, LoginPassword) and data.password == PASSWORD.encode()
    return AuthResult(success=ok, handled=False)  # handled=True would send no reply


@dataclass
class Server:
    port: int
    handler: Handler
    client_ctx: ssl.SSLContext


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ecf_server.mail.smtp.COMMAND_S", 5.0)


@pytest.fixture(scope="module")
def certs() -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="ecf-smtp-") as d:
        yield _cert(Path(d))


def _start(certs: tuple[Path, Path], *, implicit: bool, starttls: bool = True,
           size: int = 0) -> Iterator[Server]:  # fmt: skip
    key, crt = certs
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(str(crt), str(key))
    client_ctx = ssl.create_default_context(cafile=str(crt))
    handler = Handler()
    # aiosmtpd doesn't count a TLS-from-the-start connection as TLS for AUTH, so it is required
    # only on the STARTTLS server (the client logs in only inside TLS either way)
    kwargs: dict[str, Any] = {"authenticator": _auth, "auth_require_tls": not implicit,
                              "data_size_limit": size}  # fmt: skip
    if not implicit and starttls:
        kwargs |= {"tls_context": server_ctx, "require_starttls": True}
    port = _free_port()
    ctl = Controller(handler, hostname="127.0.0.1", port=port, server_hostname="localhost",
                     ssl_context=server_ctx if implicit else None, **kwargs)  # fmt: skip
    ctl.start()
    try:
        yield Server(port, handler, client_ctx)
    finally:
        ctl.stop()


@pytest.fixture
def tls_server(certs: tuple[Path, Path]) -> Iterator[Server]:
    yield from _start(certs, implicit=True, size=100_000)


@pytest.fixture
def starttls_server(certs: tuple[Path, Path]) -> Iterator[Server]:
    yield from _start(certs, implicit=False)


@pytest.fixture
def plain_server(certs: tuple[Path, Path]) -> Iterator[Server]:
    yield from _start(certs, implicit=False, starttls=False)


def _sender(srv: Server, *, implicit: bool, password: str = PASSWORD) -> SmtpSender:
    calls: list[int] = []

    def pw() -> str:
        calls.append(1)
        return password

    s = SmtpSender("localhost", srv.port, "ap@acme.example", pw, ssl_context=srv.client_ctx,
                   implicit_tls=implicit)  # fmt: skip
    s.pw_calls = calls  # type: ignore[attr-defined]
    return s


def test_send_over_tls_dot_stuffed_and_password_read_per_connect(tls_server: Server) -> None:
    s = _sender(tls_server, implicit=True)
    info = s.check()
    assert info.size == 100_000
    s.send(RAW, "ap@acme.example", ["lead@acme.example"])
    [(frm, to, body)] = tls_server.handler.got
    assert (frm, to) == ("ap@acme.example", ["lead@acme.example"])
    assert body.replace(b"\r\n", b"\n") == RAW.replace(b"\r\n", b"\n")  # dots un-stuffed again
    assert len(s.pw_calls) == 2  # type: ignore[attr-defined]


def test_send_over_starttls(starttls_server: Server) -> None:
    s = _sender(starttls_server, implicit=False)
    s.send(RAW, "ap@acme.example", ["lead@acme.example"])
    assert len(starttls_server.handler.got) == 1


def test_no_starttls_is_refused_before_login(plain_server: Server) -> None:
    s = _sender(plain_server, implicit=False)
    with pytest.raises(SendNotSentError, match="STARTTLS") as e:
        s.send(RAW, "ap@acme.example", ["lead@acme.example"])
    assert not e.value.retryable
    assert s.pw_calls == []  # type: ignore[attr-defined]  # the password never left


def test_wrong_password_is_a_rejected_login(tls_server: Server) -> None:
    with pytest.raises(MailLoginRejectedError):
        _sender(tls_server, implicit=True, password="wrong").check()


def test_untrusted_certificate_is_not_sent(tls_server: Server) -> None:
    s = SmtpSender("localhost", tls_server.port, "ap@acme.example", lambda: PASSWORD,
                   implicit_tls=True)  # system trust store: the test CA isn't in it  # fmt: skip
    with pytest.raises(SendNotSentError) as e:
        s.check()
    assert e.value.retryable


REFUSALS = [("rcpt550", False), ("rcpt451", True), ("data554", False), ("data451", True)]


@pytest.mark.parametrize(("mode", "retryable"), REFUSALS)
def test_refusals_are_not_sent(tls_server: Server, mode: str, retryable: bool) -> None:
    tls_server.handler.mode = mode
    with pytest.raises(SendNotSentError) as e:
        _sender(tls_server, implicit=True).send(RAW, "ap@acme.example", ["lead@acme.example"])
    assert e.value.retryable is retryable
    assert tls_server.handler.got == []


def test_no_final_reply_is_unknown(tls_server: Server) -> None:
    tls_server.handler.mode = "drop"
    with pytest.raises(SendOutcomeUnknownError):
        _sender(tls_server, implicit=True).send(RAW, "ap@acme.example", ["lead@acme.example"])


def test_over_the_size_limit_is_refused_locally(tls_server: Server) -> None:
    big = RAW + b"x" * 200_000 + b"\r\n"
    with pytest.raises(SendNotSentError, match="larger") as e:
        _sender(tls_server, implicit=True).send(big, "ap@acme.example", ["lead@acme.example"])
    assert not e.value.retryable


def test_eight_bit_needs_8bitmime(tls_server: Server) -> None:
    s = _sender(tls_server, implicit=True)
    s.send(RAW + "café\r\n".encode(), "ap@acme.example", ["lead@acme.example"])
    assert len(tls_server.handler.got) == 1  # aiosmtpd announces 8BITMIME
