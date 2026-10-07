"""`ecf corpus fetch`: the service job (SPEC §16.7; OD-466 to OD-468)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError, StepupRequiredError
from ecf_server import corpus, download_budget, isolate, stepup
from ecf_server.clock import FakeClock
from ecf_server.isolate import Isolated, IsolationError
from ecf_server.mail.corpus_reader import CorpusReader
from ecf_server.message import parse
from ecf_server.notify import FakeNotifier
from ecf_server.senderauth import AuthOutcome
from tests.corpus_fakes import FakeConn, FakeMessage, FakeServer

SECRET = "correct horse battery staple paper clip"
ADDR = "ap"


def mail(i: int, sender: str = "billing@vendor-a.example", body: str = "") -> bytes:
    text = body or f"Invoice {i} is attached. Please pay by Friday.\r\n"
    return (f"From: Vendor <{sender}>\r\nTo: ap@acme.example\r\nSubject: Invoice {i}\r\n"
            f"Message-ID: <m{i}@vendor-a.example>\r\nDate: Thu, 1 Oct 2026 12:00:00 +0000\r\n"
            f"\r\n{text}").encode()  # fmt: skip


def stop_now(_s: float) -> bool:
    return True


def inline(raw: bytes, scan: int) -> Isolated:
    return isolate.isolated(parse(raw, max_scan_bytes=scan), AuthOutcome("none", "no signature"))


def server_with(n: int, **kw: object) -> FakeServer:
    s = FakeServer(**kw)  # type: ignore[arg-type]
    base = datetime(2026, 9, 1, tzinfo=UTC)
    s.folders["INBOX"] = {i: FakeMessage(mail(i), internaldate=base + timedelta(days=i))
                          for i in range(1, n + 1)}  # fmt: skip
    return s


def reader(s: FakeServer) -> CorpusReader:
    return CorpusReader("imap.example", "ap@acme.example", lambda: "pw",
                        connect=lambda: FakeConn(s))  # fmt: skip


def req(out: Path, **kw: object) -> corpus.Request:
    base: dict[str, object] = {"email": "ap@acme.example", "host": "imap.example", "port": 993,
                               "folder": "default", "out": str(out), "total": 3, "chunk": 2,
                               "sleep_s": 0}  # fmt: skip
    return corpus.Request(**(base | kw))  # type: ignore[arg-type]


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


@pytest.fixture
def data_dir(db_path: Path) -> Path:
    return db_path.parent


Fetched = tuple[Path | None, corpus.Progress, FakeNotifier]


def fetch(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, r: corpus.Request,
          s: FakeServer, **kw: object) -> Fetched:  # fmt: skip
    progress, notifier = corpus.Progress(), FakeNotifier()
    kw.setdefault("isolator", inline)
    got = corpus.run(conn, clock, notifier, data_dir, r, reader(s), SECRET, progress, **kw)  # type: ignore[arg-type]
    return got, progress, notifier


def test_fetch_writes_an_encrypted_corpus_newest_first(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    s = server_with(5)
    path, progress, notifier = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus"), s)
    assert path is not None and path == out_dir / "c.ecfcorpus" and progress.state == "done"
    assert path.stat().st_mode & 0o777 == 0o600
    raw = path.read_bytes()
    assert b"Invoice 5" not in raw and b"vendor-a.example" not in raw  # encrypted
    header = corpus.read_header(path)
    assert header["count"] == 3 and header["complete"] and header["source_domain"] == "acme.example"
    c = corpus.open_corpus(path, SECRET)
    assert [r["uid"] for r in c.rows] == [5, 4, 3]  # most recent first
    assert c.message(1) == s.folders["INBOX"][5].raw
    assert c.rows[0]["excerpts"]["classifier"].startswith("Invoice 5")
    assert c.rows[0]["display"]["subject"] == "Invoice 5" and "auth_result" in c.rows[0]["facts"]
    assert c.profile["source"] == "one_off" and c.first["install_id"]
    assert download_budget.used_email(conn, clock, "ap@acme.example") == sum(
        r["size"] for r in c.rows
    )
    audit = conn.execute("SELECT data FROM audit WHERE event = 'corpus.fetched'").fetchone()
    data = json.loads(audit["data"])
    assert data["count"] == 3 and "ap@acme.example" not in audit["data"] and "out" not in data
    assert len(data["mailbox"]) == 12 and str(out_dir) not in audit["data"]
    assert notifier.sent[0][0] == "[ecf-alert] Security Notice" and str(path) in notifier.sent[0][1]
    with pytest.raises(InvalidInputError, match="wrong passphrase"):
        corpus.open_corpus(path, "not it")


def test_a_tampered_header_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    path, *_ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus"), server_with(2))
    assert path is not None
    data = path.read_bytes().replace(b'"count": 2', b'"count": 9', 1)
    path.chmod(0o600)
    path.write_bytes(data)
    assert corpus.read_header(path)["count"] == 9  # the clear header alone can't be trusted
    with pytest.raises(InvalidInputError, match="doesn't match its manifest"):
        corpus.open_corpus(path, SECRET)


def test_skips_large_own_duplicate_and_unanalysable(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    s = server_with(6, gmail=True)
    s.flags["INBOX"] = frozenset({"\\All"})  # Gmail's default folder is All Mail (R26)
    s.folders["INBOX"][6] = FakeMessage(mail(6), labels=("\\Draft",))
    s.folders["INBOX"][5] = FakeMessage(mail(4))  # same message as 4
    s.folders["INBOX"][3] = FakeMessage(mail(3, body="x" * 2000))
    monkeypatch.setattr(corpus, "LARGE_BYTES", 1000)

    def flaky(raw: bytes, scan: int) -> Isolated:
        if b"Invoice 2" in raw:
            raise IsolationError("child timed out after 30 s")
        return inline(raw, scan)

    path, progress, _ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus", total=5), s,
                              isolator=flaky)  # fmt: skip
    assert path is not None
    assert progress.skipped == {"own": 1, "large": 1, "duplicate": 1, "facts": 1}
    c = corpus.open_corpus(path, SECRET)
    assert [r["uid"] for r in c.rows] == [5, 1] and c.header["reason"] == "folder_exhausted"
    assert c.header["complete"] and c.rows[0]["gmail_labels"] == []


def test_the_byte_cap_stops_the_run_but_completes_it(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    s = server_with(5)
    size = len(s.folders["INBOX"][5].raw)
    path, progress, _ = fetch(conn, clock, data_dir,
                              req(out_dir / "c.ecfcorpus", max_bytes=2 * size + 1), s)  # fmt: skip
    assert path is not None and progress.fetched == 2 and progress.reason == "byte_cap"
    assert corpus.read_header(path)["complete"]


def test_gmail_corpus_takes_at_most_half_of_what_is_left(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    s = server_with(5, gmail=True)
    s.flags["INBOX"] = frozenset({"\\All"})
    size = len(s.folders["INBOX"][5].raw)
    monkeypatch.setattr(download_budget, "GMAIL_BYTES_PER_DAY", 5 * size)  # S = 2.5 messages
    path, progress, _ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus", total=5), s)
    assert path is not None and progress.fetched == 2 and progress.reason == "byte_cap"


def test_a_stop_writes_a_partial_corpus(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    path, progress, _ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus", total=5),
                              server_with(5), sleep=stop_now)  # fmt: skip
    assert path is not None and progress.state == "stopped" and progress.fetched == 2
    header = corpus.read_header(path)
    assert not header["complete"] and header["reason"] == "stopped"


def test_a_dropped_connection_is_retried_and_a_renumbered_folder_stops(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    s = server_with(4)
    s.drops = 0
    waits: list[float] = []

    class Dropper(FakeConn):
        fetches = 0

        def fetch(self, uids: Sequence[int], items: Sequence[str]) -> dict[int, dict[str, Any]]:
            if list(items) == ["BODY.PEEK[]"]:
                Dropper.fetches += 1
                if Dropper.fetches == 2:
                    raise OSError("reset")
                if Dropper.fetches == 4:
                    self.s.uidvalidity += 1
                    raise OSError("reset")
            return super().fetch(uids, items)

    r = CorpusReader("imap.example", "ap@acme.example", lambda: "pw", connect=lambda: Dropper(s))
    progress = corpus.Progress()

    def note(w: float) -> bool:
        waits.append(w)
        return False

    path = corpus.run(conn, clock, FakeNotifier(), data_dir,
                      req(out_dir / "c.ecfcorpus", total=4), r, SECRET, progress,
                      isolator=inline, sleep=note)  # fmt: skip
    backoffs = [w for w in waits if w]  # the zeros are the between-chunk sleeps (--sleep 0)
    assert path is not None and backoffs == [5, 5]  # one back-off per failure, reset by success
    assert progress.fetched == 2 and progress.reason == "uidvalidity_changed"
    assert not corpus.read_header(path)["complete"]


def test_an_address_corpus_records_its_bytes_by_address(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                 " VALUES (?, 'ap@acme.example', 'standard', 'A', '2026-10-01T12:00:00.000000Z')",
                 (ADDR,))  # fmt: skip
    path, *_ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus", address_id=ADDR),
                     server_with(3))  # fmt: skip
    assert path is not None
    assert conn.execute("SELECT count(*) FROM corpus_downloads").fetchone()[0] == 0
    assert download_budget.used(conn, clock, ADDR) > 0
    c = corpus.open_corpus(path, SECRET)
    assert c.profile["source"] == "address" and c.header["source_preset"] == "A"


def test_nothing_fetched_writes_nothing(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    s = server_with(2, gmail=True)  # labels are read on Gmail only
    s.flags["INBOX"] = frozenset({"\\All"})
    for m in s.folders["INBOX"].values():
        m.labels = ("\\Draft",)
    path, progress, _ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus"), s)
    assert path is None and progress.state == "failed" and not (out_dir / "c.ecfcorpus").exists()


def test_random_order_samples_the_whole_folder(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    s = server_with(40)
    path, *_ = fetch(conn, clock, data_dir, req(out_dir / "c.ecfcorpus", order="random", total=5),
                     s)  # fmt: skip
    assert path is not None
    uids = [r["uid"] for r in corpus.open_corpus(path, SECRET).rows]
    assert len(set(uids)) == 5 and all(1 <= u <= 40 for u in uids)


def test_request_limits_and_paths(data_dir: Path, out_dir: Path, tmp_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    good = req(out_dir / "c.ecfcorpus")
    assert corpus.check_request(good, data_dir) == (out_dir / "c.ecfcorpus").resolve()
    for bad, why in (
        (req(out_dir / "c.ecfcorpus", total=5001), "--total"),
        (req(out_dir / "c.ecfcorpus", max_bytes=513 * corpus.MIB), "--max-bytes"),
        (req(out_dir / "c.ecfcorpus", order="biggest"), "--order"),
        (req(out_dir / "c.ecfb"), "end in"),
    ):
        with pytest.raises(InvalidInputError, match=why):
            corpus.check_request(bad, data_dir)
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere\n")  # a worktree: .git is a file (R32)
    with pytest.raises(InvalidInputError, match="git work tree"):
        corpus.check_request(req(repo / "sub" / "c.ecfcorpus"), data_dir)


def test_step_up_binds_the_request_but_never_the_password(
    conn: sqlite3.Connection, clock: FakeClock, out_dir: Path
) -> None:
    target = req(out_dir / "c.ecfcorpus").target(out_dir / "c.ecfcorpus")
    assert "pw" not in json.dumps(target) and target["folder"] == "default"
    bound = corpus._describe(conn, target)  # pyright: ignore[reportPrivateUsage]
    assert "a mailbox you own" in bound.prompt and "imap.example" in bound.prompt
    other = corpus._describe(conn, target | {"host": "evil.example"})  # pyright: ignore[reportPrivateUsage]
    assert other.hash != bound.hash
    with pytest.raises(StepupRequiredError):
        stepup.consume(conn, clock, "corpus_fetch", target, None)


def test_passphrase_rules() -> None:
    assert len(corpus.new_passphrase(None).split()) == 6
    assert corpus.new_passphrase(SECRET) == SECRET
    with pytest.raises(InvalidInputError, match="too weak for a corpus"):
        corpus.new_passphrase("a b c d e f g h i j k l m")  # 5+ words, but too short ones


def test_preflight_estimates_and_refuses_an_empty_folder(
    conn: sqlite3.Connection, clock: FakeClock, out_dir: Path
) -> None:
    got = corpus.preflight(conn, clock, req(out_dir / "c.ecfcorpus"), reader(server_with(5)))
    assert got["folder"] == "INBOX" and got["exists"] == 5 and got["fit_in_cap"] == 3
    assert got["batches"] == 2 and got["budget_share"] is None
    with pytest.raises(InvalidInputError, match="empty"):
        corpus.preflight(conn, clock, req(out_dir / "c.ecfcorpus"), reader(server_with(0)))


def test_a_dropped_noop_between_chunks_does_not_end_the_run(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path
) -> None:
    """Real-service test 1: a connection that died during the sleep fails the NOOP; the next fetch
    reconnects instead of the run stopping."""
    s = server_with(4)

    class NoopDropper(FakeConn):
        def noop(self) -> None:
            if not getattr(NoopDropper, "done", False):
                NoopDropper.done = True  # type: ignore[attr-defined]
                raise OSError("connection reset during the sleep")

    r = CorpusReader("imap.example", "ap@acme.example", lambda: "pw",
                     connect=lambda: NoopDropper(s))  # fmt: skip
    progress = corpus.Progress()
    path = corpus.run(conn, clock, FakeNotifier(), data_dir,
                      req(out_dir / "c.ecfcorpus", total=4, chunk=1), r, SECRET, progress,
                      isolator=inline)  # fmt: skip
    assert path is not None and progress.fetched == 4 and progress.state == "done"
    assert s.logins >= 2  # it reconnected


@pytest.mark.parametrize(("down", "fetched", "reason"), [(6, 4, ""), (9, 1, "mail_unavailable")])
def test_a_long_outage_is_survived_within_the_back_off(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out_dir: Path,
    down: int, fetched: int, reason: str,
) -> None:  # fmt: skip
    """Real-service test 1 (rc4): Wi-Fi off past 75 s ended the run. Now seven waits (about four
    minutes) pass before it gives up; the progress says it is reconnecting meanwhile."""
    s = server_with(4)
    notes: list[str] = []

    class Outage(FakeConn):
        def noop(self) -> None:
            if not getattr(Outage, "started", False):
                Outage.started = True  # type: ignore[attr-defined]
                self.s.drops = down  # every command fails until `down` have
            super().noop()

    r = CorpusReader("imap.example", "ap@acme.example", lambda: "pw", connect=lambda: Outage(s))
    progress = corpus.Progress()
    waits: list[float] = []

    def note(w: float) -> bool:
        waits.append(w)
        if progress.retrying:
            notes.append(progress.retrying)
        return False

    corpus.run(conn, clock, FakeNotifier(), data_dir, req(out_dir / "c.ecfcorpus", total=4,
               chunk=1), r, SECRET, progress, isolator=inline, sleep=note)  # fmt: skip
    assert progress.fetched == fetched and progress.reason == reason
    assert notes and notes[0] == f"reconnecting (attempt 1 of {corpus.TRIES - 1})"
    assert progress.retrying == ""
