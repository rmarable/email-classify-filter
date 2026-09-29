"""The IMAP adapter against a real Dovecot server (contract tests plus IMAP-specific checks)."""

from __future__ import annotations

import logging
import ssl
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest

from ecf.errors import MailUnavailableError
from ecf_server.mail import MailSource
from ecf_server.mail.imap import ImapSource, MailLoginRejectedError
from tests import dovecot
from tests.mail_contract import Harness, MailSourceContract, message

pytestmark = pytest.mark.imap


@pytest.fixture
def server(dovecot_server: dovecot.Dovecot) -> dovecot.Dovecot:
    return dovecot_server


def _source(dv: dovecot.Dovecot, user: str, password: str = dovecot.PASSWORD) -> ImapSource:
    return ImapSource(dv.host, user, lambda: password, port=dv.port, ssl_context=dv.context())


class DovecotHarness:
    def __init__(self, dv: dovecot.Dovecot) -> None:
        self.user = f"ecf-t-{uuid.uuid4().hex[:12]}"
        self.imap = _source(dv, self.user)
        self.source: MailSource = self.imap
        self._admin = dv.admin(self.user)

    def deliver(self, raw: bytes, when: datetime) -> None:
        dovecot.append(self._admin, raw, when)

    def expunge(self, uid: int) -> None:
        dovecot.expunge(self._admin, uid)

    def close(self) -> None:
        self.imap.close()
        self._admin.logout()


class TestImapContract(MailSourceContract):
    @pytest.fixture
    def harness(self, server: dovecot.Dovecot) -> Iterator[Harness]:
        h = DovecotHarness(server)
        yield h
        h.close()


def test_wrong_password_is_login_rejected(server: dovecot.Dovecot) -> None:
    src = _source(server, "ecf-t-login", password="wrong")
    with pytest.raises(MailLoginRejectedError) as info:
        src.inbox()
    assert "wrong" not in str(info.value)


def test_untrusted_certificate_is_refused(server: dovecot.Dovecot) -> None:
    src = ImapSource(server.host, "ecf-t-tls", lambda: dovecot.PASSWORD, port=server.port)
    with pytest.raises(MailUnavailableError) as info:
        src.inbox()
    assert MailLoginRejectedError not in type(info.value).__mro__


def test_contexts_that_skip_verification_are_refused(server: dovecot.Dovecot) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="must verify"):
        ImapSource(server.host, "x", lambda: "x", port=server.port, ssl_context=ctx)


def test_reconnects_after_the_server_drops_the_connection(server: dovecot.Dovecot) -> None:
    h = DovecotHarness(server)
    try:
        h.deliver(message(0), datetime.now().astimezone())
        assert len(h.imap.uids_after(0)) == 1
        conn = h.imap._conn  # pyright: ignore[reportPrivateUsage]
        assert conn is not None
        conn._c.shutdown()  # pyright: ignore[reportPrivateUsage]  # simulate a dropped link
        with pytest.raises(MailUnavailableError):
            h.imap.uids_after(0)
        assert len(h.imap.uids_after(0)) == 1  # the next call reconnects
    finally:
        h.close()


def test_writes_do_not_mark_read(server: dovecot.Dovecot) -> None:
    h = DovecotHarness(server)
    try:
        h.deliver(message(0), datetime.now().astimezone())
        (u,) = h.imap.uids_after(0)
        h.imap.add_keyword(u, "$ecf_test_regulatory")
        h.imap.fetch(u)  # read after switching to the read-write selection
        assert "\\Seen" not in h.imap.flags([u])[u]
    finally:
        h.close()


def test_imaplib_debug_output_is_off(
    server: dovecot.Dovecot, caplog: pytest.LogCaptureFixture
) -> None:
    """imapclient turns imaplib's debug output on; at DEBUG it would log the LOGIN line with the
    app password and every fetched message, and it costs memory even when nothing logs (OD-195)."""
    h = DovecotHarness(server)
    marker = "contract-17@synthetic"  # in the fetched message's Message-ID
    try:
        h.deliver(message(17), datetime.now().astimezone())
        with caplog.at_level(logging.DEBUG):
            (u,) = h.imap.uids_after(0)
            raw = h.imap.fetch(u)
        assert raw is not None and marker.encode() in raw
        logged = caplog.text
        assert dovecot.PASSWORD not in logged and marker not in logged
        assert h.imap._conn is not None  # pyright: ignore[reportPrivateUsage]
        assert h.imap._conn._c._imap.debug == 0  # pyright: ignore[reportPrivateUsage]
    finally:
        h.close()
