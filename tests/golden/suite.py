# ruff: noqa: E501 - the test IDs in UNSTABLE are long
"""Goldens from v1's own tests (planner step 2, D117): each test's database commits, recorded.

A pytest plugin, off unless asked for. With `-p tests.golden.suite`:

    --golden-suite=record   write each test's write-set hash to tests/golden/suite.tsv
    --golden-suite=check    fail a test whose write-sets differ from its recorded hash
    --golden-suite=dump --golden-dump=DIR   write each test's write-sets as JSON, to diff
                            against the same dump taken on main

Run over the files in suite_files.txt (the tests of the planner's modules):

    PYTHONPATH=. uv run pytest -p tests.golden.suite --golden-suite=check -n 4 \
        $(cat tests/golden/suite_files.txt)

A test's write-sets are the row diffs of every COMMIT on every database it opens, in order (the
harness's dump and diff). Random ids, step-up codes, tokens, check holders, UUIDs and the fake
come from counters, so a test records the same hash every run. Rows the
migrations stamp with the wall clock or a random value are left out. UNSTABLE lists the tests
that still differ between runs, and SKIPPED the ones the recorder can't run unchanged, with why;
neither is checked.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Generator, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

from ecf_server.mail import fake
from tests.golden import harness as H

HERE = Path(__file__).parent
ARRIVED = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
HASHES = HERE / "suite.tsv"
PARTS = HERE / ".suite-parts"
# tests whose write-sets still differ between two identical runs, with the reason
_DOVECOT = "a real IMAP server (Dovecot): its UIDs and timing"
_MIGRATION = "migration 0041's audit rows stamped by the real clock"
_ORDER = "varies only when run beside other tests"
_THREADS = "the service's threads: write order varies"
_SLACK_ADMIN = "Slack app admin (out of scope): rows stamped by the real clock"
UNSTABLE: dict[str, str] = {
    "tests/test_checks.py::test_a_check_against_dovecot": _DOVECOT,
    "tests/test_fetch.py::test_oversized_path_against_dovecot": _DOVECOT,
    "tests/test_mailbox_actions.py::test_archive_and_undo_on_a_real_server": _DOVECOT,
    "tests/test_precheck.py::test_live_actions_against_dovecot": _DOVECOT,
    "tests/test_claude_pins.py::test_0041_removes_a_stored_sonnet_override[stored0-None]": _MIGRATION,
    "tests/test_claude_pins.py::test_0041_removes_a_stored_sonnet_override[stored1-left1]": _MIGRATION,
    "tests/test_escalations.py::test_cards_render_through_the_real_sender": _ORDER,
    "tests/test_retention.py::test_the_daily_job_deletes_old_finished_items_and_keeps_the_rest": _ORDER,
    "tests/test_schedule.py::test_a_crashing_check_is_recorded_and_rescheduled": _THREADS,
    "tests/test_schedule.py::test_service_tick_and_worker_run_a_check": _THREADS,
    "tests/test_schedule.py::test_timer_work_that_keeps_failing_is_counted_shown_and_told_once": _THREADS,
    "tests/test_slack_admin.py::test_install_route_stores_tokens_and_reconnects": _SLACK_ADMIN,
    "tests/test_slack_admin.py::test_real_sdk_traffic_leaves_no_token_or_payload_in_the_log": _SLACK_ADMIN,
    "tests/test_slack_admin.py::test_reauthorize_updates_the_manifest_then_edits_or_reposts_every_card": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_a_bot_token_slack_no_longer_accepts_counts_as_revoked": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_a_config_token_deletes_the_app": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_a_pending_app_is_removed_too": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_a_refused_revoke_is_reported_and_the_run_goes_on": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_an_app_already_deleted_in_slack_counts_as_done": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_cli_config_token_deletes_the_app": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_cli_removes_after_the_typed_name_and_step_up": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_held_keeps_the_thread_idle_and_reconnects_only_if_still_installed": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_install_works_again_from_scratch": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_item_cards_after_removal_and_a_new_install": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_refusals_change_nothing": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_route_needs_step_up_and_holds_the_slack_thread": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_slack_unreachable_forgets_nothing": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_step_up_is_bound_to_the_recorded_app": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_tokens_missing_asks_nothing_of_slack_and_says_what_is_left": _SLACK_ADMIN,
    "tests/test_slack_remove.py::test_with_tokens_the_bot_token_is_revoked_and_everything_slack_is_forgotten": _SLACK_ADMIN,
}
MASKED_TABLES = frozenset({"dns_cache"})


class _State:
    mode: str | None = None
    dump_dir: Path | None = None
    want: dict[str, tuple[int, str]] = {}  # noqa: RUF012 - one plugin instance per process
    on: bool = False
    commits: list[dict[str, Any]] = []  # noqa: RUF012
    last: dict[str, dict[str, dict[str, Any]]] = {}  # noqa: RUF012


S = _State()


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("golden-suite")
    g.addoption("--golden-suite", choices=("record", "check", "dump"), default=None)
    g.addoption("--golden-dump", default=None)


def pytest_configure(config: pytest.Config) -> None:
    S.mode = config.getoption("--golden-suite")
    dump = config.getoption("--golden-dump")
    S.dump_dir = Path(dump) if dump else None
    if S.mode == "dump" and S.dump_dir is None:
        raise pytest.UsageError("--golden-suite=dump needs --golden-dump=DIR")
    if S.mode == "check":
        S.want = load(HASHES)


def load(path: Path) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            node, n, h = line.split("\t")
            out[node] = (int(n), h)
    return out


def _no_timings(v: Any) -> Any:
    """A JSON text with measured `timings` (a check report's IMAP and isolation seconds) loses
    them; anything else is returned as it is."""
    if not isinstance(v, str) or '"timings"' not in v:
        return v
    try:
        doc = json.loads(v)
    except ValueError:
        return v
    if isinstance(doc, dict):
        doc = {k: x for k, x in cast(dict[str, Any], doc).items() if k != "timings"}
    return json.dumps(doc, sort_keys=True)


def _masked(state: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Leave out what isn't the engine's and changes run to run: the settings rows migrations
    write (wall-clock stamps, migration 0025's random install ID), the DNS cache (written by the
    isolation child on the real clock) and measured timings in check reports."""
    out: dict[str, dict[str, Any]] = {}
    for table, rows in state.items():
        if table in MASKED_TABLES:
            continue
        kept = {
            k: {c: _no_timings(v) for c, v in r.items()}
            for k, r in rows.items()
            if not (table == "settings" and r.get("updated_by") == "migration")
        }
        if kept:
            out[table] = kept
    return out


class SuiteConnection(sqlite3.Connection):
    """Records the row diff of each COMMIT on this database while a test runs."""

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        cur = super().execute(sql, parameters)
        if S.on and sql.strip().upper() == "COMMIT":
            path = super().execute("PRAGMA database_list").fetchone()[2]
            if path:
                now = _masked(H.dump(Path(path)))
                S.commits.append(H.diff(S.last.get(path, {}), now))
                S.last[path] = now
        return cur


@contextlib.contextmanager
def _deterministic() -> Generator[None]:
    n = {"id": 0}

    def count() -> int:
        n["id"] += 1
        return n["id"]

    def rid() -> str:
        return H.seq_hex(count(), 16)

    def token(nbytes: int | None = None) -> str:
        del nbytes
        return f"tok{count()}"

    def first(seq: Sequence[Any]) -> Any:
        return seq[0]

    def hexes(nbytes: int | None = None) -> str:
        return H.seq_hex(count(), nbytes or 32)

    with contextlib.ExitStack() as st:
        for owner in ("ecf.ids", "ecf_server.api", "ecf_server.chat", "ecf_server.claude_review",
                      "ecf_server.scans"):  # fmt: skip
            st.enter_context(mock.patch(f"{owner}.new_random_id", rid))
        st.enter_context(mock.patch("secrets.token_hex", hexes))
        st.enter_context(mock.patch("secrets.token_urlsafe", token))
        st.enter_context(mock.patch("secrets.choice", first))
        st.enter_context(mock.patch("uuid.uuid4", lambda: uuid.UUID(H.seq_hex(count(), 16))))
        st.enter_context(mock.patch("ecf_server.checks.holder", lambda: f"holder-{count()}"))
        st.enter_context(mock.patch("ecf_server.stepup.person", lambda: "operator"))
        for cls in (fake.FakeMailSource, fake.GmailFakeSource):  # arrival time when none is given
            st.enter_context(mock.patch.object(cls, "deliver", _arrives(cls.deliver, count)))
        st.enter_context(
            mock.patch.object(
                H.db.sqlite3, "connect", partial(H.ORIG_CONNECT, factory=SuiteConnection)
            )
        )
        yield


def _arrives(deliver: Any, count: Any) -> Any:
    """The fake mailbox's deliver, with a fixed arrival time (a second apart) instead of the wall
    clock when the test gives none."""

    def wrapped(self: Any, raw: bytes, internaldate: datetime | None = None, **kw: Any) -> Any:
        when = internaldate or ARRIVED + timedelta(seconds=count())
        return deliver(self, raw, when, **kw)

    return wrapped


def _hash(commits: list[dict[str, Any]]) -> str:
    blob = json.dumps(commits, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


@pytest.fixture(autouse=True)
def _golden_suite(request: pytest.FixtureRequest) -> Generator[None]:
    node = request.node.nodeid
    if S.mode is None or node.startswith("tests/golden/") or node in UNSTABLE:
        yield
        return
    S.commits, S.last, S.on = [], {}, True
    try:
        with _deterministic():
            yield
    finally:
        S.on = False
    commits, h = S.commits, _hash(S.commits)
    if S.mode == "record":
        PARTS.mkdir(exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        with (PARTS / f"{worker}.tsv").open("a", encoding="utf-8") as f:
            f.write(f"{node}\t{len(commits)}\t{h}\n")
    elif S.mode == "dump":
        assert S.dump_dir is not None
        S.dump_dir.mkdir(parents=True, exist_ok=True)
        name = node.replace("/", "_").replace("::", "__")[:200]
        (S.dump_dir / f"{name}.json").write_text(json.dumps(commits, indent=1, sort_keys=True))
    elif node in S.want and S.want[node] != (len(commits), h):
        n0 = S.want[node][0]
        pytest.fail(
            f"golden: this test's write-sets changed ({n0} commits recorded, {len(commits)} now);"
            " compare --golden-suite=dump here and on main",
            pytrace=False,
        )
    elif node not in S.want:
        pytest.fail("golden: no recorded write-sets for this test (record it)", pytrace=False)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Record mode, on the controller (or the only process): merge the workers' parts."""
    if S.mode != "record" or os.environ.get("PYTEST_XDIST_WORKER"):
        return
    rows = dict(load(HASHES))
    for part in sorted(PARTS.glob("*.tsv")) if PARTS.exists() else []:
        for node, v in load(part).items():
            rows[node] = v
        part.unlink()
    if PARTS.exists():
        PARTS.rmdir()
    HASHES.write_text("".join(f"{k}\t{n}\t{h}\n" for k, (n, h) in sorted(rows.items())),
                      encoding="utf-8")  # fmt: skip
    del session
