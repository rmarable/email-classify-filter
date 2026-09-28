import json
import shutil
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
from ecf.eval.results import CaseResult, ResultFile, compare

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
    assert b"SYNTHETIC TEST DOCUMENT" in data or b"/Filter" in data


# ---- hygiene


@pytest.mark.parametrize(
    "text",
    [
        "write to someone@gmail.com",
        "see https://www.realbank.com/login",
        "visit paypal.com",
        "call 212-555-0199 or 415-867-5309",
        "card 4111 1111 1111 1111",
        "IBAN DE44500105175407324931",
        "routing 021000021",
        "key AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_hygiene_flags(text: str) -> None:
    assert scan_text(text, "t"), text


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
