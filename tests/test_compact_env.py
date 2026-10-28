"""The main session's requests over Haiku 5.5's 100,000-token price line (OD-564): counted per
request group in a Claude eval's figures, and named as a warning by `ecf eval compare` when the
main session or a compaction request had any (the compaction setting didn't take)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from ecf import cli
from ecf.eval.results import ResultFile
from ecf_server import claude_usage


def _calls(conn: sqlite3.Connection, rows: list[tuple[str, str, int, int, int]]) -> None:
    conn.executemany("INSERT INTO claude_calls (ts, session_id, model, source, input_tokens,"
                     " output_tokens, cache_read_tokens, cache_creation_tokens, duration_ms,"
                     " cost_usd) VALUES ('2026-10-27T00:00:01Z', 's', ?, ?, ?, 10, ?, ?, 1000,"
                     " 0.01)", rows)  # fmt: skip


def test_requests_over_100k_are_counted_per_group(conn: sqlite3.Connection) -> None:
    haiku, opus = "claude-haiku-5-5", "claude-opus-5-5"
    _calls(conn, [
        (haiku, "main", 1, 99_000, 999),  # 100,000 exactly: not over
        (haiku, "main", 1, 99_000, 1_000),  # cache reads and writes count
        (haiku, "main", 1, 30_000, 500),
        (opus, "agent", 1, 120_000, 0),
    ])  # fmt: skip
    report = claude_usage.run_report(conn, ["s"], datetime(2026, 10, 27, tzinfo=UTC))
    by = {(g["model"], g["source"]): g for g in report["groups"]}
    assert by[(haiku, "main")]["prompts_over_100k"] == 1
    assert by[(opus, "agent")]["prompts_over_100k"] == 1
    assert report["all"]["prompts_over_100k"] == 2


def _run(groups: list[dict[str, Any]]) -> ResultFile:
    return ResultFile(run_id="a" * 32, pair="claude/claude", set_version="v1",
                      created_at="2026-10-27T00:00:00Z", cases=[],
                      summary={"claude": {"preset": "C", "groups": groups}})  # fmt: skip


def _group(source: str, over: int | None) -> dict[str, Any]:
    g: dict[str, Any] = {"model": "claude-haiku-5-5", "source": source, "calls": 3,
                         "input_tokens": 3, "output_tokens": 30}  # fmt: skip
    return g if over is None else g | {"prompts_over_100k": over}


def test_compare_warns_for_session_requests_over_100k() -> None:
    figures = cli._claude_figures  # pyright: ignore[reportPrivateUsage]
    warned = figures(_run([_group("main", 2), _group("compact", 1)]), 1)
    for source, n in (("main", 2), ("compact", 1)):
        assert any(f"warning: {n} {source} requests over 100,000 tokens on claude-haiku-5-5"
                   in line for line in warned)  # fmt: skip
    for quiet in ([_group("main", 0)], [_group("agent", 5)], [_group("main", None)]):
        assert not any("warning" in line for line in figures(_run(quiet), 1))


def test_stats_prints_requests_over_100k(capsys: pytest.CaptureFixture[str]) -> None:
    from ecf import cli_stats  # noqa: PLC0415

    show = cli_stats._claude_figures  # pyright: ignore[reportPrivateUsage]
    g: dict[str, Any] = {"calls": 3, "input_tokens": 3, "output_tokens": 30, "cache_read_tokens": 0,
                         "cache_creation_tokens": 0, "seconds": {"median": 1.0, "p95": 1.0},
                         "api_equivalent_usd": None}  # fmt: skip
    show(g | {"prompts_over_100k": 4})
    assert "requests over 100,000 tokens: 4" in capsys.readouterr().out
    for quiet in (g, g | {"prompts_over_100k": 0}):  # older service, or none
        show(quiet)
        assert "100,000" not in capsys.readouterr().out
