"""The weekly model canary (scripts/model_canary.py; V1.4 step 10; SPEC §7.6; OD-300), on excerpts
of the three pages as they read on 2026-10-02."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from ecf_server import claude_pins

ROOT = Path(__file__).resolve().parents[1]
PAGES = Path(__file__).parent / "canary"
DEPRECATIONS = (PAGES / "deprecations-2026-10-02.md").read_text("utf-8")
OVERVIEW = (PAGES / "overview-2026-10-02.md").read_text("utf-8")
TAGS = (PAGES / "gemma4-tags-2026-10-02.html").read_text("utf-8")


@pytest.fixture(scope="module")
def canary() -> ModuleType:
    spec = importlib.util.spec_from_file_location("model_canary",
                                                  ROOT / "scripts" / "model_canary.py")  # fmt: skip
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["model_canary"] = mod  # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


def test_models_lock_matches_the_pages_as_verified(canary: ModuleType) -> None:
    rep = canary.check(DEPRECATIONS, OVERVIEW, TAGS)
    assert rep.failures == []
    assert rep.notes == ["Ollama library: 51 gemma4 tags; gemma4:12b listed"]


def test_the_status_table_parses_every_kind_of_date(canary: ModuleType) -> None:
    st = canary.parse_status(DEPRECATIONS)
    assert st["claude-haiku-4-5-20251001"] == canary.Status("Active", None, date(2026, 10, 15),
                                                            False)  # fmt: skip
    assert st["claude-sonnet-4-5-20250929"] == canary.Status("Deprecated", date(2026, 11, 30),
                                                             None, False)  # fmt: skip
    assert st["claude-mythos-preview"].announced
    assert len(st) == 22
    assert canary.parse_replacements(DEPRECATIONS) == {
        "claude-sonnet-4-5-20250929": "claude-sonnet-5-5"
    }
    assert canary.parse_lineup(OVERVIEW) == [
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-4-5-20251001",
    ]


def test_a_pin_that_gets_a_retirement_date_fails_until_models_lock_records_it(
    canary: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = DEPRECATIONS.replace(
        "| claude-haiku-4-5-20251001  | Active        | N/A                | Not sooner than"
        " October 15, 2026   |",
        "| claude-haiku-4-5-20251001  | Deprecated    | October 1, 2026    | December 1, 2026"
        "                   |",
    )
    page += (
        "\n| Retirement date | Deprecated model | Recommended replacement |\n| --- | --- | ---"
        " |\n| December 1, 2026 | `claude-haiku-4-5-20251001` | `claude-haiku-5` |\n"
    )
    rep = canary.check(page, OVERVIEW, TAGS)
    assert rep.failures == [
        "claude-haiku-4-5-20251001: state Deprecated on the page, Active in models.lock",
        "claude-haiku-4-5-20251001: retirement 2026-12-01 on the page, not set in models.lock",
        "claude-haiku-4-5-20251001: 'not sooner than' not set on the page, 2026-10-15 in"
        " models.lock",
    ]
    assert "claude-haiku-4-5-20251001: Anthropic recommends claude-haiku-5" in rep.notes
    life = claude_pins.lifecycle()
    life["claude-haiku-4-5-20251001"] = claude_pins.Lifecycle(
        "Deprecated", date(2026, 12, 1), None, "claude-haiku-5"
    )
    monkeypatch.setattr(claude_pins, "lifecycle", lambda: life)
    assert canary.check(page, OVERVIEW, TAGS).failures == []


def test_a_newer_model_in_a_pinned_family_is_a_note_not_a_failure(canary: ModuleType) -> None:
    rep = canary.check(DEPRECATIONS, OVERVIEW.replace("`claude-sonnet-5-5`", "`claude-sonnet-6`"),
                       TAGS)  # fmt: skip
    assert rep.failures == []
    assert "newer in a pinned family: claude-sonnet-6 (the overview's lineup)" in rep.notes


@pytest.mark.parametrize("which", ["deprecations", "columns", "overview"])
def test_a_page_that_changed_format_fails(canary: ModuleType, which: str) -> None:
    dep, ov = DEPRECATIONS, OVERVIEW
    if which == "deprecations":
        dep = dep.replace("## Model status", "## Model lifecycle")
    elif which == "columns":
        dep = dep.replace("Tentative retirement date", "Retires")
    else:
        ov = ov.replace("| Claude API ID ", "| Model ID ")
    with pytest.raises(canary.CanaryError):
        canary.check(dep, ov, TAGS)


def test_a_missing_pin_or_ollama_tag_fails(canary: ModuleType) -> None:
    dep = "\n".join(line for line in DEPRECATIONS.splitlines() if "claude-opus-5-5" not in line)
    rep = canary.check(dep, OVERVIEW, TAGS.replace('gemma4:12b"', 'gemma4:12x"'))
    assert rep.failures == [
        "claude-opus-5-5: not on the deprecations page's status table",
        "Ollama library: gemma4:12b isn't on its tags page (51 tags read)",
    ]


def test_main_reports_a_page_it_cant_read(canary: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:  # fmt: skip
    def fail(url: str) -> str:
        raise OSError(f"no network for {url}")

    summary = tmp_path / "summary.md"
    monkeypatch.setattr(canary, "fetch", fail)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert canary.main() == 1
    assert "- FAIL: can't read a page: no network for https://platform.claude.com/" in (
        summary.read_text("utf-8")
    )
