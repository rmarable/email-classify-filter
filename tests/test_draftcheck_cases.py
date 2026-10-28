"""The draft checker's fixture file follows its own rules (M8 plan §1, AN3, AO6, AP4): every case
runs on its own, names known codes and a known email, gives a span for each refuse code, and keeps
superseded entries; every refuse code has a refusing case and a near miss. This test reads only
the file, so it runs before the checker exists."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ecf.yamlio import load_yaml

CASES = Path(__file__).parent / "fixtures" / "draftcheck_cases.yaml"
REFUSE_CODES = frozenset({
    "recipient", "money", "bank", "date", "price", "contact", "credential", "injection", "new_link",
    "characters", "language",
})  # fmt: skip
WARN_CODES = frozenset({"link"})
KEYS = frozenset({
    "id", "draft", "email", "refuse", "warn", "spans", "near_miss", "supersedes", "reason", "cost",
    "source", "od",
})  # fmt: skip


def _load() -> dict[str, Any]:
    data: dict[str, Any] = load_yaml(CASES.read_text(encoding="utf-8"), source=CASES.name)
    return data


def _active(data: dict[str, Any]) -> list[dict[str, Any]]:
    gone = {c["supersedes"] for c in data["cases"] if "supersedes" in c}
    return [c for c in data["cases"] if c["id"] not in gone]


def test_ids_are_unique_and_keys_known() -> None:
    cases = _load()["cases"]
    dupes = [i for i, n in Counter(c["id"] for c in cases).items() if n > 1]
    assert not dupes
    for c in cases:
        assert set(c) <= KEYS, (c["id"], set(c) - KEYS)
        assert isinstance(c["draft"], str) and c["draft"], c["id"]
        assert c.get("source"), c["id"]


def test_codes_and_emails_are_known() -> None:
    data = _load()
    assert "default" in data["emails"]
    for c in data["cases"]:
        assert set(c.get("refuse", [])) <= REFUSE_CODES, c["id"]
        assert set(c.get("warn", [])) <= WARN_CODES, c["id"]
        email = c.get("email", "default")
        assert (email in data["emails"]) if isinstance(email, str) else ("text" in email), c["id"]
        if "near_miss" in c:
            assert c["near_miss"] in REFUSE_CODES, c["id"]
            assert c["near_miss"] not in c.get("refuse", []), c["id"]


def test_every_refuse_code_has_a_span_in_the_draft() -> None:
    for c in _active(_load()):
        spans = c.get("spans", {})
        assert set(spans) == set(c.get("refuse", [])), c["id"]
        for code, text in spans.items():
            assert text in c["draft"], (c["id"], code)


def test_superseded_entries_are_kept_with_a_reason() -> None:
    cases = _load()["cases"]
    by_id = {c["id"]: c for c in cases}
    for c in cases:
        if "supersedes" in c:
            assert c["supersedes"] in by_id and c.get("reason"), c["id"]
            old = set(by_id[c["supersedes"]].get("refuse", []))
            if old - set(c.get("refuse", [])):  # a refusal turned into a pass needs its OD row
                assert c.get("od"), c["id"]


@pytest.mark.parametrize("code", sorted(REFUSE_CODES))
def test_every_code_has_a_refusing_case_and_a_near_miss(code: str) -> None:
    active = _active(_load())
    assert any(code in c.get("refuse", []) for c in active), code
    assert any(c.get("near_miss") == code for c in active), code
