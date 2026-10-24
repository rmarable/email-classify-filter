"""The settle phone page builder (scripts/build_settle_page.py; eval plan, Judges; OD-547): the
export's data goes into the page whole, nothing in it or in the set label can close the script or
be substituted, and the page reads only keys `ecf eval settle --export` writes."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_eval_agree import _one_dispute  # pyright: ignore[reportPrivateUsage]

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    path = ROOT / "scripts" / "build_settle_page.py"
    spec = importlib.util.spec_from_file_location("build_settle_page", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_settle_page"] = mod
    spec.loader.exec_module(mod)
    return mod


def _data(view: str = "Subject: hello") -> dict[str, Any]:
    return {"items": [{"view": view, "t": "0a1b2c3d", "judges": {"phishing": [True, False]},
                       "fields": [{"id": "phishing", "kind": "boolean", "values": [],
                                   "help": "h"}]}],
            "signs": "s", "phishing": "p"}  # fmt: skip


def _embedded(html: str) -> tuple[Any, str]:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    s = re.search(r"const SET = (.*);\n", html)
    assert m and s
    return json.loads(m.group(1)), json.loads(s.group(1))


def test_the_data_and_label_go_in_whole_and_cannot_close_the_script(builder: ModuleType) -> None:
    view = "Subject: </script><script>alert(1)</script> __SET__ __DATA__"
    label = "set </script> __DATA__ __SET__"
    html = builder.build(_data(view), label)
    assert 'id="data">__DATA__<' not in html and "const SET = __SET__;" not in html
    assert "\0" not in html
    assert html.count("</script>") == 2  # the data block's and the page script's own
    data, set_label = _embedded(html)
    assert data["items"][0]["view"] == view and set_label == label
    assert sorted(data["items"][0]["judges"]["phishing"]) == [False, True]


def test_build_leaves_its_input_alone(builder: ModuleType) -> None:
    data = _data()
    data["items"][0]["judges"]["phishing"] = list(range(50))
    builder.build(data)
    assert data["items"][0]["judges"]["phishing"] == list(range(50))


def test_no_items_still_builds(builder: ModuleType) -> None:
    data, _ = _embedded(builder.build({"items": [], "signs": "s", "phishing": "p"}))
    assert data["items"] == []


def test_the_page_reads_only_what_the_export_writes(builder: ModuleType, tmp_path: Path) -> None:
    from ecf.eval import settle as st  # noqa: PLC0415

    root, _a = _one_dispute(tmp_path)
    page, _key = st.export(root, tmp_path / "page")
    exported = json.loads(page.read_text(encoding="utf-8"))
    assert set(exported) == {"items", "signs", "phishing"}
    for it in exported["items"]:
        assert set(it) <= {"view", "fields", "judges", "t", "note"}
        for f in it["fields"]:
            assert set(f) == {"id", "kind", "values", "help"} and f["kind"] in ("boolean", "choice")
    out = tmp_path / "settle.html"
    assert builder.main([str(tmp_path / "page"), str(out), "DEV"]) == 0
    data, _ = _embedded(out.read_text(encoding="utf-8"))
    assert [i["t"] for i in data["items"]] == [i["t"] for i in exported["items"]]
    assert builder.main([str(page), str(out)]) == 0  # the file itself, not its folder
    # and the page's script asks for nothing more (reads written as `DATA.x`, `it.x` or `f.x`;
    # bracket access, destructuring or another variable name would not be seen)
    for name, keys in (("DATA", {"items", "signs", "phishing"}),
                       ("it", {"view", "fields", "judges", "t", "note"}),
                       ("f", {"id", "kind", "values", "help"})):  # fmt: skip
        assert set(re.findall(rf"\b{name}\.(\w+)", builder.PAGE)) <= keys, name


def test_wrong_arguments_print_the_usage(
    builder: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert builder.main([]) == 2
    assert "Usage" in capsys.readouterr().err
    assert builder.main(["/nonexistent-export", "out.html"]) == 2
    assert "no settle-page.json" in capsys.readouterr().err
