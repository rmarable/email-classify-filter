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
from collections.abc import Callable

import pytest

from ecf_server import triggers as tr
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
    """The baseline: the fastest of three searches for a missing word over 10 MiB."""
    text = LINE * (10 * MIB // len(LINE))
    return min(_cpu(lambda: _PROBE.search(text))[0] for _ in range(3))


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
    220 units to scan whole); the scan stops at SCAN_CHARS (about 70 units) and says so."""
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
