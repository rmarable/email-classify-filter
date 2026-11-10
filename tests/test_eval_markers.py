"""X1's marker predicate and the builder's checks (EVAL-PRIVATE.md X1, "Builder refusal", V3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import markers
from ecf.eval.builder import build_all, marker_refusals
from ecf.eval.case_templates import TEMPLATES

SYNTHETIC = Path(__file__).resolve().parent / "eval" / "synthetic"
EVAL = SYNTHETIC.parent

PLAIN = """---
id: {id}
title: test
from: "A <a@vendor-a.example>"
subject: "Hello"
{extra}---
Body line
"""


def _card(card_id: str, extra: str = "") -> str:
    return PLAIN.format(id=card_id, extra=extra)


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ("expected:\n  safety: {injection_target: archive}\n", ["injection_target"]),
        ("injection_phrase: x\n", ["injection_phrase"]),
        ("hidden_text: x\n", ["hidden_text"]),
        ("threat: Prompt INJECTION past the window\n", ["threat"]),
        ("expected:\n  draft:\n    group: injection\n", ["draft.group"]),
        ("threat: size and encoding evasion\n", []),  # V4: not a marker
        ("threat: bank change\n", []),
    ],
)
def test_marker_reasons(extra: str, why: list[str]) -> None:
    assert markers.reasons(markers.front_matter(_card("x", extra))) == why


def test_starter_pin_matches_its_file_and_only_its_exact_id() -> None:
    data = (SYNTHETIC / "cases" / "starter-injection.md").read_bytes()
    assert markers.is_pinned_starter("starter-injection", data)
    assert not markers.is_pinned_starter("starter-injection-2", data)
    assert not markers.is_pinned_starter("starter-injection", data + b"\n")
    # the card is the shipped template with author claude, rule fraud_guard, must_escalate true
    want = TEMPLATES["injection"].format(id="starter-injection")
    want = want.replace("author: hand", "author: claude").replace(
        "  safety: {must_not_hide: true,",
        "  rule: fraud_guard\n  safety: {must_escalate: true, must_not_hide: true,",
    )
    assert want == data.decode()


def test_marked_cards_are_refused_in_a_public_set_and_required_in_a_private_one(
    tmp_path: Path,
) -> None:
    pub = tmp_path / "pub"
    (pub / "cases").mkdir(parents=True)
    (pub / "cases" / "plain.md").write_text(_card("plain"))
    (pub / "cases" / "inj.md").write_text(_card("inj", "hidden_text: x\n"))
    (pub / "cases" / "starter-injection.md").write_bytes(
        (SYNTHETIC / "cases" / "starter-injection.md").read_bytes()
    )
    (pub / "cases" / "hand.md").write_text(_card("hand", "author: hand\nhidden_text: x\n"))
    # a generated card is exempt in a public set (it can't go private, P1); nowhere else
    (pub / "cases" / "made.md").write_text(_card("made", "author: generated\nhidden_text: x\n"))
    assert marker_refusals(pub, private=False) == [
        "hand: marked card in a public set (hidden_text)",
        "inj: marked card in a public set (hidden_text)",
    ]
    assert marker_refusals(pub, private=True) == ["plain: private card without an injection marker"]


def test_committed_public_sets_mark_no_card() -> None:
    """After the move every injection card is private; only the pinned starter stays public. The
    predicate is applied directly: the builder's generated-card exemption doesn't apply here."""
    marked = [
        p.relative_to(EVAL).as_posix()
        for p in sorted(EVAL.glob("*/cases/*.md"))
        if markers.reasons(markers.front_matter(p.read_text(), source=p.name))
        and not markers.is_pinned_starter(p.stem, p.read_bytes())
    ]
    assert not marked, marked


def test_build_refuses_a_marked_card_in_a_public_set(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "plain.md").write_text(_card("plain"))
    (tmp_path / "cases" / "inj.md").write_text(_card("inj", "hidden_text: x\n"))
    with pytest.raises(InvalidInputError) as e:
        build_all(tmp_path)
    assert str(e.value) == ("an injection card belongs in the private set (X1): inj: marked card"
                            " in a public set (hidden_text)")  # fmt: skip
    assert not (tmp_path / "eml").exists()  # refused before anything is written
    (tmp_path / "cases" / "inj.md").unlink()
    assert build_all(tmp_path).built == ["plain"]


def test_build_refuses_a_card_whose_id_differs_from_its_file_and_an_orphan_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "a.md").write_text(_card("b"))
    with pytest.raises(InvalidInputError, match="equal their file names"):
        build_all(tmp_path)
    (tmp_path / "cases" / "a.md").write_text(_card("a"))
    (tmp_path / "eml").mkdir()
    (tmp_path / "eml" / "gone.eml").write_bytes(b"x")
    with pytest.raises(InvalidInputError, match=r"no card for eml/gone\.eml"):
        build_all(tmp_path)
