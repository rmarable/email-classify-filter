"""CPU-time bounds on the trigger hot path (pre-classifier plan step 3b, R3-P17).

Each test measures CPU time (`time.process_time`, so other xdist workers don't count) against an
in-test baseline: one search for a missing word over 10 MiB of plain text, the machine's speed
unit. CPU time and a baseline taken in the same test keep the bounds steady while other xdist
workers run, so these tests run in parallel like the rest. Bounds are about three times what the
Mac that measured them took (2026-10-10), and well under what the code took before the fix each
one guards (`uv run pytest -m perf` runs only these). Texts are built inside the tests, never at
collection.
"""

from __future__ import annotations

import re
import sqlite3
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ecf_server import excerpt
from ecf_server import triggers as tr
from ecf_server.htmltext import LINK_CAP, html_text
from ecf_server.mail import PartInfo
from ecf_server.message import parse, parse_partial
from ecf_server.skeleton import entity_text

pytestmark = pytest.mark.perf

MIB = 1024 * 1024
LINE = "Hello team, please find the attached quarterly report for review and let me know.\n"
_PROBE = re.compile(r"(?<!\w)qzxjv(?!\w)")


def _cpu[T](f: Callable[[], T]) -> tuple[float, T]:
    """CPU seconds `f` took, and what it returned."""
    began = time.process_time()
    out = f()
    return time.process_time() - began, out


def _best[T](f: Callable[[], T], bound: float, n: int = 3) -> tuple[float, T]:
    """CPU seconds of a repeatable `f`, re-timed up to `n` times while it misses `bound`, and what
    its last run returned. The baseline is a fastest-of-five, so a workload timed once could land
    on a slower core or a busier moment and fail a bound it meets (test_a_check_through_the_isolate
    did, 2026-10-14); a run within the bound isn't repeated, so passing costs one run."""
    took, out = _cpu(f)
    for _ in range(n - 1):
        if took < bound:
            break
        again, out = _cpu(f)
        took = min(took, again)
    return took, out


def _best_fresh[T](
    setup: Callable[[int], T], run: Callable[[T], object], bound: float, n: int = 3
) -> float:
    """`_best` for a workload that changes state: `setup(round)` builds fresh input untimed, then
    only `run` is timed, re-timed up to `n` times while it misses `bound`."""
    took = float("inf")
    for r in range(n):
        data = setup(r)
        took = min(took, _cpu(lambda d=data: run(d))[0])
        if took < bound:
            break
    return took


def _unit() -> float:
    """The baseline: the fastest of five searches for a missing word over 10 MiB (five: under
    `-n auto` the baseline and the workload can land on different core types; pre-gate G15)."""
    text = LINE * (10 * MIB // len(LINE))
    return min(_cpu(lambda: _PROBE.search(text))[0] for _ in range(5))


def _raw(body: str, content_type: str) -> bytes:
    head = ("From: a@vendor-a.example\r\nTo: ap@acme.example\r\nSubject: s\r\n"
            f"MIME-Version: 1.0\r\nContent-Type: {content_type}; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n")  # fmt: skip
    return head.encode() + body.encode()


def _body(kind: str) -> tuple[str, str]:
    if kind == "html":
        return "<p>" + "</p><p>".join([LINE] * (10 * MIB // (len(LINE) + 7))) + "</p>", "text/html"
    unit = {
        "plain": LINE,
        "one-keyword-repeated": "wire transfer ",
        "accented": "Ça coûte très cher, résumé naïve façade. ",
        "mark-dense": "páymènt détails ",
    }[kind]
    return unit * (10 * MIB // len(unit.encode())), "text/plain"


@pytest.mark.parametrize(
    ("kind", "units"),
    [("plain", 90), ("html", 90), ("one-keyword-repeated", 90), ("accented", 160),
     ("mark-dense", 130)],
)  # fmt: skip
def test_scan_of_a_10_mib_part(kind: str, units: int) -> None:
    """Step 2a: about 450 units with one search per pattern; 25-30 now for ASCII, about 55 for
    accented text and 40 for text dense with marks."""
    texts = tr.texts_of(parse(_raw(*_body(kind))))
    unit = _unit()
    took, hits = _best(lambda: tr.scan(texts), units * unit)
    assert took < units * unit
    assert hits["unscanned"] == []
    if kind == "one-keyword-repeated":
        assert "wire transfer" in hits["bank"]


def test_scan_of_a_20_part_partial_message_stops_at_its_limit() -> None:
    """R3-P11: a message over the size limit is read as 20 parts of 5 MiB here (100 MiB, about
    220 units to scan whole); the scan stops at SCAN_BYTES (about 70 units) and says so."""
    parts = [PartInfo(str(i + 1), "text/plain", None, None, "8bit", "utf-8", 5 * MIB)
             for i in range(20)]  # fmt: skip
    texts = {p.section: (f"part {p.section}\n" + LINE * (5 * MIB // len(LINE))).encode()
             for p in parts}  # fmt: skip
    parsed = parse_partial(b"From: a@vendor-a.example\r\n\r\n", parts, texts, size=100 * MIB)
    all_texts = tr.texts_of(parsed)
    unit = _unit()
    took, hits = _best(lambda: tr.scan(all_texts), 140 * unit)
    assert took < 140 * unit
    assert hits["unscanned"] == [tr.UNSCANNED_TEXT]


@pytest.mark.parametrize(
    "unit_text",
    [
        "DE12 3456 7890 1234 5678 90 ",
        "DE12 ",
        "GB82-WEST-1234-5698-7654-33 ",
        "NO12 ",  # the shortest IBAN country
        "DE12\n3456\n7890\n1234\n5678\n90\n",  # newlines: no candidate spans them
        "LC12 3456 7890 1234 . ",  # too short for its country
        "ZZ12 3456 7890 1234 5678 ",  # no such country
        "ZZ12 AB12 CD12 EF12 ",  # dense heads naming no country (every start is tried)
        "DE12 3456. ",  # dense heads of a real country, each too short
        "LC12 LC12 LC12 LC12 LC12 LC12 LC12 AAA.",  # just short of LC's 32: uncounted matches
        "DE12.",  # a real country's head every 5 bytes, no full match
    ],
    ids=[
        "groups",
        "heads",
        "hyphens",
        "short-country",
        "newlines",
        "short",
        "no-country",
        "no-country-dense",
        "short-dense",
        "uncounted-matches",
        "head-only",
    ],
)
def test_the_iban_check_on_a_crafted_10_mib_text(unit_text: str) -> None:
    """R3-P12: every candidate fails its checksum; 1.26 s per 10 MiB before the candidate cap
    (about 16 units), under 3 units now."""
    text = entity_text(unit_text * (10 * MIB // len(unit_text)))
    unit = _unit()
    took, valid = _best(lambda: tr.valid_iban(text), 10 * unit)
    assert took < 10 * unit
    assert not valid


@pytest.mark.parametrize(
    "unit_text",
    ["bc1qqqqqqqqqq ", "0x" + "a" * 39 + " ", "1" + "A" * 24 + " ", "TEST-WALLET-12 "],
    ids=["bech32-short", "eth-short", "legacy-short", "placeholder-short"],
)
def test_the_wallet_search_on_a_crafted_10_mib_text(unit_text: str) -> None:
    """R3-P16: one alternation instead of four searches; near misses of every shape."""
    text = unit_text * (10 * MIB // len(unit_text))
    unit = _unit()
    took, found = _best(lambda: tr.WALLET.search(text), 6 * unit)
    assert took < 6 * unit
    assert found is None


@pytest.mark.parametrize(
    "unit_text",
    ["1.1.1.1.", "1,111,111,111,111,111,111.1", "USDUSD ", "USD USD ", "$$$$", "1 1 1 ",
     "1 USDX ", "1 dollarsx "],
    ids=["dotted-digits", "long-group-run", "codes", "spaced-codes", "signs", "spaced-digits",
         "code-prefix", "word-suffix"],
)  # fmt: skip
def test_the_amount_search_on_a_crafted_10_mib_text(unit_text: str) -> None:
    """Pre-classifier plan step 6 (§4, perf-2): near misses of every amount form, none found.
    At most about 6.5 units on this Mac (2026-10-10, spaced digits: 0.53 s); one alternation
    of all four forms took 10 units there, and 4 on ordinary text against 1 now."""
    text = unit_text * (10 * MIB // len(unit_text))
    unit = _unit()
    took, found = _best(lambda: tr.amount(text), 20 * unit)
    assert took < 20 * unit  # at least 3x the worst measured (pre-gate review G15)
    assert not found


def test_redact_injection_on_a_crafted_paragraph() -> None:
    """R150: one 4,000-line paragraph with the phrase on its last line took about 90 s with the
    line scan (quadratic); the binary search keeps it to a fraction of a unit."""
    para = "\n".join(["ordinary words in a line of mail text"] * 3999 + ["note to the assistant"])
    unit = _unit()
    took, out = _best(lambda: tr.redact_injection(para), 10 * unit)
    assert took < 10 * unit
    assert out.endswith(tr.INJECTION_MARK) and "assistant" not in out


def test_redact_injection_on_a_10_mib_paragraph() -> None:
    """R180: a crafted 10 MiB paragraph took over a minute even with the binary search; with the
    head search it costs about 5 units."""
    line = "ordinary words in a line of mail text"
    para = "\n".join([line] * 270_000 + ["note to the assistant"])
    unit = _unit()
    took, out = _best(lambda: tr.redact_injection(para), 20 * unit)
    assert took < 20 * unit
    assert out.endswith(tr.INJECTION_MARK) and "assistant" not in out
    assert tr.scan([para])["injection"]  # trigger 10 still sees the whole text


@pytest.mark.parametrize(("injected", "units"), [("none", 15), ("one", 60), ("all", 20)])
def test_redact_injection_on_10_mib_of_tiny_paragraphs(injected: str, units: int) -> None:
    """R3-P20: 10 MiB of two-letter paragraphs took about 160 units tested one by one; the
    whole text first, then batches, keep it to a few (about 18 with one phrase in the middle of
    20 MiB, 4 when every paragraph holds one)."""
    tiny = "hi\n\n" * (10 * MIB // 4)
    text = {"none": tiny, "one": tiny + "note to the assistant\n\n" + tiny,
            "all": "note to the assistant\n\n" * (10 * MIB // 23)}[injected]  # fmt: skip
    unit = _unit()
    took, out = _best(lambda: tr.redact_injection(text), units * unit)
    assert took < units * unit
    assert (tr.INJECTION_MARK in out) is (injected != "none")


def _repeat(unit_text: str) -> str:
    return unit_text * (10 * MIB // len(unit_text))


@pytest.mark.parametrize(
    "shape",
    ["anchors", "nested-unclosed-anchors", "one-anchor-of-10-mib", "a-10-mib-href", "paragraphs"],
)
def test_link_recording_on_hostile_html(shape: str) -> None:
    """Pre-classifier plan step 5 (P22): the anchor recorder stays linear. About 8-10 units for
    each shape on this Mac (2026-10-10), the same as ordinary paragraphs with no anchors."""
    html = {
        "anchors": lambda: _repeat('<a href="https://evil.test/x">www.vendor-a.example</a>'),
        "nested-unclosed-anchors": lambda: _repeat('<a href="https://evil.test/">acme.com '),
        "one-anchor-of-10-mib": lambda: '<a href="https://x.test/">' + _repeat("acme.com "),
        "a-10-mib-href": lambda: '<a href="https://' + "a" * (10 * MIB) + '.test/">acme.com</a>',
        "paragraphs": lambda: _repeat("<p>" + LINE + "</p>"),
    }[shape]()
    unit = _unit()
    took, out = _best(lambda: html_text(html), 30 * unit)
    assert took < 30 * unit
    assert out.links.number <= LINK_CAP


def test_the_scan_holds_little_more_than_its_limit_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-gate review G7: the limit counts bytes, so non-BMP text can't make it 4x larger, and
    one text's folds are held at a time. 6x the limit measured on this Mac (2026-10-10; 24x
    when the limit counted characters)."""
    monkeypatch.setattr(tr, "SCAN_BYTES", 4 * MIB)
    text = "words \U0001f600 " * (8 * MIB // 9)
    tr.scan(["warm"])
    tracemalloc.start()
    try:
        hits = tr.scan([text])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert hits["unscanned"] == [tr.UNSCANNED_TEXT]
    assert peak < 10 * 4 * MIB


@pytest.mark.parametrize(
    "shape",
    ["on-lines", "from-lines", "quoted-lines", "one-line", "paragraphs", "spaces"],
)
def test_excerpt_preparation_on_hostile_text(shape: str) -> None:
    """Pre-classifier plan step 10 (P22): preparation reads only `HEAD_CHARS`, each marker regex
    one bounded line. Under 1 unit for every shape on this Mac (2026-10-11), on 10 MiB inputs."""
    text = {
        "on-lines": lambda: _repeat("On x\n"),  # each line tried as a wrapped attribution
        "from-lines": lambda: _repeat("From: a\n"),  # each looks ahead for Sent:
        "quoted-lines": lambda: _repeat("> q\n"),
        "one-line": lambda: "x" * (10 * MIB),
        "paragraphs": lambda: _repeat("unsubscrib\n\n"),  # a near-miss footer per paragraph
        "spaces": lambda: _repeat(" \t"),
    }[shape]()
    unit = _unit()
    took, out = _best(lambda: excerpt.prepare([(text, "")]), 3 * unit)
    assert took < 3 * unit
    assert len(out) <= excerpt.HEAD_CHARS + len(excerpt.MOVED_MARK) + 4


@pytest.mark.parametrize("shape", ["many-quotes", "nested-unclosed-quotes", "one-big-quote",
                                   "half-quoted"])  # fmt: skip
def test_quote_containers_on_hostile_html(shape: str) -> None:
    """Pre-classifier plan step 10 (P22): quote containers are a depth counter in the walker and
    only span positions are kept; the split reads `HEAD_CHARS` of each side (pre-gate review G4,
    G11: one 10 MiB container, and one after 5 MiB of text). About 20 units on this Mac for the
    first two (2026-10-11), against 16-17 before step 10 and 10 for ordinary paragraphs."""
    para = "<p>" + LINE + "</p>"
    html = {
        "many-quotes": lambda: _repeat("<blockquote>x</blockquote>"),
        "nested-unclosed-quotes": lambda: _repeat("<blockquote>"),
        "one-big-quote": lambda: "<p>New</p><blockquote>" + _repeat(para) + "</blockquote>",
        "half-quoted": lambda: (para * (5 * MIB // len(para)) + "<blockquote>"
                                + para * (5 * MIB // len(para)) + "</blockquote>"),
    }[shape]()  # fmt: skip
    unit = _unit()
    took, out = _best(lambda: html_text(html, excerpt.HEAD_CHARS), 60 * unit)
    assert took < 60 * unit
    assert out.unquoted is not None
    assert len(out.unquoted) <= excerpt.HEAD_CHARS and len(out.quoted) <= excerpt.HEAD_CHARS


# The history work's own costs (v3.0.0 step 5, OD-507 §5, P19): CPU-time bounds for storing a
# classification with its audit row and for backfill pages on the fake mail source.
# Bounds about three times what this Mac took on the branch (2026-10-14), each against the same
# unit; main at 1daf644 took about the same per unit (the plan's before/after report). Smaller
# than the measured sizes (1,000 stores, 400k audit rows) to keep the suite fast.


def _db(tmp_path: Path, clock: Any) -> sqlite3.Connection:
    from ecf_server import db  # noqa: PLC0415

    conn = db.connect(tmp_path / "ecf.db")
    db.migrate(conn)
    # The bounds measure ecf's code, not the disk: with the service's synchronous=FULL each commit's
    # fsync counts in process_time as system time, which on the CI runner made the store test about
    # three times its Mac figure on every run (2026-10-15), whatever the re-timing.
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset, created_at)"
                 " VALUES ('ap', 'ap@acme.example', 'standard', 'live', 'C', 'now')")  # fmt: skip
    conn.execute("INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'h', 'now')")
    del clock
    return conn


def _note(i: int) -> bytes:
    return (f"From: Pat {i} <pat{i}@friends.example>\r\nTo: ap@acme.example\r\nSubject: Note {i}"
            f"\r\nMessage-ID: <n{i}@friends.example>\r\n\r\n" + f"Lunch on Friday? {i}\r\n" * 20
            ).encode()  # fmt: skip


def test_classifier_store_with_its_audit_row_per_item(tmp_path: Path) -> None:
    """`classifier.store` (the reprocessing key and `item.classified`) and the policy after it:
    about 0.013 units per item on main and on this branch."""
    from ecf.ids import AddressId, StableId  # noqa: PLC0415
    from ecf.status import Status  # noqa: PLC0415
    from ecf_server import classifier, items  # noqa: PLC0415
    from ecf_server.clock import FakeClock  # noqa: PLC0415
    from ecf_server.state_machine import TransitionContext  # noqa: PLC0415
    from tests.helpers import GOOD  # noqa: PLC0415

    clock = FakeClock()
    conn = _db(tmp_path, clock)
    n = 200

    def fresh(r: int) -> list[str]:  # 200 new items each round, so every round does the work
        sids: list[str] = []
        for i in range(n):
            sid = f"{r}{i:05d}".ljust(64, "b")
            items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                              content_hash="h", facts="{}")  # fmt: skip
            items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE,
                             TransitionContext(), actor="t")  # fmt: skip
            sids.append(sid)
        return sids

    def store(sids: list[str]) -> None:
        for s in sids:
            classifier.store(conn, clock, s, GOOD, {"classifier": "x"},
                             expected=Status.AWAITING_CLAUDE)  # fmt: skip

    unit = _unit()
    took = _best_fresh(fresh, store, 0.04 * unit * n)
    assert took / n < 0.04 * unit


def test_a_30_message_page_and_backfill_pages_on_the_fake(tmp_path: Path) -> None:
    """`fetch_page` (with the source's phase timings) about 0.27 units for 30 messages; a
    backfill page over 1,000 old messages (its range bounded once) about 0.27."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from ecf_server import fetch, leases  # noqa: PLC0415
    from ecf_server.clock import FakeClock  # noqa: PLC0415
    from ecf_server.mail.fake import FakeMailSource  # noqa: PLC0415

    clock = FakeClock()
    conn = _db(tmp_path, clock)
    src = FakeMailSource()
    old = datetime.now(UTC) - timedelta(days=10)
    for i in range(1000):
        src.deliver(_note(i), old + timedelta(minutes=i))
    lease = leases.acquire(conn, clock, "ap", "perf")
    assert lease is not None
    cfg = fetch.address_config(conn, "ap")
    fetch.fetch_page(conn, clock, src, cfg, lease)  # the first run: position only
    cur = fetch.load_cursor(conn, "ap")
    assert cur is not None and cur.uidvalidity is not None
    bf = fetch.Backfill((old - timedelta(days=1)).date().isoformat(), False, cur.uidvalidity,
                        cur.last_uid, run_id="perf")  # fmt: skip
    created: list[int] = []

    def thirty(r: int) -> None:  # 30 new messages each round
        for i in range(30):
            src.deliver(_note(5000 + 100 * r + i))

    def page_of(_: None) -> None:
        created.append(len(fetch.fetch_page(conn, clock, src, cfg, lease).created))

    unit = _unit()
    took = _best_fresh(thirty, page_of, 0.7 * unit)
    assert created and all(c == 30 for c in created) and took < 0.7 * unit
    took, _ = _cpu(lambda: [fetch.backfill_page(conn, clock, src, cfg, lease, bf)
                            for _ in range(3)])  # fmt: skip
    assert took / 3 < 0.7 * unit


def test_a_check_through_the_isolate(tmp_path: Path) -> None:
    """A check of 5 new messages, each in a child process (this process's CPU only), with its
    timings on `check.completed`: about 0.15 units."""
    from ecf_server import checks, db  # noqa: PLC0415
    from ecf_server.clock import FakeClock  # noqa: PLC0415
    from ecf_server.mail import MailSource  # noqa: PLC0415
    from ecf_server.mail.fake import FakeMailSource  # noqa: PLC0415
    from ecf_server.secretstore.memory import MemorySecretStore  # noqa: PLC0415

    clock = FakeClock()
    conn = _db(tmp_path, clock)
    src = FakeMailSource()
    store = MemorySecretStore()
    store.set("mailbox/ap", "pw")

    def factory(_h: str, _u: str, pw: Callable[[], str]) -> MailSource:
        pw()
        return src

    def check() -> checks.CheckReport:
        return checks.run_check(conn, clock, address_id="ap", install="t", secrets=store,
                                factory=factory,
                                connect=lambda: db.connect(tmp_path / "ecf.db"))  # fmt: skip

    check()
    rounds = iter(range(3))

    def five_new() -> checks.CheckReport:
        r = next(rounds)
        for i in range(5):
            src.deliver(_note(100 * r + i))
        return check()

    unit = _unit()
    # five new messages each round, so every round does the work
    took, report = _best(five_new, 0.5 * unit)
    assert report.created == 5 and report.timings["auth"]["messages"] == 5
    assert took < 0.5 * unit


def test_stats_stages_on_many_transitions(tmp_path: Path) -> None:
    """`ecf stats --stages` reads `item.transitioned` rows by (event, ts) (migration 0039): 400k
    transitions took about 1.8 s of CPU (21 units) on this branch; 100k here (about 5), bound 15."""
    import json  # noqa: PLC0415
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from ecf_server import db, timings  # noqa: PLC0415
    from ecf_server.clock import FakeClock, to_ts  # noqa: PLC0415

    conn = _db(tmp_path, FakeClock())
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    path = ["new", "awaiting_claude", "classified", "proposed", "executing"]
    rows: list[tuple[str, str, str, str]] = []
    for i in range(25_000):
        sid = f"{i:07d}".ljust(64, "c")
        at = t0 + timedelta(seconds=i)
        rows.append((to_ts(at), sid, "item.created", "{}"))
        for k in range(4):
            at += timedelta(seconds=30 * (k + 1))
            rows.append((to_ts(at), sid, "item.transitioned",
                         json.dumps({"from": path[k], "to": path[k + 1]})))  # fmt: skip
    with db.write_tx(conn):
        conn.executemany("INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome,"
                         " data) VALUES (?, 'ap', ?, ?, 'service', 'ok', ?)", rows)  # fmt: skip
    unit = _unit()
    took, r = _best(lambda: timings.stages(conn, t0), 15 * unit)
    assert r["new_mail"]["items"] == 25_000 and took < 15 * unit
