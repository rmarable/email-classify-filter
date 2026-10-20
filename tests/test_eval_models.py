"""The shipped generator and grader model list (eval generate plan, Model switches; P24)."""

from __future__ import annotations

import json
from importlib import resources

from ecf.eval import models


def test_the_claude_entries_match_the_pinned_models() -> None:
    lock = json.loads(resources.files("ecf_server.data").joinpath("models.lock").read_text())
    claude = {m.id for m in models.listed() if m.provider == "claude"}
    assert claude == set(lock["lifecycle"])
    assert all(m.family == "anthropic" and m.modes == ("dev", "prod")
               for m in models.listed() if m.provider == "claude")  # fmt: skip


def test_no_listed_model_is_one_the_filter_bars() -> None:
    for m in models.listed():
        assert models.excluded(m.provider, m.id) is None, m.id
        assert models.check(m.provider, m.id, m.roles[0], m.modes[0]) == m


def test_api_graders_carry_a_dated_price() -> None:
    for m in models.listed():
        if m.provider == "mistral":
            assert m.price is not None
            assert {"input", "output", "source", "fetched"} <= set(m.price)


def test_the_classifier_family_comes_from_the_result() -> None:
    assert models.classifier_family("claude/claude") == "anthropic"
    assert models.classifier_family("jev/claude") == "typesafe"
    assert models.classifier_family("gemini/local") is None
