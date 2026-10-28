"""The draft checker against its fixture file (M8 plan §1, AM1): every active case refuses and warns
with exactly its expected codes, and each refuse code matched text inside the case's span, so no
case passes for the wrong reason. Also the accented-letter list, generated as the plan defines it
(AI3), and the checker's own word lists."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import pytest

from ecf.yamlio import load_yaml
from ecf_server import draftcheck

CASES = Path(__file__).parent / "fixtures" / "draftcheck_cases.yaml"


@cache
def _data() -> dict[str, Any]:
    data: dict[str, Any] = load_yaml(CASES.read_text(encoding="utf-8"), source=CASES.name)
    return data


def _active() -> list[dict[str, Any]]:
    cases = _data()["cases"]
    gone = {c["supersedes"] for c in cases if "supersedes" in c}
    return [c for c in cases if c["id"] not in gone]


def _email(data: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    email = case.get("email", "default")
    found: dict[str, Any] = data["emails"][email] if isinstance(email, str) else email
    return found


@pytest.mark.parametrize("case", _active(), ids=lambda c: c["id"])
def test_a_fixture_case(case: dict[str, Any]) -> None:
    data = _data()
    email = _email(data, case)
    got = draftcheck.check(case["draft"], email["text"], tuple(email.get("links", ())),
                           tuple(data["allowed"]))  # fmt: skip
    assert sorted(got.refuse) == sorted(case.get("refuse", [])), got.spans
    assert sorted(got.warn) == sorted(case.get("warn", []))
    for code, want in case.get("spans", {}).items():
        span = got.spans[code]  # the matched text, not one character of it (AR-F4)
        assert want in span or (span in want and 2 * len(span) >= len(want)), (code, span, want)


def test_the_accented_letters_are_the_plans_list() -> None:
    """AI3: the precomposed letters in U+00C0-U+017F whose first two folded forms are plain ASCII
    letters, less U+0131, U+0130 and U+017F, regenerated and compared with the committed list."""
    assert draftcheck.accented_letters() == draftcheck.generated_accented_letters()
    assert len(draftcheck.accented_letters()) == 178


def test_characters_is_reported_alone() -> None:
    """The fixture file's header: on text `characters` refuses, no other code is reported."""
    got = draftcheck.check("We will pay \ue000 Friday, account 44712233.", "", (), ())
    assert got.refuse == ("characters",)


def test_the_result_carries_the_lists_digest() -> None:
    """P24: the checker's digest is recorded wherever it is used."""
    got = draftcheck.check("Thanks, Pat.", "", (), ())
    assert got.digest == draftcheck.digest() and len(got.digest) == 64
