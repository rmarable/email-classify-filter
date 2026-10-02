import base64
import json
import re
import shutil
import zlib
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import metrics as m
from ecf.eval.builder import COMMIT_LIMIT, build_all, build_bytes, message_id
from ecf.eval.cards import load_cards, parse_card
from ecf.eval.hygiene import scan_text
from ecf.eval.results import CaseResult, ResultFile, compare, compare_fields, differences

ROOT = Path(__file__).resolve().parent / "eval" / "synthetic"
CARD = """---
id: t-1
title: test
from: "A <a@vendor-a.example>"
subject: "Hello"
{extra}---
Body line
## html
<p>Body line</p>
"""


def card(extra: str = "") -> str:
    return CARD.format(extra=extra)


def _streams(pdf: bytes) -> list[bytes]:
    """The decoded content streams of a PDF (reportlab: ASCII85 then Flate)."""
    out: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = match.group(1).strip()
        if raw.endswith(b"~>"):
            raw = base64.a85decode(raw, adobe=True)
        try:
            out.append(zlib.decompress(raw))
        except zlib.error:
            out.append(raw)
    return out


def parsed(data: bytes) -> EmailMessage:
    msg = message_from_bytes(data, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    return msg


# ---- cards


def test_card_parsing() -> None:
    c = parse_card(card())
    assert c.id == "t-1" and c.body == "Body line\n" and c.html == "<p>Body line</p>\n"
    for bad in ("no header", "---\nid: x\n", card("colour: red\n")):
        with pytest.raises(InvalidInputError):
            parse_card(bad)


def test_duplicate_card_ids(tmp_path: Path) -> None:
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(card())
    with pytest.raises(InvalidInputError, match="duplicate"):
        load_cards(tmp_path)


# ---- builder


def test_build_is_deterministic_and_well_formed() -> None:
    c = parse_card(
        card(
            'bulk: true\nauth_results: ["mx.example.net; dmarc=pass"]\n'
            'hidden_text: "secret instruction"\n'
        )
    )
    a, b = build_bytes(c), build_bytes(c)
    assert a == b
    msg = parsed(a)
    assert msg["Message-ID"] == message_id(c) and msg["Message-ID"].endswith(
        "@synthetic.acme.example>"
    )
    assert msg["Authentication-Results"] == "mx.example.net; dmarc=pass"
    assert msg["List-Unsubscribe"] and "192.0.2.10" in msg["Received"]
    html = msg.get_body(("html",))
    assert html is not None and "secret instruction" in html.get_content()
    assert "display:none" in html.get_content()


def test_generated_pdf_is_plain() -> None:
    c = parse_card(card("attachments:\n  - {name: inv.pdf, generate: invoice_pdf}\n"))
    msg = parsed(build_bytes(c))
    pdf = next(p for p in msg.iter_attachments() if p.get_filename() == "inv.pdf")
    data = pdf.get_content()
    assert isinstance(data, bytes) and data.startswith(b"%PDF")
    for marker in (b"/JavaScript", b"/JS ", b"/AcroForm", b"/URI", b"/EmbeddedFile", b"/Launch"):
        assert marker not in data
    text = b"".join(_streams(data))
    assert b"SYNTHETIC TEST DOCUMENT" in text


# ---- hygiene


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("write to someone@gmail.com", "email address"),
        ("see https://www.realbank.com/login", "URL"),
        ("visit paypal.com", "domain"),
        ("visit ubs.ch or rabobank.nl", "domain"),
        ("paypal[.]com", "domain"),
        ("paypal (dot) com", "domain"),
        ("p\u0430ypal.com", "non-ASCII domain (homoglyph or IDN)"),
        ("bank.\u4e2d\u56fd", "non-ASCII domain (homoglyph or IDN)"),
        ("call 212-555-0199 or 415-867-5309", "phone number"),
        ("call 867-5309", "phone number"),
        ("call 4158675309", "phone number"),
        ("call +44 20 7946 0958", "phone number"),
        ("ssn 123-45-6789", "SSN-like number"),
        ("card 4111 1111 1111 1111", "card number (Luhn-valid)"),
        ("card 4111.1111.1111.1111", "card number (Luhn-valid)"),
        ("card 4111\u20131111\u20131111\u20131111", "card number (Luhn-valid)"),
        ("IBAN DE44500105175407324931", "IBAN (valid checksum)"),
        ("iban de44 5001 0517 5407 3249 31", "IBAN (valid checksum)"),
        ("refDE44500105175407324931", "IBAN (valid checksum)"),
        ("routing 021000021", "routing number (valid checksum)"),
        ("key AKIAABCDEFGHIJKLMNOP", "secret-like token"),
        ("sk_live_abcdefghij12", "secret-like token"),
        ("github_pat_" + "a" * 22, "secret-like token"),
        ("AIza" + "a" * 35, "secret-like token"),
        ("glpat-" + "a" * 20, "secret-like token"),
    ],
)
def test_hygiene_flags(text: str, kind: str) -> None:
    kinds = {f.kind for f in scan_text(text, "t")}
    assert kind in kinds, (text, kinds)


@pytest.mark.parametrize(
    "text",
    [
        "write to ap@acme.example",
        "see https://portal.vendor-a.test/x",
        "invoice.pdf attached",
        "call 555-0142",
        "call 212-555-0150",
        "published example IBAN GB82 WEST 1234 5698 7654 32",
        "routing 123456789",
        "card 4111 1111 1111 1112",
        "example.com is reserved",
        "e.g. and i.e. and U.S.",
        "INV-2044 due 2026-10-15",
        "version 1.2.3",
        "toner 89.50",
        "mx.example.net; dkim=pass header.d=vendor-a.example; "
        "dmarc=pass header.from=vendor-a.example",
    ],
)
def test_hygiene_allows(text: str) -> None:
    assert scan_text(text, "t") == [], text


def test_failed_hygiene_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "bad.md").write_text(
        card().replace("a@vendor-a.example", "a@realcorp.com")
    )
    report = build_all(tmp_path)
    assert report.findings and not (tmp_path / "eml").exists()


# ---- the committed set


def test_committed_set_is_current(tmp_path: Path) -> None:
    """Rebuild the committed cards and compare with the committed .eml files and labels."""
    shutil.copytree(ROOT / "cases", tmp_path / "cases")
    report = build_all(tmp_path)
    assert not report.findings
    labels = [json.loads(x) for x in (ROOT / "labels.jsonl").read_text().splitlines()]
    fresh = {
        x["id"]: x
        for x in (json.loads(y) for y in (tmp_path / "labels.jsonl").read_text().splitlines())
    }
    assert {x["id"] for x in labels} == set(fresh)
    committed = {p.stem for p in (ROOT / "eml").glob("*.eml")}
    small = {x["id"] for x in labels if x["bytes"] <= COMMIT_LIMIT}
    assert committed == small, f"orphaned or missing .eml files: {committed ^ small}"
    for entry in labels:
        assert entry["sha256"] == fresh[entry["id"]]["sha256"], entry["id"]
        if entry["bytes"] <= COMMIT_LIMIT:
            assert (ROOT / entry["file"]).read_bytes() == (tmp_path / entry["file"]).read_bytes()


# ---- metrics and results


def test_metrics_known_values() -> None:
    lo, hi = m.wilson(8, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.49, 0.943)
    assert m.wilson(0, 0) == (0.0, 1.0)
    assert m.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert m.mcnemar_exact(3, 3) == 1.0
    assert m.accuracy(["a", "b", "c"], ["a", "b", "x"]) == pytest.approx(2 / 3)
    assert m.macro_f1(["a", "a", "b"], ["a", "b", "b"], ["a", "b"]) == pytest.approx(2 / 3)
    assert m.confusion(["a", "b"], ["b", "b"], ["a", "b"]) == {
        "a": {"a": 0, "b": 1},
        "b": {"a": 0, "b": 1},
    }
    assert m.ordinal_mae(["low", "high"], ["medium", "high"], ["low", "medium", "high"]) == 0.5
    holm = m.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.005})
    assert holm == pytest.approx({"d": 0.02, "a": 0.03, "c": 0.06, "b": 0.06})
    assert m.holm({}) == {}


def _run(pair: str, correct: list[bool]) -> ResultFile:
    return ResultFile(
        run_id=pair,
        pair=pair,
        set_version="v1",
        created_at="t",
        cases=[CaseResult(id=str(i), correct=c) for i, c in enumerate(correct)],
    )


def test_compare() -> None:
    a = _run("A", [True] * 90 + [False] * 10)
    b = _run("B", [True] * 95 + [False] * 5)
    c = compare(a, b)
    assert (c.n, c.b_only, c.a_only) == (100, 5, 0)
    assert c.diff_points == pytest.approx(5.0) and c.b_non_inferior
    with pytest.raises(InvalidInputError):
        compare(a, ResultFile(run_id="x", pair="x", set_version="v2", created_at="t", cases=[]))


def test_differences_name_what_the_model_returned() -> None:
    expected = {"labels": {"category": "other", "fraud_risk": "none"}, "rule": "otherwise"}
    got = {"category": "partnership", "fraud_risk": "none", "rule": "requires_reply",
           "payment_related": False}  # fmt: skip
    assert differences(expected, got) == ["category partnership (expected other)",
                                          "rule requires_reply (expected otherwise)"]  # fmt: skip
    assert differences({"labels": {"payment_related": False}}, {"payment_related": True}) == [
        "payment_related true (expected false)"
    ]
    assert differences(expected, {}) == []  # an older result without values


def test_compare_fields_holm() -> None:
    def run(pair: str, cat: list[bool], rule: list[bool]) -> ResultFile:
        cases = [CaseResult(id=str(i), correct=c and r, fields={"category": c, "rule": r})
                 for i, (c, r) in enumerate(zip(cat, rule, strict=True))]  # fmt: skip
        return ResultFile(run_id=pair, pair=pair, set_version="v1", created_at="t", cases=cases)

    a = run("A", [False] * 8 + [True] * 12, [True] * 20)
    b = run("B", [True] * 20, [True] * 19 + [False])
    by = {f.field: f for f in compare_fields(a, b)}
    assert (by["category"].b_only, by["category"].a_only) == (8, 0)
    assert by["category"].p_value == pytest.approx(2 / 256)
    assert by["category"].p_holm == pytest.approx(4 / 256) and by["category"].significant
    assert (by["rule"].a_only, by["rule"].p_holm) == (1, 1.0) and not by["rule"].significant


# ---- CLI


def test_cli_new_case_show_compare(tmp_path: Path) -> None:
    r = CliRunner()
    assert (
        r.invoke(
            app, ["eval", "new-case", "bec-9", "--template", "bec", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        r.invoke(
            app, ["eval", "new-case", "bec-9", "--template", "bec", "--root", str(tmp_path)]
        ).exit_code
        != 0
    )  # exists
    assert (
        r.invoke(
            app, ["eval", "new-case", "x", "--template", "nope", "--root", str(tmp_path)]
        ).exit_code
        != 0
    )
    shown = r.invoke(app, ["eval", "show", "bec-9", "--root", str(tmp_path)])
    assert shown.exit_code == 0 and "Subject: Updated remittance details" in shown.output
    built = r.invoke(app, ["eval", "build", "--root", str(tmp_path)])
    assert built.exit_code == 0, built.output
    fa, fb = tmp_path / "a.json", tmp_path / "b.json"
    fa.write_text(_run("A", [True, False, True]).model_dump_json())
    fb.write_text(_run("B", [True, True, True]).model_dump_json())
    out = r.invoke(app, ["eval", "compare", str(fa), str(fb)])
    assert out.exit_code == 0 and "McNemar" in out.output
    assert "per field" not in out.output  # no per-field results in these files
