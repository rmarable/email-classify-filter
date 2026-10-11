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
import time
import tracemalloc
from collections.abc import Callable

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
    took, hits = _cpu(lambda: tr.scan(texts))
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
    took, hits = _cpu(lambda: tr.scan(all_texts))
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
    ],
    ids=["groups", "heads", "hyphens", "short-country", "newlines", "short", "no-country"],
)
def test_the_iban_check_on_a_crafted_10_mib_text(unit_text: str) -> None:
    """R3-P12: every candidate fails its checksum; 1.26 s per 10 MiB before the candidate cap
    (about 16 units), under 3 units now."""
    text = entity_text(unit_text * (10 * MIB // len(unit_text)))
    unit = _unit()
    took, valid = _cpu(lambda: tr.valid_iban(text))
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
    took, found = _cpu(lambda: tr.WALLET.search(text))
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
    took, found = _cpu(lambda: tr.amount(text))
    assert took < 20 * unit  # at least 3x the worst measured (pre-gate review G15)
    assert not found


def test_redact_injection_on_a_crafted_paragraph() -> None:
    """R150: one 4,000-line paragraph with the phrase on its last line took about 90 s with the
    line scan (quadratic); the binary search keeps it to a fraction of a unit."""
    para = "\n".join(["ordinary words in a line of mail text"] * 3999 + ["note to the assistant"])
    unit = _unit()
    took, out = _cpu(lambda: tr.redact_injection(para))
    assert took < 10 * unit
    assert out.endswith(tr.INJECTION_MARK) and "assistant" not in out


def test_redact_injection_on_a_10_mib_paragraph() -> None:
    """R180: a crafted 10 MiB paragraph took over a minute even with the binary search; with the
    head search it costs about 5 units."""
    line = "ordinary words in a line of mail text"
    para = "\n".join([line] * 270_000 + ["note to the assistant"])
    unit = _unit()
    took, out = _cpu(lambda: tr.redact_injection(para))
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
    took, out = _cpu(lambda: tr.redact_injection(text))
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
    took, out = _cpu(lambda: html_text(html))
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
    took, out = _cpu(lambda: excerpt.prepare([(text, "")]))
    assert took < 3 * unit
    assert len(out) <= excerpt.HEAD_CHARS + len(excerpt.MOVED_MARK) + 4


@pytest.mark.parametrize("shape", ["many-quotes", "nested-unclosed-quotes"])
def test_quote_containers_on_hostile_html(shape: str) -> None:
    """Pre-classifier plan step 10 (P22): quote containers are a depth counter in the walker and
    only span positions are kept. About 20 units on this Mac (2026-10-11), against 16-17 before
    step 10 and 10 for ordinary paragraphs."""
    html = {
        "many-quotes": lambda: _repeat("<blockquote>x</blockquote>"),
        "nested-unclosed-quotes": lambda: _repeat("<blockquote>"),
    }[shape]()
    unit = _unit()
    took, out = _cpu(lambda: html_text(html))
    assert took < 60 * unit
    assert out.unquoted is not None
