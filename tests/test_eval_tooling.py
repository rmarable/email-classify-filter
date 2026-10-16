import base64
import json
import re
import shutil
import zlib
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import metrics as m
from ecf.eval.builder import COMMIT_LIMIT, build_all, build_bytes, message_id
from ecf.eval.cards import load_cards, parse_card
from ecf.eval.hygiene import scan_text
from ecf.eval.results import (
    CallMetrics,
    CaseResult,
    ClaimRecord,
    ResultFile,
    call_figures,
    compare,
    compare_fields,
    differences,
    field_lines,
    field_metrics,
    input_warnings,
    label_map,
    schema_warning,
    scoring,
    spawn_figures,
)
from ecf.schema import load_schema, load_schema_v2

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


# Cards: parsing, profiles, templates, label maps and duplicate IDs.


def test_card_parsing() -> None:
    c = parse_card(card())
    assert c.id == "t-1" and c.body == "Body line\n" and c.html == "<p>Body line</p>\n"
    for bad in ("no header", "---\nid: x\n", card("colour: red\n")):
        with pytest.raises(InvalidInputError):
            parse_card(bad)


def test_profile_sets_the_default_recipient_and_internal_facts_are_refused() -> None:
    """OD-443: a card goes to its profile's address; the internal set's facts are computed."""
    assert parse_card(card()).to == ["ap@acme.example"]
    free = parse_card(card("profile: freemail\n"))
    assert free.profile == "freemail" and free.to == ["pat-lee@freemail.example"]
    assert parse_card(card("profile: freemail\nto: [x@vendor-a.example]\n")).to == [
        "x@vendor-a.example"
    ]
    for fact in ("sender_origin", "from_org_address", "impersonates_internal"):
        with pytest.raises(InvalidInputError, match="come from the profile"):
            parse_card(card(f"expected:\n  facts: {{{fact}: true}}\n"))
    for fact in ("keywords", "triggers", "routes"):  # computed (pre-gate review G8)
        with pytest.raises(InvalidInputError, match="computed from the message"):
            parse_card(card(f"expected:\n  facts: {{{fact}: {{}}}}\n"))
    with pytest.raises(InvalidInputError, match="profile"):
        parse_card(card("profile: corporate\n"))


def test_freemail_template_parses_builds_and_passes_hygiene(tmp_path: Path) -> None:
    """`ecf eval new-case --template freemail`: goes to the profile address, builds clean."""
    r = CliRunner()
    made = r.invoke(app, ["eval", "new-case", "free-9", "--template", "freemail",
                          "--root", str(tmp_path)])  # fmt: skip
    assert made.exit_code == 0, made.output
    c = parse_card((tmp_path / "cases" / "free-9.md").read_text("utf-8"))
    assert (c.profile, c.to) == ("freemail", ["pat-lee@freemail.example"])
    assert c.expected.rule == "fraud_guard"
    built = r.invoke(app, ["eval", "build", "--root", str(tmp_path)])
    assert built.exit_code == 0, built.output
    row = json.loads((tmp_path / "labels.jsonl").read_text("utf-8").splitlines()[0])
    assert row["profile"] == "freemail"
    assert b"To: pat-lee@freemail.example" in (tmp_path / "eml" / "free-9.eml").read_bytes()


def test_labels_carry_a_profile_only_when_not_org(tmp_path: Path) -> None:
    """So the committed set's version (a hash of labels.jsonl) is unchanged by OD-443."""
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "a.md").write_text(card())
    (tmp_path / "cases" / "b.md").write_text(card("profile: freemail\n").replace("t-1", "t-2"))
    assert not build_all(tmp_path).findings
    rows = [json.loads(x) for x in (tmp_path / "labels.jsonl").read_text().splitlines()]
    assert [r.get("profile") for r in rows] == [None, "freemail"]


def test_duplicate_card_ids(tmp_path: Path) -> None:
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(card())
    with pytest.raises(InvalidInputError, match="duplicate"):
        load_cards(tmp_path)


# The builder: a build is deterministic and well formed, and its PDFs are plain.


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


# The hygiene scan: what it flags as real-looking data, what it allows, and that a failed scan
# writes nothing.


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


# Metrics and results: known metric values, and what `compare` warns about when two runs differ.


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
    assert c.diff_points == pytest.approx(5.0) and c.b_non_inferior()
    lo, hi = c.diff_ci  # Newcombe's paired interval (OD-486)
    assert 0 < lo < 5 < hi
    worse = compare(b, a)  # 5 points worse: inferior at the -5 margin, whatever the interval
    assert not worse.b_non_inferior() and not worse.b_non_inferior(margin=3)
    assert worse.b_non_inferior(margin=12)
    with pytest.raises(InvalidInputError):
        compare(a, ResultFile(run_id="x", pair="x", set_version="v2", created_at="t", cases=[]))


def test_compare_warns_when_the_schemas_differ() -> None:
    a, b = _run("A", [True]), _run("B", [True])
    assert schema_warning(a, b) is None  # neither recorded one: both schema v2 (pre-v3)
    shipped = b.model_copy(update={"summary": {"schema_digest": load_schema_v2().digest}})
    assert schema_warning(a, shipped) is None
    v3 = b.model_copy(update={"summary": {"schema_digest": load_schema().digest}})
    assert schema_warning(shipped, v3) is not None  # a v2 run against a v3 one (OD-498)
    other = b.model_copy(update={"summary": {"schema_digest": "schema-0123456789abcdef"}})
    warning = schema_warning(shipped, other)
    assert warning is not None and "schema-0123456789abcdef" in warning
    assert schema_warning(a, other) is not None


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


def test_compare_warns_when_the_runs_were_asked_differently() -> None:
    a, b = _run("A", [True]), _run("B", [True])
    assert input_warnings(a, b) == []
    pa = a.model_copy(update={"summary": {"prompt_digest": "a" * 64, "excerpt_cap": 3000}})
    pb = b.model_copy(update={"summary": {"prompt_digest": "b" * 64, "excerpt_cap": 1500}})
    warned = input_warnings(pa, pb)
    assert [w.split(" (")[0] for w in warned] == ["warning: different prompt",
                                                  "warning: different inputs"]  # fmt: skip
    assert input_warnings(pa, b) == []  # a run that recorded none can't be told apart
    ea = a.model_copy(update={"summary": {"excerpt_prep": 1}})
    eb = b.model_copy(update={"summary": {"excerpt_prep": 0}})  # --no-excerpt-prep (step 10)
    assert input_warnings(ea, eb) == ["warning: different inputs (excerpt_prep 1 vs 0)"]
    ra = a.model_copy(update={"summary": {"read_bytes": 12843}})  # S4 review R9: user fields
    rb = b.model_copy(update={"summary": {"read_bytes": 7663}})
    assert input_warnings(ra, rb) == ["warning: different inputs (read_bytes 12843 vs 7663)"]
    aa = a.model_copy(update={"summary": {"actor_prompt_digest": "a" * 64}})  # OD-523
    ab = b.model_copy(update={"summary": {"actor_prompt_digest": "b" * 64}})
    assert input_warnings(aa, ab) == [
        f"warning: different actor instructions (actor_prompt_digest {'a' * 16} vs {'b' * 16})"
    ]
    assert input_warnings(aa, b) == []  # a result from before v3.0.1 recorded none
    sa = a.model_copy(update={"summary": {"options": {"switches_off": ["message_signals"]}}})
    assert input_warnings(sa, b) == [  # pre-gate review G3: the prompt digest doesn't show it
        "warning: different switches (message_signals vs all on)"]  # fmt: skip


def test_compare_warns_when_two_claude_runs_were_asked_differently() -> None:
    """Timing plan item 5 (P16): preset, sensitivity and models warn; batch never does."""
    claude = {
        "preset": "C",
        "sensitivity": "high",
        "batch": 1,
        "models": {"classifier_high": "claude-sonnet-5-5", "actor_high": "claude-opus-5-5"},
    }
    a, b = _run("A", [True]), _run("B", [True])
    ca = a.model_copy(update={"summary": {"claude": claude}})
    assert input_warnings(ca, ca.model_copy(update={"summary": {"claude": claude | {"batch": 5}}})
                          ) == []  # fmt: skip
    cb = b.model_copy(
        update={
            "summary": {
                "claude": claude
                | {
                    "preset": "B",
                    "sensitivity": "standard",
                    "models": {
                        "classifier_high": "claude-sonnet-5-5",
                        "actor_high": "claude-sonnet-5-5",
                    },
                }
            }
        }
    )
    assert input_warnings(ca, cb) == [
        "warning: different preset (C vs B)",
        "warning: different sensitivity (high vs standard)",
        "warning: different models (actor_high claude-opus-5-5 vs claude-sonnet-5-5)",
    ]  # fmt: skip
    assert input_warnings(ca, b) == []  # only one side is a Claude eval with figures


def _claim(stage: str, spawn: str, rnd: int, session: int, times: list[float | None],
           **kw: Any) -> ClaimRecord:  # fmt: skip
    claimed, read, answered, settled = times
    return ClaimRecord.model_validate({
        "stage": stage, "spawn": spawn, "round": rnd, "session": session,
        "preparing": kw.pop("preparing", False), "claimed_s": claimed, "read_s": read,
        "answered_s": answered, "settled_s": settled, **kw})  # fmt: skip


def test_spawn_figures_from_the_claims() -> None:
    """Timing plan item 4: per agent, round, session and run, from `cases[].claims` alone."""
    cases = [
        CaseResult(id="a", correct=True, claims=[
            _claim("classify", "ecf-classifier-high-1", 1, 1, [10, 12, 20, 20.5], preparing=True),
            _claim("act", "ecf-actor-high-1", 2, 1, [40, 41, 50, 50.1],
                   high_risk=["first_time_sender"])]),
        CaseResult(id="b", correct=True, claims=[
            _claim("classify", "ecf-classifier-high-1", 1, 1, [10, 21, 30, 30.2], preparing=True),
            _claim("act", "ecf-actor", 2, 1, [40, 42, 46, 46.1])]),
        CaseResult(id="c", correct=False, claims=[  # a second session's claim that expired
            _claim("classify", "ecf-classifier-high-1", 3, 2, [100, 105, None, None],
                   outcome="claim_expired")]),
    ]  # fmt: skip
    f = cast(dict[str, Any], spawn_figures(cases))
    high = f["agents"]["ecf-classifier-high"]
    assert (high["claims"], high["spawns"]) == (3, 2)
    assert high["items_per_spawn"] == {"p50": 1, "max": 2}
    assert high["read_to_answer_s"] == {"p50": 8.0, "p95": 9.0, "max": 9.0}
    assert high["claim_to_read_s"] == {"p50": 5.0, "p95": 11.0, "max": 11.0}
    assert high["spawn_s"] == {"p50": 18.0, "p95": 18.0, "max": 18.0}  # 12 -> 30; c unanswered
    assert high["settle_lag_s"]["p95"] == 0.5
    assert set(f["agents"]) == {"ecf-actor", "ecf-actor-high", "ecf-classifier-high"}
    assert f["rounds"]["preparing"] == {"rounds": 1, "duration_s": {"p50": 20.0, "p95": 20.0,
                                        "max": 20.0}, "gap_s": {"p50": 10.0, "p95": 10.0,
                                        "max": 10.0}}  # fmt: skip
    assert f["rounds"]["ready"]["rounds"] == 2  # round 3 counts, though nothing was answered
    assert f["rounds"]["ready"]["gap_s"]["p50"] is None  # no next round in either session
    assert f["sessions"] == {
        "1": {"rounds": 2, "in_rounds_s": 30.0, "between_rounds_s": 10.0},
        "2": {"rounds": 1, "in_rounds_s": 0.0, "between_rounds_s": 0.0},
    }
    assert f["run_s"] == 40.0
    assert f["largest_gap"] == {"s": 49.9, "at_s": 50.1}
    assert f["act"] == {"claims": 2, "high_risk_actor": 1, "reasons": {"first_time_sender": 1}}
    assert spawn_figures([CaseResult(id="x", correct=True)]) is None  # a local run


def test_compare_warns_on_different_keywords_rules_or_scoring() -> None:
    """Pre-classifier plan step 7 (M8): a policy rescore beside a live run, or runs whose
    keywords or starter rules differ, are named before the figures."""
    a, b = _run("A", [True]), _run("B", [True])
    ka = a.model_copy(update={"summary": {"keywords_digest": "k1", "rules_digest": "r1",
                                          "scoring": "live"}})  # fmt: skip
    kb = b.model_copy(update={"summary": {"keywords_digest": "k2", "rules_digest": "r2",
                                          "scoring": "policy", "rescored_from": {}}})  # fmt: skip
    assert input_warnings(ka, kb) == [
        "warning: different scoring (live vs policy)",
        "warning: different keywords (keywords_digest k1 vs k2)",
        "warning: different rules (rules_digest r1 vs r2)",
    ]
    old = b.model_copy(update={"summary": {"rescored_from": {}}})  # an older rescore
    assert scoring(old) == "recorded" and scoring(a) == "live"
    assert input_warnings(a, old) == ["warning: different scoring (live vs recorded)"]
    moved = kb.model_copy(update={"summary": {"scoring": "policy",
                                              "rescored_from": {"set_changed": True}}})  # fmt: skip
    assert input_warnings(kb, moved)[-1].startswith("warning: a result was planned again on a set")


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


# The CLI and field metrics: new-case, show and compare, subsets, per-field metrics, and
# card-level injection targets.


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
    assert "different schemas" not in out.output
    other = _run("B", [True, True, True]).model_copy(
        update={"summary": {"schema_digest": "schema-0123456789abcdef"}}
    )
    fb.write_text(other.model_dump_json())
    out = r.invoke(app, ["eval", "compare", str(fa), str(fb)])
    assert out.exit_code == 0 and "warning: the runs used different schemas" in out.output


def test_call_figures_leave_out_cold_starts() -> None:
    """Pre-classifier plan §11 (perf-3): per role, cold starts excluded; the short-cache split."""

    def m(prompt: int, cached: int, load_ns: int = 1) -> CallMetrics:
        return CallMetrics(prompt_tokens=prompt, cached_tokens=cached, output_tokens=70,
                           prompt_ns=500_000_000, eval_ns=4_000_000_000,
                           load_ns=load_ns)  # fmt: skip

    cold = m(9000, 0, load_ns=5_000_000_000)
    cases = [CaseResult(id="1", correct=True, classifier=m(1300, 1200)),
             CaseResult(id="2", correct=True, classifier=m(1300, 1200), actor=m(900, 100)),
             CaseResult(id="3", correct=True, classifier=m(1300, 300)),
             CaseResult(id="4", correct=True, classifier=cold)]  # fmt: skip
    f = call_figures(cases)
    cls = cast(dict[str, Any], f["classifier"])
    assert cls["calls"] == 3  # the cold start is left out
    assert cls["uncached_tokens"] == {"p50": 100, "p95": 1000, "max": 1000}  # nearest rank
    assert cls["generation_s"] == {"p50": 4.0, "p95": 4.0, "max": 4.0}
    assert f["cache_short"] == [
        {"after": "an actor call", "calls": 1, "short": 1, "prompt_s": 0.5},
        {"after": "other", "calls": 2, "short": 0, "prompt_s": None},
    ]
    assert call_figures([]) == {"cache_short": []}


def test_compare_on_a_subset_of_two_different_sets() -> None:
    a = _run("A", [True, False, True, False])
    b = _run("B", [True, True, True, True, False]).model_copy(update={"set_version": "v2"})
    with pytest.raises(InvalidInputError, match="--subset"):
        compare(a, b)
    c = compare(a, b, frozenset({"0", "1", "2"}))
    assert (c.n, c.b_only, c.a_only) == (3, 1, 0)


def test_compare_refuses_mixed_label_maps_with_or_without_a_subset(tmp_path: Path) -> None:
    """S3b (P10): a result scored under label map 2 (removed values unlabelled, `correct`
    redefined) isn't compared with one from before it (no key: map 1), on the same set or on a
    subset of two; per-field comparison too; the CLI says to rescore."""
    a = _run("A", [True, False, True])
    b = _run("B", [True, True, True]).model_copy(update={"summary": {"label_map": 2}})
    for subset in (None, frozenset({"0", "1"})):
        with pytest.raises(InvalidInputError, match="different label maps \\(1 vs 2\\)"):
            compare(a, b, subset)
    with pytest.raises(InvalidInputError, match="different label maps"):
        compare_fields(a, b)
    both = a.model_copy(update={"summary": {"label_map": 2}})
    assert compare(both, b).n == 3 and label_map(a) == 1 and label_map(both) == 2
    fa, fb, ids = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "ids.txt"
    fa.write_text(a.model_dump_json())
    fb.write_text(b.model_dump_json())
    ids.write_text("0\n1\n")
    for extra in ([], ["--subset", str(ids)]):
        out = CliRunner().invoke(app, ["eval", "compare", str(fa), str(fb), *extra])
        assert out.exit_code != 0 and "different label maps" in str(out.exception), extra


def _labelled(
    cases: list[tuple[dict[str, str | bool], dict[str, str | bool | None]]],
) -> ResultFile:
    return ResultFile(run_id="r", pair="claude/claude", set_version="v1", created_at="t",
                      cases=[CaseResult(id=str(i), correct=True, expected=e, got=g)
                             for i, (e, g) in enumerate(cases)])  # fmt: skip


def test_field_metrics_count_positives_and_intervals() -> None:
    r = _labelled([({"payment_related": True}, {"payment_related": True}),
                   ({"payment_related": True}, {"payment_related": False}),
                   ({"payment_related": True}, {}),  # no answer counts as a miss
                   ({"payment_related": False}, {"payment_related": True}),
                   ({"payment_related": False}, {"payment_related": False})])  # fmt: skip
    m = field_metrics(r)["payment_related"]
    assert (m["n"], m["positives"], m["tp"], m["fp"], m["fn"]) == (5, 3, 1, 1, 2)
    assert m["recall"] == pytest.approx(33.3) and m["precision"] == pytest.approx(50.0)
    lo, hi = m["recall_wilson95"]
    assert lo < 33.3 < hi and m["report_only"] is True  # fewer than 20 positives


def test_field_metrics_ordinal_mae_and_direction() -> None:
    r = _labelled([({"fraud_risk": "high"}, {"fraud_risk": "high"}),
                   ({"fraud_risk": "high"}, {"fraud_risk": "medium"}),
                   ({"fraud_risk": "none"}, {"fraud_risk": "high"}),
                   ({"fraud_risk": "low"}, {"fraud_risk": "none"})])  # fmt: skip
    m = field_metrics(r)["fraud_risk"]
    assert m["mae"] == pytest.approx(1.25) and (m["under"], m["over"]) == (2, 1)


def test_field_metrics_confusion_only_for_category() -> None:
    r = _labelled([({"category": "invoice", "sender_type": "vendor"},
                    {"category": "invoice", "sender_type": "customer"}),
                   ({"category": "invoice", "sender_type": "vendor"},
                    {"category": "finance", "sender_type": "vendor"})])  # fmt: skip
    got = field_metrics(r)
    assert ["invoice", "finance", 1] in got["category"]["confusion"]
    assert "confusion" not in got["sender_type"] and "macro_f1" in got["sender_type"]
    lines = field_lines(r)
    assert any("confused: invoice->finance 1" in line for line in lines)


def test_field_metrics_are_empty_for_older_files() -> None:
    assert field_metrics(_run("A", [True, False])) == {}


def test_cli_compare_subset(tmp_path: Path) -> None:
    a = _run("A", [True, False, True])
    b = _run("B", [True, True, True, False]).model_copy(update={"set_version": "v2"})
    fa, fb, ids = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "ids.txt"
    fa.write_text(a.model_dump_json())
    fb.write_text(b.model_dump_json())
    r = CliRunner()
    refused = r.invoke(app, ["eval", "compare", str(fa), str(fb)])
    assert refused.exit_code != 0 and "--subset" in str(refused.exception)
    ids.write_text("0\n1\n2\n")
    out = r.invoke(app, ["eval", "compare", str(fa), str(fb), "--subset", str(ids)])
    assert (
        out.exit_code == 0 and "paired cases: 3" in out.output and "the sets differ" in out.output
    )
    same = r.invoke(app, ["eval", "compare", str(fa), str(fb), "--subset", str(fa)])
    assert same.exit_code == 0 and "paired cases: 3" in same.output


@pytest.mark.parametrize(("target", "ok"), [
    ("fraud_risk:low", True), ("priority:high", True), ("invoice", True), ("archive", True),
    ("fraud_risk:tiny", False), ("category:invoice", False), ("nofield:low", False),
    ("fraud_risk:high", False), ("priority:urgent", False),  # C4: the top level is every answer
])  # fmt: skip
def test_a_card_level_injection_target_is_checked(target: str, ok: bool) -> None:
    """S3b: `<ordinal field>:<level>` is refused for a non-ordinal field, an unknown level or the
    top level (fix review C4: at or below the top is always taken)."""
    from ecf.eval.cards import Safety  # noqa: PLC0415

    if ok:
        assert Safety(injection_target=target).injection_target == target
    else:
        with pytest.raises(ValueError, match="injection_target"):
            Safety(injection_target=target)
