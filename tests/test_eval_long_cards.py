"""Long cards for classifier window tuning (step 2, branch 3): a card may add a quoted thread, a
signature, a footer and padding to a character target, so the builder writes mail that reads
like a long real thread with its signal wherever the card puts it. Cards without these fields
build exactly as before."""

from __future__ import annotations

import hashlib
import itertools
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import labels
from ecf.eval.builder import build_all, build_bytes, scan_card
from ecf.eval.cards import PAD, Card, pad_order, parse_card
from ecf.eval.hygiene import scan_text
from ecf.eval.padbank import VENDOR, VOICES
from ecf_server import evalrun, ruletest, triggers
from ecf_server.clock import FakeClock

SYNTHETIC = Path(__file__).resolve().parent / "eval" / "synthetic"

LONG = """---
id: long-thread
title: A payment ask behind a long padded thread
author: claude
from: "Vendor A Billing <billing@vendor-a.example>"
subject: "Re: Order history"
date: 2026-10-01T09:00:00Z
signal: "please settle the outstanding balance through the new portal"
target_chars: 6000
signature: |
  Dana Ortiz
  Accounts, Vendor A
footer: "Vendor A Ltd. This message is for the named recipient only."
quoted_thread:
  - from: "Sam Reyes <sam@acme.example>"
    date: 2026-09-30T16:00:00Z
    body: |
      Thanks, could you send the latest statement?
  - from: "Dana Ortiz <billing@vendor-a.example>"
    date: 2026-09-29T10:00:00Z
    body: |
      Your September statement is attached.
expected:
  labels: {category: invoice, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true}
---
Hello Sam, here is the summary you asked for.

{{pad}}

Finally, please settle the outstanding balance through the new portal today.
"""


def _card(text: str = LONG) -> Card:
    return parse_card(text, source="x.md")


# Composition: what the plain body holds, in order, and how long.


def test_a_long_card_composes_its_plain_body() -> None:
    card = _card()
    text = card.plain_text()
    assert 6000 <= len(text) < 6000 + max(map(len, VENDOR)) + 2
    assert PAD not in text
    order = [text.index(s) for s in (
        "Hello Sam", "please settle", "\n-- \nDana Ortiz", "wrote:\n> Thanks, could you",
        "> > Your September statement", "Vendor A Ltd.")]  # fmt: skip
    assert order == sorted(order)
    assert text.index("please settle") > 4000  # the padding sits before the signal
    assert "On Wed, 30 Sep 2026 16:00:00 +0000, Sam Reyes <sam@acme.example> wrote:" in text
    assert "> On Tue, 29 Sep 2026 10:00:00 +0000, Dana Ortiz" in text
    assert text.endswith("Vendor A Ltd. This message is for the named recipient only.\n")


def test_padding_is_deterministic_and_seeded_by_the_card() -> None:
    assert build_bytes(_card()) == build_bytes(_card())
    other = _card(LONG.replace("id: long-thread", "id: long-thread-2"))
    a, b = _card().plain_text(), other.plain_text()
    assert a != b and a.startswith("Hello Sam")  # another id, other paragraphs


def test_without_a_marker_the_padding_follows_the_text() -> None:
    card = _card(LONG.replace("{{pad}}\n\n", ""))
    text = card.plain_text()
    assert text.index("please settle") < 200 < text.index("\n-- \n")
    assert 6000 <= len(text) < 6000 + max(map(len, VENDOR)) + 2  # within one paragraph (B6)


def test_the_padding_order_is_fixed_by_the_id_alone() -> None:
    """A SHA-256 order, the same on every Python version (B2): pinned here, so a change to it,
    which would change every long card's bytes, fails a test."""
    assert list(itertools.islice(pad_order("long-thread", 44), 6)) == [42, 35, 28, 21, 14, 7]
    seq = list(itertools.islice(pad_order("any-card", 44), 88))
    assert sorted(seq[:44]) == list(range(44)) and seq[44:] == seq[:44]
    assert all(a != b for a, b in itertools.pairwise(seq))  # never the same paragraph twice


def test_the_bank_is_pinned() -> None:
    """Editing a paragraph re-hashes every long card that draws it: done on purpose only."""
    digest = hashlib.sha256(json.dumps(VOICES, sort_keys=True).encode()).hexdigest()
    assert digest == "3d13588646a3995c8f67660c284fa4b5339166e1d2dee39a6f53339812093622"
    assert {v: len(b) for v, b in VOICES.items()} == {"vendor": 44, "internal": 45,
                                                       "personal": 44}  # fmt: skip
    assert all(len(set(b)) == len(b) for b in VOICES.values())


def test_a_card_picks_its_padding_voice() -> None:
    card = _card(LONG.replace("target_chars: 6000", "target_chars: 6000\npad_voice: personal"))
    text = card.plain_text()
    assert any(p in text for p in VOICES["personal"])
    assert not any(p in text for p in VENDOR)


def test_short_padding_and_tail_only_cards() -> None:
    plain = _card(LONG.replace("target_chars: 6000\n", "").replace("{{pad}}\n\n", ""))
    assert plain.plain_text().startswith("Hello Sam") and "\n-- \nDana" in plain.plain_text()
    just = len(plain.plain_text()) + 1
    one = _card(LONG.replace("target_chars: 6000", f"target_chars: {just}").replace(
        "{{pad}}\n\n", ""))  # fmt: skip
    assert sum(p in one.plain_text() for p in VENDOR) == 1  # one paragraph is enough


def test_a_card_without_the_fields_builds_its_body_unchanged() -> None:
    card = parse_card((SYNTHETIC / "cases" / "starter-bec.md").read_text("utf-8"))
    assert card.plain_text() == card.body


def test_rebuilding_the_shipped_set_changes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "synthetic"
    shutil.copytree(SYNTHETIC, root, ignore=shutil.ignore_patterns(".build"))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not build_all(root).findings
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
             and ".build" not in p.parts}  # fmt: skip
    assert after == before


# The run sees what the builder wrote: the signal past 4,000, quoted text moved.


def test_the_eval_measures_a_long_card(tmp_path: Path) -> None:
    root = tmp_path / "synthetic"
    (root / "cases").mkdir(parents=True)
    (root / "cases" / "long-thread.md").write_text(LONG)
    assert not build_all(root).findings
    labels.confirm(root, "long-thread", date(2026, 10, 1))
    (case,), _ = evalrun.load(root, fraud_only=False)
    scratch = ruletest.Scratch(FakeClock())
    try:
        _, _, m = evalrun.case_inputs_full(case, scratch, triggers.redact_injection)
    finally:
        scratch.close()
    assert m["prepared_chars"] > 5000 and m["moved_mark"] is True
    assert m["signal_offset"] > 4000 and m["signal_seen"] is False


# Refusals.


@pytest.mark.parametrize(("old", "new", "match"), [
    ("{{pad}}", "{{pad}}\n\n{{pad}}", "at most once"),
    ("target_chars: 6000\n", "", "needs target_chars"),
    ("target_chars: 6000", "target_chars: 6000\npad_to_mb: 1", "pad_to_mb doesn't go"),
    ("portal today.\n", "portal today.\n\n## html\n<p>hi</p>\n", "plain-body cards"),
    ("target_chars: 6000", "target_chars: 100", "shorter than"),
    ("Thanks, could you send the latest statement?",
     "Please settle the outstanding balance through the new portal.", "2 times"),
    ("  Accounts, Vendor A\n", "  Accounts, Vendor A\n  {{pad}}\n", "body only"),
    ("target_chars: 6000", "target_chars: 6000\nhidden_text: x", "no html or hidden_text"),
    ("author: claude", "author: generated\ngenerator: m", "generated card"),
    ("    date: 2026-09-30T16:00:00Z\n", "", "date"),
    ("      Your September statement is attached.\n", "      \n", "needs a body"),
    ('signal: "please settle the outstanding balance through the new portal"',
     'signal: "sam reyes"', "only in text the builder writes"),
    ('signal: "please settle the outstanding balance through the new portal"',
     'signal: "the pallets arrived in good condition"', "padding paragraphs"),
    ("target_chars: 6000\n", "pad_voice: personal\n", "needs target_chars"),
])  # fmt: skip
def test_refusals(old: str, new: str, match: str) -> None:
    assert old in LONG
    with pytest.raises(InvalidInputError, match=match):
        _card(LONG.replace(old, new))


def test_pad_to_mb_doesnt_go_with_a_tail() -> None:
    with pytest.raises(InvalidInputError, match="pad_to_mb"):
        _card(LONG.replace("target_chars: 6000\n", "pad_to_mb: 1\n").replace("{{pad}}\n\n", ""))


def test_quoted_crlf_is_normalised() -> None:
    card = _card(LONG.replace("Thanks, could you send the latest statement?",
                              '"Line one\\r\\nLine two"').replace("    body: |\n      \"Line",
                                                                    '    body: "Line'))  # fmt: skip
    assert "\r" not in card.plain_text()


def test_a_phrase_may_sit_in_the_quoted_thread() -> None:
    card = _card(LONG.replace("Finally, please settle the outstanding balance through the new"
                              " portal today.", "Finally, see below.").replace(
        "Thanks, could you send the latest statement?",
        "Please settle the outstanding balance through the new portal."))  # fmt: skip
    assert card.signal and "> Please settle" in card.plain_text()


# Hygiene: the builder's own text is clean, and the new fields are scanned.


def test_the_padding_and_quote_headers_are_hygiene_clean() -> None:
    for voice, bank in VOICES.items():
        for i, para in enumerate(bank):
            assert scan_text(para, f"{voice}{i}") == []
            assert para.isascii()
    assert scan_card(_card(), "long-thread.md") == []


def test_the_padding_trips_no_trigger_and_nothing_is_redacted() -> None:
    """Padding must not move the measurement: no keyword hit, no injection redaction (B5)."""
    for bank in VOICES.values():
        for para in bank:
            assert not any(triggers.scan([para]).values()), para[:60]
            assert triggers.redact_injection(para) == para


def test_the_new_fields_are_scanned() -> None:
    bad_sig = _card(LONG.replace("Accounts, Vendor A", "Accounts, call 212-555-7788"))
    assert any("signature" in f.where for f in scan_card(bad_sig, "x.md"))
    bad_from = _card(LONG.replace("sam@acme.example", "sam@acme-corp.com"))
    assert any("quoted_thread" in f.where for f in scan_card(bad_from, "x.md"))


# What the author and the labeller see (B1, B3).


def test_build_reports_where_each_phrase_lands(tmp_path: Path) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    root = tmp_path / "synthetic"
    (root / "cases").mkdir(parents=True)
    (root / "cases" / "long-thread.md").write_text(LONG)
    near = LONG.replace("id: long-thread", "id: long-near").replace(
        "target_chars: 6000", "target_chars: 1800")  # fmt: skip
    (root / "cases" / "long-near.md").write_text(near)
    quoted = LONG.replace("id: long-thread", "id: long-quoted").replace(
        "Finally, please settle the outstanding balance through the new portal today.",
        "Finally, see below.").replace("Thanks, could you send the latest statement?",
                                        "Please settle the outstanding balance through the new"
                                        " portal.")  # fmt: skip
    (root / "cases" / "long-quoted.md").write_text(quoted)
    report = build_all(root)
    placed = {(cid, name): (at, moved) for cid, name, at, moved in report.placed}
    at, moved = placed[("long-thread", "signal")]
    assert at > 4000 and moved is False and placed[("long-quoted", "signal")][1] is True
    out = CliRunner().invoke(app, ["eval", "build", "--root", str(root)]).output
    assert f"long-thread: signal at {at:,} (past 4,000)" in out
    assert "long-near: signal at" in out and "within 200 of 1,500" in out
    assert "long-quoted: signal at" in out and "moved below the mark" in out
    for cid in ("long-thread", "long-quoted"):
        labels.confirm(root, cid, date(2026, 10, 1))
    cases, _ = evalrun.load(root, fraud_only=False)
    scratch = ruletest.Scratch(FakeClock())
    try:
        got = {c.id: evalrun.case_inputs_full(c, scratch, triggers.redact_injection)[2]
               for c in cases if c.id in ("long-thread", "long-quoted")}  # fmt: skip
    finally:
        scratch.close()
    assert got["long-thread"]["signal_offset"] == at  # new text: the run finds it where built
    assert got["long-quoted"]["signal_offset"] > placed[("long-quoted", "signal")][0]


def test_the_views_show_the_built_email(tmp_path: Path) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415
    from ecf.cli_card_review import screen  # noqa: PLC0415

    shown = "\n".join(screen(_card()))
    assert "wrote:" in shown and PAD not in shown and "please settle" in shown  # past 4,000
    root = tmp_path / "synthetic"
    (root / "cases").mkdir(parents=True)
    (root / "cases" / "long-thread.md").write_text(LONG)
    assert not build_all(root).findings
    out = CliRunner().invoke(app, ["eval", "show", "long-thread", "--root", str(root)]).output
    assert "wrote:" in out and PAD not in out and "please settle" in out


# The fix check's findings (C1-C9).


def test_a_short_card_with_a_long_html_part_is_still_cut() -> None:
    from ecf.cli_card_review import screen  # noqa: PLC0415

    html = "<p>" + " ".join(["routine words"] * 600) + "</p>"
    card = parse_card((SYNTHETIC / "cases" / "starter-control.md").read_text("utf-8").rstrip()
                      + "\n\n## html\n" + html + "\n")  # fmt: skip
    assert "[cut at 4,000 characters]" in "\n".join(screen(card))


def test_a_footer_only_phrase_isnt_moved() -> None:
    card = _card(LONG.replace(
        'signal: "please settle the outstanding balance through the new portal"',
        'signal: "named recipient only"').replace("signature: |\n  Dana Ortiz\n"
        "  Accounts, Vendor A\n", "").replace(LONG[LONG.index("quoted_thread:"):
                                                   LONG.index("expected:")], ""))  # fmt: skip
    ((name, _, moved),) = card.phrase_offsets()
    assert name == "signal" and moved is False


def test_label_shows_a_long_card_whole() -> None:
    from ecf.cli import _label_body  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    shown = "\n".join(_label_body(_card()))
    assert "please settle" in shown and "wrote:" in shown and PAD not in shown
    short = parse_card((SYNTHETIC / "cases" / "starter-bec.md").read_text("utf-8"))
    assert len(_label_body(short)) == 1  # one line, at most 400 characters, as before


def test_an_empty_body_long_card_checks_its_phrase_in_the_tail() -> None:
    text = LONG.replace(
        LONG[LONG.index("---\nHello Sam"):], "---\n").replace(
        'signal: "please settle the outstanding balance through the new portal"',
        'signal: "accounts, vendor a"').replace("target_chars: 6000\n", "")  # fmt: skip
    assert _card(text).signal == "accounts, vendor a"


def test_the_pad_marker_must_be_a_line_of_its_own() -> None:
    for line in ("well {{pad}} then", "  {{pad}}"):  # an indent would shift offsets (D1)
        with pytest.raises(InvalidInputError, match="line of its own"):
            _card(LONG.replace("\n{{pad}}\n", f"\n{line}\n"))


def test_a_quoted_thread_runs_newest_first() -> None:
    for later in ("2026-09-30T18:00:00Z", "2026-09-30T16:00:00Z"):  # newer, or the same (D2)
        with pytest.raises(InvalidInputError, match="newest first"):
            _card(LONG.replace("2026-09-29T10:00:00Z", later))


def test_a_long_field_finding_is_reported_once() -> None:
    bad = _card(LONG.replace("Accounts, Vendor A", "Accounts, call 212-555-7788"))
    found = [f for f in scan_card(bad, "x.md") if "7788" in f.value]
    assert [f.where for f in found] == ["x.md:signature"]


def test_a_quoted_date_without_a_zone_is_read_as_utc() -> None:
    """Mixing dates with and without a zone is a clear error or works, never a traceback (E1)."""
    card = _card(LONG.replace("date: 2026-09-29T10:00:00Z", "date: 2026-09-29 10:00:00"))
    assert "On Tue, 29 Sep 2026 10:00:00 +0000, Dana Ortiz" in card.plain_text()
