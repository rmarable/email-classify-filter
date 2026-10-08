"""The read-only corpus reader (SPEC §16.7; OD-466, OD-467; R11, R12, R48, R148, R149, R191,
R192, R195)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from ecf.errors import InvalidInputError, MailUnavailableError
from ecf_server.mail import _imapclient as lib
from ecf_server.mail import corpus_reader as cr
from ecf_server.mail.corpus_reader import CorpusReader, LeanMeta, UidValidityChangedError
from ecf_server.mail.imap import MailLoginRejectedError
from tests import dovecot
from tests.corpus_fakes import FakeConn, FakeMessage, FakeServer

MINE = ("0" * 32, 2)


def mail(sender: str = "Vendor A <billing@vendor-a.example>", stamp: str | None = None) -> bytes:
    head = f"From: {sender}\r\nTo: pat@acme.example\r\nSubject: hello\r\n"
    if stamp:
        head += f"X-ECF-Install: {stamp}\r\n"
    return (head + "\r\nbody text\r\n").encode()


def reader(server: FakeServer) -> CorpusReader:
    return CorpusReader("imap.example", "pat@acme.example", lambda: "pw",
                        connect=lambda: FakeConn(server))  # fmt: skip


def opened(server: FakeServer, folder: str = "INBOX") -> CorpusReader:
    r = reader(server)
    r.open()
    r.examine(folder)
    return r


def test_folder_requests_resolve_by_role_or_name() -> None:
    none: frozenset[str] = frozenset()
    listed = [(none, "INBOX"), (frozenset({"\\All"}), "[Gmail]/Alle Nachrichten"),
              (frozenset({"\\Junk"}), "[Gmail]/Spam"), (none, "Receipts")]  # fmt: skip
    assert cr.resolve_folder(listed, "default", gmail=True, allow_spam=False) == (
        "[Gmail]/Alle Nachrichten"  # by role, whatever the account's language calls it
    )
    assert cr.resolve_folder(listed, "", gmail=False, allow_spam=False) == "INBOX"
    assert cr.resolve_folder(listed, "inbox", gmail=True, allow_spam=False) == "INBOX"
    assert cr.resolve_folder(listed, "Receipts", gmail=True, allow_spam=False) == "Receipts"
    with pytest.raises(InvalidInputError, match="--allow-spam"):
        cr.resolve_folder(listed, "[Gmail]/Spam", gmail=True, allow_spam=False)
    assert cr.resolve_folder(listed, "[Gmail]/Spam", gmail=True, allow_spam=True) == "[Gmail]/Spam"
    with pytest.raises(InvalidInputError, match="Show in IMAP"):
        cr.resolve_folder(listed[:1], "default", gmail=True, allow_spam=False)  # never INBOX
    with pytest.raises(InvalidInputError, match="no folder named"):
        cr.resolve_folder(listed, "Nope", gmail=True, allow_spam=False)


def test_examine_is_read_only_and_records_the_folder() -> None:
    server = FakeServer()
    server.folders["INBOX"] = {3: FakeMessage(mail()), 9: FakeMessage(mail())}
    r = opened(server)
    assert (r.folder.uidvalidity, r.folder.uidnext, r.folder.exists) == (7, 10, 2)
    assert "SELECT" not in server.commands and not r.gmail


def test_windows_list_a_huge_sparse_folder_that_one_search_could_not() -> None:
    """R48, R149: 150,000 UIDs in one SEARCH response overrun the 1 MB line; windows don't."""
    server = FakeServer()
    server.folders["INBOX"] = {u: FakeMessage(b"x") for u in range(1, 300_001, 2)}
    r = opened(server)
    with pytest.raises(ValueError, match="too wide"):
        r.window(1, 300_000)
    assert len(r.all_uids()) == 150_000
    assert r.window(10, 14) == [11, 13] and r.window(5, 4) == []


def test_lean_meta_reads_stamps_senders_and_gmail_labels() -> None:
    server = FakeServer(gmail=True)
    server.folders["INBOX"] = {
        1: FakeMessage(mail(stamp="f" * 32 + ".1"), labels=("\\Inbox",)),
        2: FakeMessage(mail("Pat <Pat@acme.example>, other@x.example"), labels=("\\Sent",)),
    }
    r = opened(server)
    assert r.gmail
    by_range = r.meta_range(1, 2)
    assert by_range == r.meta([2, 1])
    one, two = by_range[1], by_range[2]
    assert one.install_stamps == ("f" * 32 + ".1",) and one.labels == frozenset({"\\Inbox"})
    assert two.from_addrs == ("pat@acme.example", "other@x.example") and two.gmail_msgid
    assert one.size == len(server.folders["INBOX"][1].raw)


def _meta(
    labels: frozenset[str] | set[str] = frozenset(),
    stamps: tuple[str, ...] = (),
    froms: tuple[str, ...] = ("billing@vendor-a.example",),
) -> LeanMeta:
    return LeanMeta(1, 10, datetime(2026, 10, 1, tzinfo=UTC), frozenset(labels), None, stamps,
                    froms)  # fmt: skip


def test_own_mail_rules() -> None:
    """R22, R148, R191, R192."""
    src = "Pat.Lee@gmail.com"

    def own(m: LeanMeta, *, include: bool = False) -> bool:
        return cr.is_own(m, source=src, mine=MINE, include_own=include)

    assert own(_meta({"\\Draft"})) and own(_meta({"\\Drafts"}))
    assert own(_meta(stamps=("0" * 32 + ".2",)))  # this install, this generation
    assert not own(_meta(stamps=("0" * 32 + ".1",)))  # an older copy of this install: kept
    assert not own(_meta(stamps=("forged",)))  # trigger 9 material: kept
    assert own(_meta({"\\Sent"}))  # sent to someone else
    assert not own(_meta({"\\Sent", "\\Inbox"}, froms=("patlee+x@googlemail.com",)))  # to self
    assert own(_meta({"\\Sent", "\\Inbox"}, froms=()))  # no usable From: not a note to self
    assert not own(_meta({"\\Inbox"}))
    assert not own(_meta({"\\Draft"}), include=True)


def test_header_fields_parse_strictly() -> None:
    stamps, froms = cr.header_fields(b"From: A <a@x.example>\r\nFrom: b@y.example\r\n\r\n")
    assert stamps == () and froms == ("a@x.example", "b@y.example")
    assert cr.header_fields(b"From: not an address\r\n\r\n")[1] == ()
    assert cr.header_fields(b"") == ((), ())


def test_reconnect_checks_uidvalidity() -> None:
    server = FakeServer()
    server.folders["INBOX"] = {1: FakeMessage(mail())}
    r = opened(server)
    server.drops = 1
    with pytest.raises(MailUnavailableError):
        r.fetch(1)
    r.reconnect()
    assert r.fetch(1) == server.folders["INBOX"][1].raw and server.logins == 2
    server.uidvalidity = 8
    with pytest.raises(UidValidityChangedError):
        r.reconnect()


def test_a_login_refused_after_one_that_worked_is_retryable() -> None:
    """R130: Gmail refuses a login over its connection limit; only the first login tests the
    password."""
    server = FakeServer(login_errors=1)
    with pytest.raises(MailLoginRejectedError):
        reader(server).open()
    server = FakeServer()
    server.folders["INBOX"] = {1: FakeMessage(mail())}
    r = opened(server)
    server.login_errors = 1
    with pytest.raises(MailUnavailableError) as exc:
        r.reconnect()
    assert not isinstance(exc.value, MailLoginRejectedError)


def test_uid_lists_stay_short() -> None:
    chunks = list(cr._by_octets(list(range(1_000_000, 1_000_400))))  # pyright: ignore[reportPrivateUsage]
    assert all(len(",".join(map(str, c))) <= cr.LIST_OCTETS for c in chunks)
    assert sum(len(c) for c in chunks) == 400


@pytest.mark.imap
def test_against_dovecot_reads_without_marking_seen(dovecot_server: dovecot.Dovecot) -> None:
    """The real thing: windows, lean meta, byte-identical bodies, and no `\\Seen` set."""
    user = f"ecf-t-{uuid.uuid4().hex[:12]}"
    admin = dovecot_server.admin(user)
    sent = [mail(f"Sender {i} <s{i}@vendor-a.example>") for i in range(3)]
    for i, raw in enumerate(sent):
        dovecot.append(admin, raw, datetime(2026, 9, 1 + i, 12, tzinfo=UTC))
    r = CorpusReader(dovecot_server.host, user, lambda: dovecot.PASSWORD,
                     port=dovecot_server.port, ssl_context=dovecot_server.context())  # fmt: skip
    try:
        r.open()
        name = cr.resolve_folder(r.folders(), "default", gmail=r.gmail, allow_spam=False)
        state = r.examine(name)
        assert (name, state.exists) == ("INBOX", 3)
        uids = r.window(1, state.uidnext - 1)
        meta = r.meta_range(1, state.uidnext - 1)
        assert sorted(meta) == uids and len(uids) == 3
        assert [meta[u].from_addrs for u in uids] == [(f"s{i}@vendor-a.example",) for i in range(3)]
        assert [r.fetch(u) for u in uids] == sent
        typ, data = admin.uid("FETCH", ",".join(map(str, uids)), "(FLAGS)")
        assert typ == "OK" and not any(b"\\Seen" in d for d in data if isinstance(d, bytes))
    finally:
        r.close()
        admin.logout()


def test_a_refused_login_carries_the_servers_reason_never_the_password() -> None:
    """Real-service test 1: Gmail's reason helps tell a wrong app password from a block."""
    exc = lib.LoginError("login failed: b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'")
    assert cr.server_reason(exc, "pw") == ": [AUTHENTICATIONFAILED] Invalid credentials (Failure)"
    echoed = lib.LoginError("b'NO bad password sekret-123 \\x1b[31m'")
    got = cr.server_reason(echoed, "sekret-123")
    assert "sekret-123" not in got and "[password removed]" in got and "\x1b" not in got
    assert cr.server_reason(lib.LoginError(""), "pw") == ""
    server = FakeServer(login_errors=1)
    with pytest.raises(MailLoginRejectedError, match="too many connections"):
        reader(server).open()
