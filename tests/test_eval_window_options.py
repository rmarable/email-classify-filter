"""Run options for classifier window tuning (step 2, branch 2): `--classifier-window N` and
`--cases` on a Claude eval, both never for the gate; the C summary's `excerpt_cap`; the root
guard that keeps a for-gate run from re-pointing the gate at another set; and the CLI's check
that the service took the options."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from typer.testing import CliRunner

from ecf import cli
from ecf.errors import InvalidInputError, NotFoundError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import load_result
from ecf_server import claude_eval, evalrun, message, ruletest, slack_admin, triggers
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from tests import test_claude_eval as ce
from tests.test_eval_window import LONG


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Two starter cards and a long card whose signal and injection sit past 1,500."""
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md"):
        shutil.copy(ce.SYNTHETIC / "cases" / name, r / "cases" / name)
    (r / "cases" / "long-signal.md").write_text(LONG)
    assert not build_all(r).findings
    for cid in ("starter-bec", "starter-control", "long-signal"):
        labels.confirm(r, cid, date(2026, 10, 1))
    return r


@pytest.fixture(autouse=True)
def _reset(conn: sqlite3.Connection) -> None:
    del conn
    claude_eval.reset()


def _remembered(conn: sqlite3.Connection) -> str | None:
    return slack_admin.setting(conn, evalrun.EVAL_ROOT)


def _by_case(cid: str) -> dict[str, Any]:
    return ce.REQUEST if cid == "starter-control" else ce.BEC


def _result(conn: sqlite3.Connection) -> Any:
    return load_result(Path(conn.execute("SELECT path FROM eval_runs").fetchone()["path"]))


# The window: cut where asked, the actor's excerpt untouched.


def test_the_classifier_excerpt_takes_a_limit() -> None:
    text = " ".join(f"word{i}" for i in range(2000))
    raw = (b"From: a@vendor-a.example\r\nTo: b@acme.example\r\nSubject: s\r\n"
           b"Content-Type: text/plain\r\n\r\n" + text.encode())  # fmt: skip
    parsed = ruletest.read_case(raw).parsed
    (cls, act), whole = parsed.excerpts_and_text(triggers.redact_injection)
    (cls15, act15), whole15 = parsed.excerpts_and_text(triggers.redact_injection, limit=1500)
    assert len(cls15) <= 1500 < len(cls) <= message.CLASSIFIER_CHARS
    assert act == act15 and whole == whole15
    (cls8, act8), _ = parsed.excerpts_and_text(triggers.redact_injection, limit=8000)
    assert len(cls8) > 4000 and act8 == act  # the actor keeps ACTOR_CHARS


def test_case_inputs_measure_against_the_window_used(root: Path) -> None:
    cases, _ = evalrun.load(root, fraud_only=False)
    long = next(c for c in cases if c.id == "long-signal")
    scratch = ruletest.Scratch(FakeClock())
    try:
        _, texts, m = evalrun.case_inputs_full(long, scratch, triggers.redact_injection,
                                               limit=8000)  # fmt: skip
    finally:
        scratch.close()
    assert "please settle" in texts[0]
    assert m["signal_seen"] is True and m["injection_seen"] is True


def test_a_stored_excerpt_refuses_another_window(root: Path) -> None:
    cases, _ = evalrun.load(root, fraud_only=False)
    stored = replace(cases[0], facts={}, excerpts=("a", "b"))
    scratch = ruletest.Scratch(FakeClock())
    try:
        assert evalrun.case_inputs_full(stored, scratch, triggers.redact_injection)[1] == ("a", "b")
        with pytest.raises(InvalidInputError, match="stored excerpt"):
            evalrun.case_inputs_full(stored, scratch, triggers.redact_injection, limit=1500)
    finally:
        scratch.close()


# Starting a run: the window and the case selector are experiments.


def test_a_window_run_is_never_for_the_gate_and_records_its_cap(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = ce.start(db_path, clock, root, classifier_window=8000)
    assert r["pinned"] is False and r["for_gate"] is False
    assert r["classifier_window"] == 8000 and r["cases"] == []
    assert not _remembered(conn)
    ce.drain(db_path, clock, _by_case)
    result = _result(conn)
    s = result.summary
    assert s["excerpt_cap"] == {"classifier_chars": 8000, "actor_chars": message.ACTOR_CHARS}
    assert s["options"]["classifier_window"] == 8000 and s["options"]["for_gate"] is False
    assert s["gate_passed"] is False and str(result.digest).startswith("eval-")
    long = {c.id: c for c in result.cases}["long-signal"]
    assert long.signal_seen is True and long.injection_seen is True


def test_a_default_run_echoes_the_window_and_records_the_cap(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = ce.start(db_path, clock, root)
    assert r["classifier_window"] == message.CLASSIFIER_CHARS and r["cases"] == []
    assert r["pinned"] is True and "for_gate" not in r
    ce.drain(db_path, clock, _by_case)
    result = _result(conn)
    s = result.summary
    long = {c.id: c for c in result.cases}["long-signal"]
    assert long.signal_seen is False and long.injection_seen is False  # 8,000 sees both
    assert s["excerpt_cap"] == {"classifier_chars": message.CLASSIFIER_CHARS,
                                "actor_chars": message.ACTOR_CHARS}  # fmt: skip
    assert "classifier_window" not in s["options"] and "cases" not in s["options"]


@pytest.mark.parametrize(("kw", "match"), [
    ({"classifier_window": 499}, "between 500 and 8000"),
    ({"classifier_window": 8001}, "between 500 and 8000"),
    ({"classifier_window": 1500, "preset": "D"}, "preset C"),
    ({"classifier_window": 1500, "source_run": "abcdefgh"}, "source run"),
    ({"cases": ("nothing-*",)}, "no case matches"),
    ({"cases": ("long-*", "typo-*")}, "no case matches --cases typo-\\*$"),
])  # fmt: skip
def test_refusals(db_path: Path, clock: FakeClock, root: Path, kw: dict[str, Any],
                  match: str) -> None:  # fmt: skip
    with pytest.raises(InvalidInputError, match=match):
        ce.start(db_path, clock, root, **kw)
    assert claude_eval.current() is None


def test_cases_runs_only_the_matching_cards_and_never_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = ce.start(db_path, clock, root, cases=("long-*", "starter-control"))
    assert r["total"] == 2 and r["for_gate"] is False and r["pinned"] is False
    assert r["cases"] == ["long-*", "starter-control"]
    assert not _remembered(conn)
    ce.drain(db_path, clock, _by_case)
    result = _result(conn)
    assert sorted(c.id for c in result.cases) == ["long-signal", "starter-control"]
    assert result.set_version == evalrun.set_version(root)  # the whole set's version
    assert result.summary["options"]["cases"] == ["long-*", "starter-control"]


def test_a_rescore_keeps_the_window_and_the_cases(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    ce.start(db_path, clock, root, classifier_window=1500, cases=("long-*",))
    ce.drain(db_path, clock, _by_case)
    path = Path(conn.execute("SELECT path FROM eval_runs").fetchone()["path"])
    out = evalrun.rescore_synthetic(root, path, clock)
    again: dict[str, Any] = dict(load_result(Path(out["path"])).summary or {})
    assert again["options"]["classifier_window"] == 1500
    assert again["options"]["cases"] == ["long-*"] and again["options"]["for_gate"] is False
    assert again["excerpt_cap"]["classifier_chars"] == 1500


# The root guard: a for-gate run never re-points the gate while its set is still there.


def test_a_for_gate_run_on_another_root_is_refused_while_the_old_set_exists(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    shutil.copytree(root, other)
    ce.start(db_path, clock, root)
    assert _remembered(conn) == str(root.resolve())
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="go-live gate's set"):
        ce.start(db_path, clock, other)
    assert claude_eval.current() is None and _remembered(conn) == str(root.resolve())
    r = ce.start(db_path, clock, other, for_gate=False)  # an experiment may run there
    assert r["for_gate"] is False and _remembered(conn) == str(root.resolve())
    claude_eval.reset()
    ce.start(db_path, clock, root)  # the same set again: allowed
    claude_eval.reset()
    (root / "labels.jsonl").unlink()  # the old set gone: the gate may move
    ce.start(db_path, clock, other)
    assert _remembered(conn) == str(other.resolve())


# The route and the CLI.


def test_the_route_takes_the_window_and_the_cases(db_path: Path, clock: FakeClock,
                                                  root: Path) -> None:  # fmt: skip
    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def go() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as h:
            cli_h = {"Authorization": "Bearer cli-token"}
            body = {"root": str(root), "preset": "C", "sensitivity": "standard"}
            out = [await h.post("/v1/eval/claude", headers=cli_h, json=body | extra)
                   for extra in ({"classifier_window": "1500"}, {"classifier_window": True},
                                 {"cases": "long-*"}, {"cases": [3]})]  # fmt: skip
            out.append(
                await h.post(
                    "/v1/eval/claude",
                    headers=cli_h,
                    json=body | {"classifier_window": 1500, "cases": ["long-*"]},
                )
            )
            return out

    *bad, ok = anyio.run(go)
    assert [b.status_code for b in bad] == [400, 400, 400, 400]
    assert "classifier_window must be a whole number" in bad[0].text
    assert "cases must be a list of text" in bad[2].text
    started = ok.json()
    assert started["classifier_window"] == 1500 and started["cases"] == ["long-*"]
    assert started["total"] == 1 and started["for_gate"] is False


class FakeClient:
    def __init__(self, reply: dict[str, Any], *, local: str = "idle",
                 stop_fails: bool = False) -> None:  # fmt: skip
        self.reply, self.local, self.stop_fails = reply, local, stop_fails
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        del method
        self.calls.append((path, body))
        if path == "/v1/eval/runs/stop" and self.stop_fails:
            raise NotFoundError("no eval is running")
        return self.reply if path == "/v1/eval/claude" else {"claude": {"state": "stopped"}}

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append((path, None))
        return {"current": {"state": self.local}, "recent": [], "claude": None}


REPLY: dict[str, Any] = {"run_id": "f" * 32, "models": {"classifier": "m"}, "pinned": False,
                         "for_gate": False, "preset": "C", "sensitivity": "standard",
                         "total": 1}  # fmt: skip


def _invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reply: dict[str, Any],
            *args: str, **kw: Any) -> tuple[Any, FakeClient]:  # fmt: skip
    client = FakeClient(reply, **kw)

    def make(_paths: object) -> FakeClient:
        return client

    monkeypatch.setattr(cli, "LocalClient", make)
    r = CliRunner().invoke(cli.app, ["eval", "run", "--root", str(tmp_path), "--copy-to",
                                     str(tmp_path / "copy"), *args])  # fmt: skip
    return r, client


def test_the_cli_sends_the_options_and_checks_the_echo(monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path) -> None:  # fmt: skip
    echoed = REPLY | {"classifier_window": 1500, "cases": ["long-*"]}
    r, client = _invoke(monkeypatch, tmp_path, echoed, "--claude", "--classifier-window",
                        "1500", "--cases", "long-*")  # fmt: skip
    assert r.exit_code == 0, r.output
    body = client.calls[0][1] or {}
    assert body["classifier_window"] == 1500 and body["cases"] == ["long-*"]
    # sent as an experiment, so a service that ignores the options never pins it or moves the
    # gate's set before it is stopped (W2)
    assert body["for_gate"] is False
    assert "classifier window 1500" in r.output
    # a service on older code ignores the options: the CLI stops the run it registered
    r, client = _invoke(monkeypatch, tmp_path, REPLY, "--claude", "--classifier-window", "1500")
    assert isinstance(r.exception, InvalidInputError) and "didn't take" in str(r.exception)
    assert "was stopped" in str(r.exception)
    assert [p for p, _ in client.calls] == ["/v1/eval/claude", "/v1/eval/runs",
                                            "/v1/eval/runs/stop"]  # fmt: skip
    r, client = _invoke(monkeypatch, tmp_path, REPLY | {"classifier_window": 4000},
                        "--claude", "--cases", "long-*")  # fmt: skip
    assert isinstance(r.exception, InvalidInputError) and "didn't take" in str(r.exception)
    # a local eval running: the stop would end it too, so the CLI leaves both (W1)
    r, client = _invoke(monkeypatch, tmp_path, REPLY, "--claude", "--cases", "long-*",
                        local="running")  # fmt: skip
    assert isinstance(r.exception, InvalidInputError) and "ecf eval stop" in str(r.exception)
    assert "/v1/eval/runs/stop" not in [p for p, _ in client.calls]
    # the stop failing still explains why (W7)
    r, client = _invoke(monkeypatch, tmp_path, REPLY, "--claude", "--cases", "long-*",
                        stop_fails=True)  # fmt: skip
    assert isinstance(r.exception, InvalidInputError) and "didn't take" in str(r.exception)
    assert "couldn't be stopped" in str(r.exception)
    assert "no eval is running" in str(r.exception)  # the stop's own message kept (X6)
    # without either option, or with the default window, an older service is fine (W11)
    for args in ((), ("--classifier-window", "4000")):
        r, client = _invoke(monkeypatch, tmp_path, REPLY, "--claude", *args)
        assert r.exit_code == 0, r.output
        assert len(client.calls) == 1 and (client.calls[0][1] or {})["for_gate"] is True


def test_an_empty_cases_option_is_refused(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:  # fmt: skip
    for value in ("", ",", " , "):
        r, client = _invoke(monkeypatch, tmp_path, REPLY, "--claude", "--cases", value)
        assert isinstance(r.exception, InvalidInputError) and client.calls == []
        assert "at least one pattern" in str(r.exception)


def test_the_options_need_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for args in (("--classifier-window", "1500"), ("--cases", "long-*")):
        r, client = _invoke(monkeypatch, tmp_path, REPLY, *args)
        assert isinstance(r.exception, InvalidInputError) and client.calls == []
        assert "with --claude" in str(r.exception)


def test_status_snapshot_echo_is_json_ready(db_path: Path, clock: FakeClock, root: Path) -> None:
    r = ce.start(db_path, clock, root, cases=("long-*",))
    assert json.loads(json.dumps(r))["cases"] == ["long-*"]


def test_cases_with_fraud_only_keeps_only_matching_fraud_cards(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """`--fraud-only` drops benign cards first (plan J9), then `--cases` picks among the rest."""
    r = ce.start(db_path, clock, root, fraud_only=True, cases=("long-*", "starter-*"))
    run = claude_eval.current()
    assert run is not None and r["for_gate"] is False
    assert "starter-control" not in {w.case.id for w in run.works}
    assert "long-signal" in {w.case.id for w in run.works}
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="among the fraud cards"):  # dropped first
        ce.start(db_path, clock, root, fraud_only=True, cases=("starter-control",))


def test_an_experiments_key_binds_its_window_and_cases(conn: sqlite3.Connection,
                                                        root: Path) -> None:  # fmt: skip
    def key(**kw: Any) -> str:
        return claude_eval.models_for(conn, claude_eval.Options(root, for_gate=False, **kw))[2]

    keys = {key(), key(classifier_window=1500), key(classifier_window=8000), key(cases=("long-*",))}
    assert len(keys) == 4 and all(k.startswith("eval-") for k in keys)
    pinned = claude_eval.models_for(conn, claude_eval.Options(root))
    assert pinned[1] is True and not pinned[2].startswith("eval-")  # the pin key unchanged


def test_an_experiment_is_never_a_source_run(conn: sqlite3.Connection, db_path: Path,
                                             clock: FakeClock, root: Path) -> None:  # fmt: skip
    ce.start(db_path, clock, root, classifier_window=1500)
    ce.drain(db_path, clock, _by_case)
    rid = str(conn.execute("SELECT run_id FROM eval_runs").fetchone()["run_id"])
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="experiment"):
        ce.start(db_path, clock, root, source_run=rid)


def test_the_root_guard_follows_symlinks_and_covers_preset_d(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path, tmp_path: Path
) -> None:
    ce.start(db_path, clock, root)
    claude_eval.reset()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    ce.start(db_path, clock, alias)  # the same set by another name: allowed
    assert _remembered(conn) == str(root.resolve())
    claude_eval.reset()
    other = tmp_path / "other"
    shutil.copytree(root, other)
    with pytest.raises(InvalidInputError, match="go-live gate's set"):
        ce.start(db_path, clock, other, preset="D")


def test_compare_and_status_tell_windows_apart(conn: sqlite3.Connection, db_path: Path,
                                               clock: FakeClock, root: Path) -> None:  # fmt: skip
    from ecf.eval.results import input_warnings  # noqa: PLC0415

    ce.start(db_path, clock, root, classifier_window=8000, cases=("long-*",))
    ce.drain(db_path, clock, _by_case)
    wide = _result(conn)
    summary = dict(wide.summary or {})
    narrow = wide.model_copy(update={"summary": summary | {"excerpt_cap": {
        "classifier_chars": 1500, "actor_chars": 4000}}})  # fmt: skip
    assert ("warning: different inputs (excerpt_cap classifier_chars 8000 vs 1500)"
            in input_warnings(wide, narrow))  # fmt: skip
    figures = cli._claude_figures(wide, 1)  # pyright: ignore[reportPrivateUsage]
    assert "classifier window 8000 (actor 4000), cases long-*" in figures[0]
    st = {"current": {"state": "idle"}, "claude": None, "recent": [{
        "created_at": "2026-10-24T10:00:00Z", "run_id": "abcdef12", "gate_passed": False,
        "metrics": summary}]}  # fmt: skip
    assert any("classifier window 8000, cases long-*" in line for line in cli.status_lines(st))
    snap = ce.start(db_path, clock, root, classifier_window=1500)
    claude_st: dict[str, Any] = {"current": {"state": "idle"}, "recent": [], "claude": snap}
    assert any("classifier window 1500" in line for line in cli.status_lines(claude_st))


def test_the_cli_window_default_is_the_services() -> None:
    assert cli.CLASSIFIER_WINDOW == message.CLASSIFIER_CHARS
