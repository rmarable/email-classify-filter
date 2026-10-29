"""The draft eval's phone pages (scripts/build_draft_rating_page.py; OD-580): the rating page's
answers mapped onto the rating fields (the safety ticks always answered, the rest only when the
draft wouldn't go as written, "signs as someone" by card kind), every mapped value on its scale,
and the embedded data unable to close the script."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ecf.eval import draftjudge
from ecf_server import draftcalibration

ROOT = Path(__file__).resolve().parents[1]
PLAIN = [f for f in draftcalibration.RATE_FIELDS if f != "impersonates"]


@pytest.fixture(scope="module")
def page() -> ModuleType:
    path = ROOT / "scripts" / "build_draft_rating_page.py"
    spec = importlib.util.spec_from_file_location("build_draft_rating_page", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_draft_rating_page"] = mod
    spec.loader.exec_module(mod)
    return mod


def _item(t: str = "aa", *, impersonation: bool = False) -> dict[str, Any]:
    fields = [*PLAIN, *(["impersonates"] if impersonation else [])]
    return {
        "t": t,
        "view": "Subject: hi",
        "question": "Q",
        "draft": "Hi [Your Name]",
        "identity": "Dana Whitfield" if impersonation else None,
        "fields": fields,
    }


def _on_scale(out: dict[str, Any]) -> None:
    for f, v in out.items():
        if f == "note":
            continue
        scale = draftjudge.SCALES.get(f) or draftcalibration.PAGE_ONLY[f]
        assert v in scale, (f, v)


def test_an_as_written_draft_with_none_of_these_passes_everything(page: ModuleType) -> None:
    out = page.to_rating(_item(), {"send": "as_written", "feel": "human", "none_of_these": True,
                                   "ticks": {"unanswered": True}})  # fmt: skip
    assert out["send"] == "as_written" and out["commits"] == "none"
    assert out["answers"] == "yes"  # an edited-only tick is ignored on an as-written draft
    assert set(PLAIN) <= set(out) and "impersonates" not in out
    _on_scale(out)


def test_the_safety_group_must_be_answered(page: ModuleType) -> None:
    assert page.to_rating(_item(), {"send": "as_written", "feel": "human"}) is None
    assert page.to_rating(_item(), {"send": "not_at_all", "feel": "human",
                                    "ticks": {"unanswered": True}}) is None  # fmt: skip
    assert page.to_rating(_item(), {"send": "as_written", "none_of_these": True}) is None


def test_none_of_these_with_a_safety_tick_is_refused(page: ModuleType) -> None:
    both = {"send": "as_written", "feel": "human", "none_of_these": True,
            "ticks": {"promises": True}}  # fmt: skip
    assert page.to_rating(_item(), both) is None


def test_the_one_tap_path_is_counted(page: ModuleType) -> None:
    data = {"items": [_item("aa"), _item("bb")]}
    saved = {"aa": {"answers": {"send": "as_written", "feel": "human", "none_of_these": True}},
             "bb": {"answers": {"send": "not_at_all", "feel": "human",
                                "ticks": {"promises": True}}}}  # fmt: skip
    assert page.one_taps(data, saved) == 1


def test_ticks_map_onto_the_fields(page: ModuleType) -> None:
    ticks = {"promises": True, "follows": True, "shares": True, "generic": True,
             "too_short": True, "writing": True}  # fmt: skip
    out = page.to_rating(_item(), {"send": "not_at_all", "feel": "machine", "ticks": ticks,
                                   "note": "  copies in a stranger  "})  # fmt: skip
    assert out["commits"] == "hard" and out["follows_or_shares"] == "yes"
    assert out["follows_or_shares_reason"] == "both" and out["specific"] == "no"
    assert out["length"] == "too_short" and out["writing_mistakes"] == "yes"
    assert out["note"] == "copies in a stranger" and out["feel"] == "machine"
    _on_scale(out)


def test_signs_as_someone_depends_on_the_card(page: ModuleType) -> None:
    """F4: impersonates on an impersonation card, invents_facts elsewhere."""
    a = {"send": "not_at_all", "feel": "human", "ticks": {"signs_as": True}}
    imp = page.to_rating(_item(impersonation=True), a)
    assert imp["impersonates"] == "yes" and imp["invents_facts"] == "no"
    plain = page.to_rating(_item(), a)
    assert plain["invents_facts"] == "yes" and "impersonates" not in plain


def test_convert_reads_the_db_shape_and_skips_unknown_tokens(page: ModuleType) -> None:
    data = {"items": [_item("aa"), _item("bb")]}
    done = {"send": "with_edits", "feel": "either", "none_of_these": True}
    saved: dict[str, Any] = {"aa": {"answers": done, "at": "2026-10-29T15:00:00Z"},
             "bb": {"answers": {"send": "with_edits"}}, "zz": {"answers": {}}}  # fmt: skip
    out = page.convert(data, saved)
    assert list(out) == ["aa"] and out["aa"]["send"] == "with_edits"


def _embedded(html: str) -> Any:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    assert m
    return json.loads(m.group(1))


def test_the_pages_embed_their_data_safely(page: ModuleType) -> None:
    view = "Subject: </script><script>alert(1)</script> __SET__ __DATA__"
    export = {"items": [_item() | {"view": view}], "rubric": "r"}
    html = page.build("rate", export, "label </script>")
    assert html.count("</script>") == 2 and "\0" not in html
    data = _embedded(html)
    assert data["items"][0]["view"] == view and data["mode"] == "rate"
    assert "fields" not in data["items"][0]  # the page needs only whether it's impersonation
    settle = {"items": [{"t": "cc", "view": "v", "question": "q", "identity": None,
                         "draft": "We will pay on Friday.", "hidden": None, "marks": [[0, 21]],
                         "fields": ["stated_fault_read"],
                         "scales": {"stated_fault_read": ["real", "stated_fault"]}}],
              "rubric": "r"}  # fmt: skip
    got = _embedded(page.build("settle", settle))
    assert got["items"][0]["marks"] == [[0, 21]] and got["mode"] == "settle"
    with pytest.raises(ValueError, match="mode"):
        page.build("other", export)
