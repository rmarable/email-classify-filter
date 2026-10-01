"""Local-model token and speed figures (V1.3 step 9; SPEC §13.4): stats.py, `ecf stats`, the daily
summary's line and `ecf eval compare`."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from typer.testing import CliRunner

from ecf import cli_stats
from ecf.cli import app
from ecf.eval.results import CaseResult, ResultFile
from ecf_server import daily, ollama, stats
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx

S = 1_000_000_000  # ns


def _call(**kw: Any) -> stats.Call:
    base: dict[str, Any] = {"ok": True, "prompt_tokens": 820, "cached_tokens": 520,
                            "output_tokens": 60, "prompt_ns": 3 * S, "eval_ns": 2 * S,
                            "load_ns": S // 10, "total_ns": 5 * S}  # fmt: skip
    return stats.Call(**(base | kw))


def test_percentiles_use_the_nearest_rank() -> None:
    assert stats.percentile([], 50) is None
    assert stats.percentile([5.0], 95) == 5.0
    values = [float(v) for v in range(1, 101)]
    assert (stats.percentile(values, 50), stats.percentile(values, 5),
            stats.percentile(values, 95)) == (50.0, 5.0, 95.0)  # fmt: skip


def test_a_summary_counts_tokens_speeds_and_cold_starts() -> None:
    calls = [_call(), _call(output_tokens=20, eval_ns=S, load_ns=3 * S), _call(ok=False)]
    f = stats.summarize(calls)
    assert (f["calls"], f["failed"], f["emails"]) == (3, 1, 2)
    assert (f["input_tokens"], f["cached_tokens"], f["output_tokens"]) == (1640, 1040, 80)
    assert f["tokens_per_email"] == 860  # (1640 + 80) / 2
    assert f["prompt_tps"] == {"median": 100.0, "slowest_5": 100.0}  # 300 uncached over 3 s
    assert f["generation_tps"] == {"median": 20.0, "slowest_5": 20.0}  # 30 and 20 tokens/s
    assert f["seconds"] == {"median": 5.0, "p95": 5.0}
    assert f["load_seconds"]["cold_starts"] == 1  # the 3-second load
    empty = stats.summarize([])
    assert empty["tokens_per_email"] is None and empty["generation_tps"]["median"] is None


def _record(conn: sqlite3.Connection, clock: FakeClock, role: str, *, address: str = "ap",
            outcome: str = "ok", digest: str = "sha256:aaa") -> None:  # fmt: skip
    m = ollama.Metrics(820, 520, 60, 3 * S, 2 * S, S // 10, 5 * S)
    ollama.record_call(conn, clock, role=role, outcome=outcome, digest=digest,  # type: ignore[arg-type]
                       metrics=m if outcome == "ok" else None, address_id=address, preset="A",
                       stage="shadow")  # fmt: skip


def test_the_report_groups_by_model_and_role_and_filters(conn: sqlite3.Connection,
                                                         clock: FakeClock) -> None:  # fmt: skip
    for _ in range(3):
        _record(conn, clock, "classifier")
    _record(conn, clock, "actor")
    _record(conn, clock, "classifier", outcome="timeout")
    _record(conn, clock, "classifier", address="billing")
    _record(conn, clock, "classifier", digest="sha256:bbb")
    since = clock.now() - timedelta(days=1)
    r = stats.report(conn, since)
    groups = {(g["model"], g["role"]): g for g in r["groups"]}
    assert set(groups) == {("sha256:aaa", "classifier"), ("sha256:aaa", "actor"),
                           ("sha256:bbb", "classifier")}  # fmt: skip
    assert groups[("sha256:aaa", "classifier")]["failed"] == 1
    assert r["all"]["emails"] == 5 and r["all"]["calls"] == 7
    assert r["all"]["tokens_per_email"] == 1056  # 6 calls of 880 tokens over 5 emails
    assert stats.report(conn, since, address="billing")["all"]["calls"] == 1
    assert stats.report(conn, since, preset="B")["all"]["calls"] == 0
    assert stats.report(conn, clock.now() + timedelta(seconds=1))["all"]["calls"] == 0


def test_the_daily_summary_gets_one_line(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert stats.daily_line(conn, clock.now() - timedelta(days=1)) is None
    _record(conn, clock, "classifier")
    _record(conn, clock, "classifier", outcome="error")
    line = stats.daily_line(conn, clock.now() - timedelta(days=1))
    assert line == (
        "Local model: 2 calls for 1 email(s) (1 failed), about 880 tokens per email,"
        " 30 tokens/s (slowest 5%: 30), 5.0 s per call (p95 5.0) (ecf stats)"
    )
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'A', ?)",
                     (to_ts(clock.now()),))  # fmt: skip
    assert line in daily.card(conn, clock.now(), "2026-10-01").text.splitlines()


def test_the_route_and_the_cli_screen(conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
                                      capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    from ecf_server.api import ServiceState, create_app  # noqa: PLC0415

    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'A', ?)",
                     (to_ts(clock.now()),))  # fmt: skip
    _record(conn, clock, "classifier")
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def get(path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.get(path, headers={"Authorization": "Bearer tok"})

    r = anyio.run(get, "/v1/stats?hours=24&address=ap@acme.example")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["all"]["calls"] == 1 and body["groups"][0]["role"] == "classifier"
    assert anyio.run(get, "/v1/stats?hours=0").status_code == 400
    assert anyio.run(get, "/v1/stats?hours=soon").status_code == 400
    cli_stats.show(body)
    out = capsys.readouterr().out
    assert "classifier (model sha256:aaa)" in out and "  calls: 1" in out
    assert "  writing: 30.0 tokens/s median, slowest 5% 30.0" in out
    assert "  model load: 0.10 s median, 0.10 s p95, 0 cold start(s)" in out
    cli_stats.show(body | {"all": body["all"] | {"calls": 0}})
    assert "wasn't used in this period" in capsys.readouterr().out


def test_since_takes_hours_or_days() -> None:
    assert cli_stats.hours_of("24h") == 24 and cli_stats.hours_of("7d") == 168
    assert cli_stats.hours_of("1.5d") == 36
    import typer  # noqa: PLC0415

    with pytest.raises(typer.BadParameter):
        cli_stats.hours_of("a week")


def test_eval_compare_prints_each_runs_figures(tmp_path: Path) -> None:
    figures = stats.summarize([_call(), _call()])

    def run(name: str, model: dict[str, Any] | None) -> Path:
        r = ResultFile(run_id=name, pair="p", set_version="v", created_at="t",
                       cases=[CaseResult(id="c", correct=True)],
                       summary={"model": model} if model else None)  # fmt: skip
        path = tmp_path / f"{name}.json"
        path.write_text(r.model_dump_json())
        return path

    out = CliRunner().invoke(app, ["eval", "compare", str(run("a", None)), str(run("b", figures))])
    assert out.exit_code == 0, out.output
    assert "A  model:" not in out.output  # an older result without figures
    assert (
        "B  model: 2 calls, about 880 tokens per email; writing 30.0 tokens/s median"
        " (slowest 5% 30.0); 5.0 s per call median, 5.0 s p95"
    ) in out.output
    assert json.loads(run("c", figures).read_text())["summary"]["model"]["calls"] == 2
