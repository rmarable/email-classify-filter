"""Blind label review (`ecf eval label --review`; SPEC §16.1; classifier plan 0.2): answers come
from the email alone, are logged in reviews.jsonl, move a card's labels only when the operator
keeps their answer, and confirm the card once its other expected values are checked."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf import cli_card_review as ui
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, labels
from ecf.eval import review as rv
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.schema import load_schema

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
HELDOUT = Path(__file__).parent / "eval" / "heldout"
DAY = date(2026, 10, 9)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    return r


def run(root: Path, fields: str, answers: list[str], **kw: Any) -> tuple[list[str], str]:
    """Review with scripted answers, cards in file order; returns the summary and the screen."""
    it: Iterator[str] = iter(answers)
    out: list[str] = []
    lines = ui.review(root, fields, read=lambda _p: next(it), write=out.append, today=DAY,
                      shuffle=lambda _x: None, **kw)  # fmt: skip
    assert next(it, None) is None, "not every scripted answer was asked for"
    return lines, "".join(out)


def card_labels(root: Path, case_id: str) -> dict[str, Any]:
    path = root / "cases" / f"{case_id}.md"
    return parse_card(path.read_text(encoding="utf-8")).expected.labels


def test_an_html_part_is_shown_with_each_links_real_target() -> None:
    card = parse_card((HELDOUT / "cases" / "ho-link-text-mismatch.md").read_text(encoding="utf-8"))
    shown = "\n".join(ui.screen(card))
    assert (
        "https://files.vendor-e.example/s/q3-statement"
        " [link: https://vendor-e-files.test/auth/start?doc=q3stmt&u=ap]"
    ) in shown
    assert "sign in with your acme.example work account" in shown
    assert shown.index("[HTML part") < shown.index("[plain-text part]")
    assert "HTML-capable mail reader" in shown


def test_quoted_html_is_marked_and_an_empty_plain_part_is_not_shown() -> None:
    path = HELDOUT / "cases" / "ho-thread-html-blockquote-giftcard.md"
    shown = "\n".join(ui.screen(parse_card(path.read_text(encoding="utf-8"))))
    assert "> Please pick up eight" in shown and "[plain-text part]" not in shown
    body = shown[shown.index("[HTML part") :].replace("<dana-chief@acme.example>", "")
    assert "<" not in body


def test_the_card_is_shown_without_its_id_notes_or_labels(root: Path) -> None:
    _, screen = run(root, "fraud_risk", ["q"], globs=("starter-bec",))
    card = parse_card((root / "cases" / "starter-bec.md").read_text(encoding="utf-8"))
    assert "Subject:" in screen and card.subject in screen
    for hidden in (card.id, card.title, card.threat, "expected", "fraud_guard"):
        assert hidden not in screen
    assert rv.read_log(root) == []


def test_a_matching_answer_confirms_and_the_pending_card_is_confirmed(root: Path) -> None:
    lines, screen = run(root, "fraud_risk", ["", "high", "y"], globs=("starter-bec",))
    [entry] = rv.read_log(root)
    assert entry["outcome"] == rv.CONFIRMED and entry["before"] == entry["final"] == "high"
    assert "This was starter-bec" in screen and "you said" not in screen
    assert labels.is_confirmed(labels.read(root)[0])
    assert "  fraud_risk: confirmed 1" in lines


def test_keeping_your_answer_moves_the_label_and_rebuilds(root: Path) -> None:
    run(root, "fraud_risk", ["", "3", "y", "y"], globs=("starter-bec",))  # 3 = medium
    assert card_labels(root, "starter-bec")["fraud_risk"] == "medium"
    [entry] = rv.read_log(root)
    assert (entry["outcome"], entry["before"], entry["final"]) == (rv.MOVED, "high", "medium")
    row = next(r for r in labels.read(root) if r["id"] == "starter-bec")
    assert row["expected"]["labels"]["fraud_risk"] == "medium" and labels.is_confirmed(row)


def test_keeping_the_cards_label_changes_nothing(root: Path) -> None:
    before = (root / "cases" / "starter-bec.md").read_text(encoding="utf-8")
    _, screen = run(root, "fraud_risk", ["", "medium", "c", "n"], globs=("starter-bec",))
    assert "fraud_risk: you said medium; the card says high." in screen
    assert (root / "cases" / "starter-bec.md").read_text(encoding="utf-8") == before
    assert rv.read_log(root)[0]["outcome"] == rv.KEPT
    assert not labels.is_confirmed(labels.read(root)[0])  # n: left pending


def test_a_sitting_resumes_and_redo_asks_again(root: Path) -> None:
    run(root, "fraud_risk", ["", "high", "y", "", "none", "y"])
    _, screen = run(root, "fraud_risk", [])
    assert "0 card(s) to review" in screen
    run(root, "fraud_risk", ["s", "s"], redo=True)


def test_quitting_mid_card_saves_nothing_for_it(root: Path) -> None:
    run(root, "category,fraud_risk", ["", "invoice", "q"])
    assert rv.read_log(root) == []


def test_add_asks_unlabelled_fields_and_logs_them_apart(root: Path) -> None:
    run(root, "fraud_risk,sender_type", ["", "-", "none", "y"], add=True,
        globs=("starter-control",))  # fmt: skip
    outcomes = {x["field"]: x["outcome"] for x in rv.read_log(root)}
    assert outcomes == {"fraud_risk": rv.CONFIRMED, "sender_type": rv.UNLABELLED}
    assert "sender_type" not in card_labels(root, "starter-control")
    run(root, "sender_type", ["", "company", "y"], add=True, redo=True,
        globs=("starter-control",))  # fmt: skip
    assert card_labels(root, "starter-control")["sender_type"] == "company"
    assert rv.read_log(root)[-1]["outcome"] == rv.ADDED


def test_all_asks_each_cards_labelled_fields(root: Path) -> None:
    [task] = rv.tasks([parse_card((root / "cases" / "starter-bec.md").read_text())],
                      labels.read(root)[:1], load_schema(), None, add=False)  # fmt: skip
    assert set(task.fields) == set(card_labels(root, "starter-bec"))


def test_a_recheck_asks_again_and_reports_agreement(root: Path) -> None:
    run(root, "fraud_risk", ["", "high", "y", "", "none", "y"])
    lines, _ = run(root, "fraud_risk", ["", "medium"], recheck=1, globs=("starter-bec",))
    last = rv.read_log(root)[-1]
    assert (last["kind"], last["before"], last["answer"]) == (rv.RECHECK, "high", "medium")
    assert card_labels(root, "starter-bec")["fraud_risk"] == "high"
    assert "  fraud_risk: 0 of 1 the same" in lines
    _, screen = run(root, "fraud_risk", [], recheck=1, globs=("starter-bec",))
    assert "0 card(s) to recheck" in screen


def test_fields_are_checked_against_the_schema() -> None:
    schema = load_schema()
    assert rv.fields_for("fraud_risk, category", schema) == ("category", "fraud_risk")
    assert rv.fields_for("all", schema) is None
    with pytest.raises(InvalidInputError, match="not a schema field: nope"):
        rv.fields_for("nope", schema)


def test_a_card_without_a_one_line_labels_map_is_left_for_a_hand_edit(root: Path) -> None:
    path = root / "cases" / "starter-bec.md"
    text = path.read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("  labels: {"))
    path.write_text(text.replace(line, "  labels:\n    fraud_risk: high"), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="edit it by hand"):
        rv.set_labels(path, {"fraud_risk": "low"})


def test_the_cli_checks_its_options(root: Path) -> None:
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--add"])
    assert isinstance(r.exception, InvalidInputError) and "go with --review" in str(r.exception)
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "fraud_risk",
                                 "--status"])  # fmt: skip
    assert isinstance(r.exception, InvalidInputError)
    assert "--review takes no case id" in str(r.exception)
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "fraud_risk"])
    assert r.exit_code != 0  # no terminal under the test runner
    assert rv.read_log(root) == []


def test_the_committed_review_log_is_consistent() -> None:
    for x in rv.read_log(SYNTHETIC):
        # fixed: S2 hand labels; page: S3b's card review page answers (category, sender_type)
        assert x["kind"] in (rv.REVIEW, rv.RECHECK, fixed_labels.KIND, rv.PAGE)
        if x["kind"] == rv.PAGE:
            assert x["field"] in rv.CARD_FIELDS and x["how"] in rv.HOW
        assert x["kind"] == rv.RECHECK or x["outcome"] in rv.OUTCOMES


def test_the_review_shows_the_schemas_texts(root: Path) -> None:
    _, screen = run(root, "sender_type", ["", "q"], add=True, globs=("starter-bec",))
    assert "Firms we don't buy from, including cold pitches" in screen
    assert "A business that is neither a vendor nor a customer" not in screen


def test_category_is_asked_with_its_order_guide(root: Path) -> None:
    _, screen = run(root, "category", ["", "q"], globs=("starter-bec",))
    assert "When several fit, choose in this order" in screen  # S3b wording (plan §12)
    assert "A fake email gets the value of what it pretends to be" in screen
    assert "vendor_change_request" not in screen  # removed in v3 (S3b)


def test_fraud_risk_is_asked_with_the_plans_level_texts(root: Path) -> None:
    _, screen = run(root, "fraud_risk", ["", "q"], globs=("starter-bec",))
    assert "Any request to move money, data, credentials or access" in screen


def test_no_labelling_text_starts_with_an_article() -> None:
    """Operator style (2026-10-09) for the schema's value texts: "Meeting ...", not "A ..."."""
    for field in load_schema().fields.values():
        for text in field.value_descriptions:
            assert not text.startswith(("A ", "An ")), text


# ---- a card review page's answers for a card field (S3b, OD-500, P11) --------------------------


def _old_card(root: Path) -> None:
    """starter-bec as it was before the S3b relabel (a removed value), built and confirmed."""
    path = root / "cases" / "starter-bec.md"
    text = path.read_text(encoding="utf-8").replace(
        "{category: notification, payment_related: true, fraud_risk: high,"
        " bank_detail_change: true}",
        "{category: vendor_change_request, payment_related: true, fraud_risk: high}",
    )
    assert "vendor_change_request" in text
    path.write_text(text, encoding="utf-8")
    assert not build_all(root).findings
    labels.confirm(root, "starter-bec", DAY)


def test_a_page_relabel_writes_the_card_keeps_its_items_and_confirms_again(root: Path) -> None:
    _old_card(root)
    got = rv.record_page(root, "category", {
        "starter-bec": {"label": "invoice", "how": "single"},
        "starter-control": {"label": "marketing", "how": "bulk"},  # not confirmed before
    }, DAY, ui.rebuild)  # fmt: skip
    assert got == {"answers": 2, "changed": 1, "reconfirmed": 1, "pending": ["starter-control"],
                   "kept_items": 1}  # fmt: skip
    # the item the removed value implied stays: the relabel moves no item label
    assert card_labels(root, "starter-bec") == {
        "category": "invoice",
        "payment_related": True,
        "fraud_risk": "high",
        "bank_detail_change": True,
    }
    rows = {r["id"]: r for r in labels.read(root)}
    assert labels.is_confirmed(rows["starter-bec"])
    assert not labels.is_confirmed(rows["starter-control"])
    log = {x["id"]: x for x in rv.read_log(root)}
    assert log["starter-bec"] | {"sha256": ""} == {
        "id": "starter-bec", "field": "category", "sha256": "", "before": "vendor_change_request",
        "answer": "invoice", "final": "invoice", "outcome": rv.MOVED, "on": DAY.isoformat(),
        "kind": rv.PAGE, "how": "single", "kept_items": ["bank_detail_change"]}  # fmt: skip
    assert (log["starter-control"]["outcome"], log["starter-control"]["how"]) == (
        rv.CONFIRMED,
        "bulk",
    )


def test_a_page_keeps_no_phishing_label_and_refuses_bad_answers(root: Path) -> None:
    """`phishing` is never kept from `spam_or_phishing` (split by hand in fixed_labels.jsonl); a
    removed value, an unknown case or a field the page doesn't set is refused, nothing written."""
    old = {"category": "spam_or_phishing", "sender_type": "team"}
    assert rv.kept_items("category", old, old | {"category": "invoice"}) == {}
    # `unknown` derives nothing, so what `team` made true or false is kept as it was
    assert rv.kept_items("sender_type", old, old | {"sender_type": "unknown"}) == {
        "claims_internal": True,
        "regulator_claim": False,
        "private_individual": False,
    }
    assert rv.kept_items("category", {"category": "invoice"}, {"category": "other"}) == {}
    before = (root / "labels.jsonl").read_bytes()
    for field, answers, why in [
        ("category", {"starter-bec": {"label": "regulatory", "how": "single"}}, "isn't a"),
        ("category", {"nope": {"label": "invoice", "how": "single"}}, "not cases"),
        ("category", {"starter-bec": {"label": "invoice"}}, "how single/bulk"),
        ("priority", {"starter-bec": {"label": "low", "how": "single"}}, "isn't a card field"),
    ]:  # fmt: skip
        with pytest.raises(InvalidInputError, match=why):
            rv.record_page(root, field, answers, DAY, ui.rebuild)
    assert (root / "labels.jsonl").read_bytes() == before and rv.read_log(root) == []


def test_import_fixed_routes_a_card_field_to_the_cards(root: Path, tmp_path: Path) -> None:
    _old_card(root)
    f = tmp_path / "answers.json"
    f.write_text('{"item": "category", "on": "2026-10-13", "answers": {"starter-bec":'
                 ' {"label": "invoice", "how": "bulk"}}}')  # fmt: skip
    r = CliRunner().invoke(app, ["eval", "label", "--import-fixed", str(f), "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "1 cards changed (1 kept a fixed item" in r.output and "1 confirmed again" in r.output
    assert card_labels(root, "starter-bec")["category"] == "invoice"
    assert not (root / fixed_labels.FILE).exists()  # a card field never goes there


def test_the_review_says_a_removed_label_is_gone_and_keeps_your_answer(root: Path) -> None:
    _old_card(root)
    _, screen = run(root, "category", ["", "invoice", "y"], globs=("starter-bec",))
    assert "the card says vendor_change_request (removed in v3), so yours is kept" in screen
    assert "Keep [y]ours" not in screen
    assert card_labels(root, "starter-bec")["category"] == "invoice"
