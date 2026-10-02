"""The Sender port and its SMTP adapter (SPEC §8.4, §15.4; OD-322, OD-324).

TLS only: port 465 is TLS from the start; port 587 must offer STARTTLS, and the connection is
refused without it (no plaintext fallback). Certificates and host names are verified, TLS 1.2 or
later, and AUTH happens only inside TLS. The app password is read at each connect and never kept.

A send runs MAIL, RCPT and the DATA command as separate steps, so a failure is classified by when
it happened (OD-322):
- before the server answered 354 to DATA, nothing was handed over: SendNotSentError
  (`retryable` for a temporary 4xx or a network error; not for a 5xx or a local refusal);
- once the message is being handed over, only the server's final reply settles it: 250 is sent,
  a 4xx or 5xx is SendNotSentError (the server didn't accept it), and anything else (a timeout,
  a dropped connection, a garbled reply) is SendOutcomeUnknownError, which is never retried.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from ecf.errors import MailUnavailableError
from ecf_server.mail.imap import MailLoginRejectedError, tls_context

PORTS = (465, 587)  # submission with TLS (RFC 8314) and with STARTTLS
CONNECT_S = 15.0
COMMAND_S = 60.0  # SPEC §15.4: an SMTP send has 60 s
_DOT = re.compile(rb"^\.", re.MULTILINE)


class SendNotSentError(MailUnavailableError):
    """The message was not accepted, so nothing was sent; `retryable` when trying again may work."""

    def __init__(self, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.retryable = retryable


class SendOutcomeUnknownError(MailUnavailableError):
    """The message was handed over but no final reply came: it may have been sent. Never retried;
    settled from the Sent folder or by a person (§6.2)."""


@dataclass(frozen=True)
class SmtpInfo:
    host: str
    port: int
    size: int | None  # the server's announced SIZE limit, if any
    eight_bit: bool  # 8BITMIME


class Sender(Protocol):
    def check(self) -> SmtpInfo:
        """Connect, start TLS and log in, then quit. Sends nothing."""
        ...

    def send(self, raw: bytes, mail_from: str, rcpt: Sequence[str]) -> None:
        """Submit one message; raises SendNotSentError, SendOutcomeUnknownError or
        MailLoginRejectedError."""
        ...


SenderFactory = Callable[[str, int, str, Callable[[], str]], Sender]  # (host, port, user, password)


class SmtpSender:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: Callable[[], str],
        *,
        ssl_context: ssl.SSLContext | None = None,
        implicit_tls: bool | None = None,
    ) -> None:
        """TLS from the start on 465, STARTTLS otherwise (`implicit_tls` overrides, for tests);
        which ports an address may use is the configuration's rule (PORTS)."""
        ctx = ssl_context or tls_context()
        if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
            raise ValueError("SMTP TLS context must verify certificates and host names")
        ctx.minimum_version = max(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        self._host, self._port, self._user, self._password = host, port, user, password
        self._ctx = ctx
        self._implicit = port == 465 if implicit_tls is None else implicit_tls

    def _open(self) -> smtplib.SMTP:
        """A logged-in connection inside TLS; every failure here is before any handover."""
        try:
            if self._implicit:
                s: smtplib.SMTP = smtplib.SMTP_SSL(
                    self._host, self._port, timeout=CONNECT_S, context=self._ctx
                )
            else:
                s = smtplib.SMTP(self._host, self._port, timeout=CONNECT_S)
        except (OSError, smtplib.SMTPException) as exc:
            raise SendNotSentError(f"cannot connect to {self._host}: {_why(exc)}",
                                   retryable=True) from None  # fmt: skip
        try:
            if s.sock is not None:
                s.sock.settimeout(COMMAND_S)
            s.ehlo()
            if not self._implicit:
                if not s.has_extn("starttls"):
                    raise SendNotSentError(f"{self._host} doesn't offer STARTTLS",
                                           retryable=False)  # fmt: skip
                s.starttls(context=self._ctx)
                s.ehlo()
            s.login(self._user, self._password())
        except smtplib.SMTPAuthenticationError:
            _quiet(s.quit)
            raise MailLoginRejectedError(
                f"{self._host} rejected the SMTP login for {self._user}"
            ) from None
        except smtplib.SMTPNotSupportedError:
            _quiet(s.quit)
            raise SendNotSentError(f"{self._host} offers no login (AUTH)",
                                   retryable=False) from None  # fmt: skip
        except SendNotSentError:
            _quiet(s.quit)
            raise
        except (OSError, smtplib.SMTPException) as exc:
            _quiet(s.quit)
            raise SendNotSentError(f"SMTP setup with {self._host} failed: {_why(exc)}",
                                   retryable=True) from None  # fmt: skip
        except BaseException:  # e.g. no password stored: never leave the connection open
            _quiet(s.quit)
            raise
        return s

    def check(self) -> SmtpInfo:
        s = self._open()
        try:
            return _info(self._host, self._port, s)
        finally:
            _quiet(s.quit)

    def send(self, raw: bytes, mail_from: str, rcpt: Sequence[str]) -> None:
        if not rcpt:
            raise SendNotSentError("no recipients", retryable=False)
        s = self._open()
        try:
            _submit(s, _info(self._host, self._port, s), raw, mail_from, rcpt)
        finally:
            _quiet(s.quit)


def _info(host: str, port: int, s: smtplib.SMTP) -> SmtpInfo:
    size = s.esmtp_features.get("size", "").strip()
    return SmtpInfo(host=host, port=port, size=int(size) if size.isdigit() and int(size) else None,
                    eight_bit=s.has_extn("8bitmime"))  # fmt: skip


def _submit(s: smtplib.SMTP, info: SmtpInfo, raw: bytes, mail_from: str,
            rcpt: Sequence[str]) -> None:  # fmt: skip
    eight_bit = any(b > 0x7F for b in raw)
    if eight_bit and not info.eight_bit:
        raise SendNotSentError("the message has 8-bit content and the server lacks 8BITMIME",
                               retryable=False)  # fmt: skip
    payload = _dot_stuff(raw)
    if info.size is not None and len(payload) > info.size:
        raise SendNotSentError(f"the message is larger than the server's limit ({info.size} bytes)",
                               retryable=False)  # fmt: skip
    opts = [f"SIZE={len(payload)}"] if info.size is not None else []
    if eight_bit:
        opts.append("BODY=8BITMIME")
    try:
        code, reply = s.mail(mail_from, opts)
        _accepted(code, reply, "MAIL", {250})
        for r in rcpt:
            code, reply = s.rcpt(r)
            _accepted(code, reply, "RCPT", {250, 251})
        s.putcmd("data")
        code, reply = s.getreply()
        _accepted(code, reply, "DATA", {354})
    except SendNotSentError:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        raise SendNotSentError(f"SMTP failed before the message was sent: {_why(exc)}",
                               retryable=True) from None  # fmt: skip
    # From here the server may have the message: only its final reply settles it.
    try:
        s.send(payload)
        code, reply = s.getreply()
    except (OSError, smtplib.SMTPException) as exc:
        raise SendOutcomeUnknownError(
            f"no final reply after the message was handed over: {_why(exc)}"
        ) from None
    if code == 250:
        return
    if 400 <= code < 600:
        raise SendNotSentError(f"the server refused the message: {code} {_text(reply)}",
                               retryable=code < 500)  # fmt: skip
    raise SendOutcomeUnknownError(f"unexpected final reply {code}")


def _accepted(code: int, reply: bytes, step: str, ok: set[int]) -> None:
    if code not in ok:
        raise SendNotSentError(f"{step} refused: {code} {_text(reply)}",
                               retryable=400 <= code < 500 or code == -1)  # fmt: skip


def _dot_stuff(raw: bytes) -> bytes:
    """The DATA payload: CRLF line ends assumed (the builder writes them), leading dots doubled
    (RFC 5321 §4.5.2), and the end-of-data line."""
    q = _DOT.sub(b"..", raw)
    if not q.endswith(b"\r\n"):
        q += b"\r\n"
    return q + b".\r\n"


def _text(reply: bytes | str) -> str:
    t = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
    return " ".join(t.split())[:200]


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]


def _quiet(fn: Callable[[], object]) -> None:
    with suppress(Exception):  # best-effort QUIT on a finished or failing connection
        fn()
