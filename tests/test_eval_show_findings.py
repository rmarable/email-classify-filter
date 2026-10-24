"""`ecf eval generate --show-findings` (hygiene fix Stage A): a DEV run prints each hygiene
finding's text where the operator can see it, and writes nothing it wouldn't write without the
switch (P27: logs hold finding kinds, never text)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf import cli_evalgen
from ecf.cli import app
from ecf.eval import generate as gen
from ecf.eval import mix as mixmod
from ecf.eval import providers, tools
from ecf.eval import report as rep
from ecf.text import escaped
from tests.test_eval_generate import fake_tool

VOLATILE_ROW = {"seconds", "at"}
VOLATILE_MANIFEST = {"seconds", "at", "written_at", "cases"}


def _run(tmp_path: Path, scenario: dict[str, Any], show: list[str] | None) -> Path:
    tool, environ = fake_tool(tmp_path, scenario)
    root = tmp_path / "set"
    gen.start(root, resume=False)
    adapter = tools.CliAdapter(tool, "fake-model-1", environ)
    adapter.preflight(tmp_path)
    gen.Run(root, mixmod.default(), 20, adapter, "dev",
            show=None if show is None else show.append).run(resume=False)  # fmt: skip
    return root


def _rows(root: Path, log: str) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k not in VOLATILE_ROW} for r in gen.Work(root).read(log)]


def _manifest(root: Path) -> dict[str, Any]:
    return {k: v for k, v in gen.read_manifest(root).items() if k not in VOLATILE_MANIFEST}


@pytest.mark.parametrize("scenario", ["hygiene_once", "hygiene_always"])
def test_the_switch_prints_values_and_writes_nothing_new(tmp_path: Path, scenario: str) -> None:
    sc = {scenario: True, "subject": True}
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    shown: list[str] = []
    with_ = _run(tmp_path / "with", sc, shown)
    without = _run(tmp_path / "without", sc, None)
    # one line per finding: seed, group, delivery, pass, where, kind and value
    assert any(" first " in x and ":subject domain: billing-desk.ch" in x for x in shown)
    assert any(":body phone number: 212-555-1234" in x for x in shown)
    assert any(" retry " in x for x in shown) == (scenario == "hygiene_always")
    seed, group, delivery, stage, where = shown[0].split()[:5]
    assert re.fullmatch(r"[a-z0-9-]+-s\d{3}", seed) and seed.startswith(group + "-")
    assert group in mixmod.default().groups or group in mixmod.default().ordinary
    assert delivery in {*mixmod.default().delivery, "-"} and stage == "first"
    assert where.startswith(f"dev-{group}-")
    # the set is the same with or without the switch, apart from times and hashes
    for log in ("calls", "outcomes", "seeds", "proposals"):
        assert _rows(with_, log) == _rows(without, log), log
    assert _manifest(with_) == _manifest(without)
    # and no printed value is in any file of the set or in the report
    files = "\n".join(p.read_text(errors="replace") for p in with_.rglob("*") if p.is_file())
    files += "\n".join(rep.report(with_, []))
    for value in ("billing-desk.ch", "212-555-1234"):
        assert value not in files


def test_a_printed_value_never_carries_a_control_character(tmp_path: Path) -> None:
    shown: list[str] = []
    _run(tmp_path, {"hygiene_once": True, "escape": True}, shown)
    url = [x for x in shown if "URL: https://evil" in x]
    assert url and all("\\u001b[31m" in x for x in url)
    assert any("domain: 31m.com" in x for x in shown)  # `[` lets the host after it match too
    assert not any("\x1b" in x for x in shown)


def test_a_hygiene_error_names_the_field_and_kind_never_the_value(tmp_path: Path) -> None:
    from ecf.cli_card_review import rebuild  # noqa: PLC0415
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "t-1.md").write_text('---\nid: t-1\ntitle: test\nfrom: "A <a@vendor-a.example>"\n'
                                  'subject: "Hello"\n---\nCall 212-555-1234 today\n')  # fmt: skip
    with pytest.raises(InvalidInputError) as exc:
        rebuild(tmp_path)
    assert "t-1.md:body: phone number" in str(exc.value) and "212-555" not in str(exc.value)


def test_escaped_shows_every_control_format_and_separator_character() -> None:
    assert escaped("a\x1bb\u202ec\u2028d\ud800e\U000f0000\n") == (
        "a\\u001bb\\u202ec\\u2028d\\ud800e\\U000f0000\\u000a")  # fmt: skip
    assert escaped("plain café text") == "plain café text"


def _generate(tmp_path: Path, *extra: str) -> Any:
    return CliRunner().invoke(app, ["eval", "generate", "--root", str(tmp_path / "s"),
                                    "--show-findings", "--yes", *extra])  # fmt: skip


def _no_adapter(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("an adapter was resolved")


def test_the_switch_is_for_dev_runs_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_evalgen, "_terminals", lambda: True)
    monkeypatch.setattr(providers, "resolve", _no_adapter)
    r = _generate(tmp_path)
    assert r.exit_code != 0 and "DEV run only" in str(r.exception)


def test_the_switch_needs_a_terminal_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(providers, "resolve", _no_adapter)
    r = _generate(tmp_path, "--dev")  # CliRunner's streams are no terminal
    assert r.exit_code != 0 and "run it in Terminal" in str(r.exception)
    assert not (tmp_path / "s").exists()


def test_with_a_terminal_the_switch_goes_on_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Reached(Exception):
        pass

    def resolve(*_a: Any, **_k: Any) -> Any:
        raise Reached

    monkeypatch.setattr(cli_evalgen, "_terminals", lambda: True)
    monkeypatch.setattr(providers, "resolve", resolve)
    r = _generate(tmp_path, "--dev")
    assert isinstance(r.exception, Reached)


def test_the_dev_check_runs_on_the_resume_path_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_evalgen, "_terminals", lambda: True)
    monkeypatch.setattr(providers, "resolve", _no_adapter)
    r = _generate(tmp_path, "--resume")
    assert r.exit_code != 0 and "DEV run only" in str(r.exception)
