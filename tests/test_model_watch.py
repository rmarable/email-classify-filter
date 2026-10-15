"""The weekly model watch (V1.4 step 10; SPEC §7.6; OD-053, OD-299 to OD-303): retirement alerts
from `models.lock`, the optional Models API, the daily lines, the key, `ecf models status` and
`ecf doctor` (the Ollama library's tags went in v3.0.0, ADR 0031)."""

from __future__ import annotations

import functools
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf import __version__
from ecf.cli_models import watch_lines
from ecf.doctor import Level, judge_model_watch
from ecf.errors import ConflictError, InvalidInputError
from ecf.release_source import ReleaseError, ReleaseInfo
from ecf_server import claude_pins, daily, db, health, jev_keys, model_watch
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_claude_review import add

HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
ROLES = "classifier, classifier_high, actor, actor_high"  # Opus's from v3.0.0 (OD-520, OD-521)
DAY = 86400
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "resolves", _resolves(True))


def _resolves(answer: bool) -> Callable[[str], bool]:
    return lambda _host: answer


def _http(handler: Handler) -> model_watch.HttpFactory:
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _models(*ids: tuple[str, str], more: bool = False) -> dict[str, Any]:
    data = [{"type": "model", "id": i, "created_at": c, "display_name": i} for i, c in ids]
    return {"data": data, "has_more": more, "first_id": ids[0][0] if ids else None,
            "last_id": ids[-1][0] if ids else None}  # fmt: skip


PINNED = ((HAIKU, "2026-10-07T00:00:00Z"), (SONNET, "2026-09-28T00:00:00Z"),
          (OPUS, "2026-09-22T00:00:00Z"))  # fmt: skip


def _api(body: dict[str, Any], seen: list[httpx.Request] | None = None) -> Handler:
    def handle(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(200, json=body)

    return handle


def _store(key: str | None = "sk-ant-test") -> MemorySecretStore:
    s = MemorySecretStore()
    if key:
        s.set(model_watch.KEY_SECRET, key)
    return s


def _retiring(monkeypatch: pytest.MonkeyPatch, mid: str, retires: date,
              replacement: str | None = None) -> None:  # fmt: skip
    life = claude_pins.lifecycle()
    life[mid] = claude_pins.Lifecycle("Deprecated", retires, None, replacement)
    monkeypatch.setattr(claude_pins, "lifecycle", lambda: life)


def _alerts(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["kind"]: r["detail"] for r in conn.execute(
        "SELECT kind, detail FROM alerts WHERE resolved_at IS NULL")}  # fmt: skip


# models.lock: every pinned model has a lifecycle entry.


def test_every_pin_has_a_lifecycle_entry_and_none_retires_yet() -> None:
    life = claude_pins.lifecycle()
    assert set(claude_pins.load_lock().values()) <= set(life)
    assert SONNET not in claude_pins.load_lock().values()  # from v3.0.0 (OD-520, OD-521)
    assert life[SONNET] == claude_pins.Lifecycle("Active", None, date(2027, 9, 28))  # cards
    assert life[OPUS] == claude_pins.Lifecycle("Active", None, date(2027, 9, 22))
    assert life[HAIKU] == claude_pins.Lifecycle("Active", None, date(2027, 10, 7))
    assert claude_pins.load_lock()["main_session"] == HAIKU  # the session only, OD-474
    assert all(e.retires is None for e in life.values())  # verified 2026-10-08


# Retirement: a retiring pin is announced, then again 30 and 7 days before, and ecf claude refuses a
# pin past its date until an override replaces it.


def test_a_retiring_pin_is_announced_then_30_and_7_days_before(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = FakeNotifier()
    _retiring(monkeypatch, OPUS, clock.now().date() + timedelta(days=45), "claude-opus-6")
    assert model_watch.retirement_tick(conn, clock, n) == 0  # no address uses Claude
    add(conn, clock, "c", "C")
    assert model_watch.retirement_tick(conn, clock, n) == 1
    head, text = n.sent[-1]
    assert head == "[ecf-alert] Model Retirement Scheduled"
    assert text.startswith(f"{OPUS} ({ROLES}) retires on 2026-11-15 (in 45 days)")
    assert "No ecf release that moves this pin is known yet" in text
    assert "(Anthropic recommends claude-opus-6)" in text
    assert model_watch.retirement_tick(conn, clock, n) == 0  # once per stage
    clock.advance(15 * DAY)  # 30 days left
    assert model_watch.retirement_tick(conn, clock, n) == 1 and "in 30 days" in n.sent[-1][1]
    clock.advance(22 * DAY)  # 8 days left
    assert model_watch.retirement_tick(conn, clock, n) == 0
    clock.advance(DAY)
    assert model_watch.retirement_tick(conn, clock, n) == 1 and "in 7 days" in n.sent[-1][1]
    clock.advance(8 * DAY)  # past the date: `ecf claude` refuses instead
    assert model_watch.retirement_tick(conn, clock, n) == 0
    events = [r[0] for r in conn.execute("SELECT event FROM audit WHERE event LIKE 'models.%'")]
    assert events == ["models.retirement_alert"] * 3


def test_first_seen_inside_30_days_sends_one_alert(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = FakeNotifier()
    add(conn, clock, "b", "C")
    _retiring(monkeypatch, OPUS, clock.now().date() + timedelta(days=20))
    assert model_watch.retirement_tick(conn, clock, n) == 1
    assert f"{OPUS} ({ROLES}) retires" in n.sent[-1][1]
    clock.advance(12 * DAY)  # 8 days left: d30 already counted
    assert model_watch.retirement_tick(conn, clock, n) == 0


def test_ecf_claude_refuses_a_pin_past_its_date_until_an_override_replaces_it(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _retiring(monkeypatch, OPUS, clock.now().date() + timedelta(days=1), "claude-opus-6")
    model_watch.refuse_retired(conn, clock)  # not yet
    clock.advance(DAY)
    with pytest.raises(ConflictError, match="retired on 2026-10-02; upgrade ecf, or run `ecf"
                       " settings set claude_model_override claude-opus-6`"):  # fmt: skip
        model_watch.refuse_retired(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 'os_user')", (claude_pins.OVERRIDE_KEY, json.dumps({"opus":
                     "claude-opus-6"}), to_ts(clock.now())))  # fmt: skip
    model_watch.refuse_retired(conn, clock)
    assert model_watch.retiring(conn) == []


# The Models API watch: newer models in a pinned family, paging, failures as system errors until a
# success, offline and no-key behaviour, and the weekly schedule.


def test_a_newer_model_in_a_pinned_family_goes_on_the_daily_summary_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    seen: list[httpx.Request] = []
    body = _models(("claude-opus-6", "2027-01-01T00:00:00Z"), ("claude-fable-6", "2027-01-02T"
                   "00:00:00Z"), ("claude-opus-5", "2026-06-30T00:00:00Z"), ("claude-sonnet-6",
                   "2027-01-03T00:00:00Z"), *PINNED)  # fmt: skip
    st = model_watch.run(conn, clock, FakeNotifier(), _store(), _http(_api(body, seen)))
    req = seen[0]
    assert req.url.host == "api.anthropic.com" and req.url.params["limit"] == "1000"
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert len(seen) == 1  # the Models API only (no Ollama library from v3.0.0)
    assert sorted(st["claude"]["newer"]) == ["claude-opus-6"]  # not older, not another family
    assert st["next_at"] == to_ts(clock.now() + timedelta(days=7))
    lines = model_watch.daily_lines(conn)
    assert lines == [
        "Newer Claude model(s) in a family ecf uses: claude-opus-6 (ecf pins"
        f" {OPUS}). ecf never switches by itself; a release moves the pins."
    ]
    assert lines[0] in daily.card(conn, clock.now(), "2026-10-01").text.splitlines()
    with write_tx(conn):
        model_watch.mark_reported(conn, to_ts(clock.now()))
    assert model_watch.daily_lines(conn) == []
    model_watch.run(conn, clock, FakeNotifier(), _store(), _http(_api(body)))
    assert model_watch.daily_lines(conn) == []  # still known, still reported


def test_pages_are_followed_and_a_pin_the_api_doesnt_list_keeps_a_system_error(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    pages = [_models(PINNED[0], more=True), _models(PINNED[1])]  # no Opus

    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[1 if req.url.params.get("after_id") else 0])

    n = FakeNotifier()
    st = model_watch.run(conn, clock, n, _store(), _http(handle))
    assert st["claude"]["listed"] == 2 and st["claude"]["missing"] == [OPUS]
    assert _alerts(conn)["models_missing"].startswith(
        f"The Models API doesn't list the pinned model(s) {OPUS}: retired, or your API key's"
    )
    assert n.sent[-1][0] == "[ecf-alert] System Error"
    model_watch.run(conn, clock, n, _store(), _http(_api(_models(*PINNED))))
    assert "models_missing" not in _alerts(conn)


@pytest.mark.parametrize(
    ("response", "text"),
    [
        (httpx.Response(401, json={}), "the API key was refused (HTTP 401). Replace it with"),
        (httpx.Response(500, json={}), "HTTP 500."),
        (httpx.Response(200, json={"nope": 1}), "an answer ecf can't read."),
    ],
)
def test_a_models_api_failure_raises_system_error_until_a_success(
    conn: sqlite3.Connection, clock: FakeClock, response: httpx.Response, text: str
) -> None:
    n = FakeNotifier()
    st = model_watch.run(conn, clock, n, _store(), _http(lambda _r: response))
    detail = _alerts(conn)["models_api"]
    assert detail.startswith("The weekly model watch couldn't use the Models API: ")
    assert text in detail and st["claude"]["error"]
    assert st["next_at"] == to_ts(clock.now() + timedelta(days=7))  # §15.4: the next weekly run
    model_watch.run(conn, clock, n, _store(), _http(_api(_models(*PINNED))))
    assert "models_api" not in _alerts(conn)
    assert n.sent[-1][0] == "[ecf-alert] Resolved: System Error"


def test_offline_waits_an_hour_and_without_a_key_nothing_is_called(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    st = model_watch.run(conn, clock, FakeNotifier(), _store(None), _http(never))
    assert st["claude"] is None and st["api_key"] is False
    monkeypatch.setattr(health, "resolves", _resolves(False))
    st = model_watch.run(conn, clock, FakeNotifier(), _store(), _http(never))
    assert st["next_at"] == to_ts(clock.now() + timedelta(hours=1)) and _alerts(conn) == {}


def test_due_weekly_and_one_watch_at_a_time(conn: sqlite3.Connection, clock: FakeClock,
                                            db_path: Path) -> None:  # fmt: skip
    assert model_watch.due(conn, clock)
    started: list[Callable[[], None]] = []
    args = (lambda: db.connect(db_path), clock, FakeNotifier(), _store(None))
    assert model_watch.start(*args, spawn=started.append) is True
    assert model_watch.start(*args, spawn=started.append) is False  # the first hasn't run yet
    started[0]()
    assert not model_watch.due(conn, clock)
    clock.advance(7 * DAY)
    assert model_watch.due(conn, clock)
    model_watch.run_now(conn)
    assert model_watch.start(*args, spawn=lambda f: f()) is True


def test_the_key_is_stored_only_once_the_api_accepts_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    store = _store(None)
    with pytest.raises(InvalidInputError, match="didn't accept it: the API key was refused"):
        model_watch.set_key(conn, clock, store, "sk-ant-bad",
                            _http(lambda _r: httpx.Response(401, json={})))  # fmt: skip
    assert store.get(model_watch.KEY_SECRET) is None
    with pytest.raises(InvalidInputError, match="without spaces"):
        model_watch.set_key(conn, clock, store, "sk ant", _http(_api(_models(*PINNED))))
    r = model_watch.set_key(conn, clock, store, "sk-ant-ok", _http(_api(_models(*PINNED))))
    assert r["listed"] == 3 and r["api_key"] is True and r["next_at"] is None
    assert store.get(model_watch.KEY_SECRET) == "sk-ant-ok"
    model_watch.set_key(conn, clock, store, None)
    assert store.get(model_watch.KEY_SECRET) is None
    assert model_watch.status(conn)["api_key"] is False
    audit = [(r[0], r[1]) for r in conn.execute("SELECT event, data FROM audit")]
    assert audit == [("models.api_key_set", '{"listed": 3, "replaced": false}'),
                     ("models.api_key_cleared", "{}")]  # fmt: skip
    assert "sk-ant" not in json.dumps(audit)


def test_status_and_doctor_lines(conn: sqlite3.Connection, clock: FakeClock,
                                 monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    st = model_watch.status(conn)
    lines = watch_lines(st)
    assert f"  {OPUS}: Active, retirement not before 2027-09-22 (models.lock)" in lines
    assert lines[-2:] == [
        "model watch: no Models API key (optional: ecf models api-key set --provider anthropic);"
        " next run within a minute", f"Jev pin (jev.lock): {jev_keys.pin()}"]  # fmt: skip
    today = clock.now().date()
    checks = judge_model_watch({"model_watch": st}, today)
    assert [(c.name, c.level) for c in checks] == [("model watch", Level.OK)]
    _retiring(monkeypatch, OPUS, today + timedelta(days=3))
    add(conn, clock, "c", "C")
    checks = judge_model_watch({"model_watch": model_watch.status(conn)}, today)
    assert (checks[0].level, checks[0].detail) == (Level.WARN, f"retires on 2026-10-04 ({ROLES})")
    later = judge_model_watch({"model_watch": model_watch.status(conn)}, date(2026, 10, 4))
    assert later[0].level == Level.FAIL and later[0].detail.startswith("retired on")
    model_watch.run(conn, clock, FakeNotifier(), _store(),
                    _http(lambda _r: httpx.Response(503, json={})))  # fmt: skip
    st = model_watch.status(conn)
    st["api_key"] = True  # stored through the API in real use
    assert "model watch: Models API failed: HTTP 503; next run" in watch_lines(st)[-2]
    assert any(c.detail == "the Models API failed: HTTP 503"
               for c in judge_model_watch({"model_watch": st}, today))  # fmt: skip
    assert datetime.fromisoformat(str(st["next_at"]).replace("Z", "+00:00")) > clock.now()


# ecf releases (operator decision D7, 2026-10-06): a newer release goes on the daily summary once.


def test_a_newer_release_goes_on_the_daily_summary_once(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    found = [ReleaseInfo("v9.0.0", False), ReleaseInfo("v9.1.0-rc1", True)]
    run = functools.partial(model_watch.run, conn, clock, FakeNotifier(), _store(None),
                            _http(never))  # fmt: skip
    st = run(releases=lambda: found)
    assert st["release"]["newest"] == "v9.0.0" and st["release"]["error"] is None
    line = f"ecf 9.0.0 is out (this is {__version__}): `ecf upgrade` checks it, then asks."
    assert line in model_watch.daily_lines(conn)
    assert "  ecf releases: v9.0.0 is out (ecf upgrade), read 2026-10-01" in watch_lines(st)
    with write_tx(conn):
        model_watch.mark_reported(conn, to_ts(clock.now()))
    run(releases=lambda: found)
    assert model_watch.daily_lines(conn) == []  # once
    found.append(ReleaseInfo("v9.0.1", False))
    run(releases=lambda: found)
    assert any("ecf 9.0.1 is out" in x for x in model_watch.daily_lines(conn))

    def signed_out() -> list[ReleaseInfo]:
        raise ReleaseError("gh isn't signed in to GitHub; run gh auth login")

    st = run(releases=signed_out)
    assert st["release"]["error"].startswith("gh isn't signed in") and _alerts(conn) == {}
    assert "  ecf releases: couldn't list them (gh isn't signed in" in watch_lines(st)[-1]
    monkeypatch.setattr(health, "resolves", _resolves(False))
    st = run(releases=lambda: pytest.fail("not called offline"))
    assert st["next_at"] == to_ts(clock.now() + timedelta(hours=1))
    st = run()  # no lister (tests' services, `ecf-server dev`): nothing about releases
    assert st["next_at"] == to_ts(clock.now() + timedelta(days=7))


def test_a_retiring_session_pin_doesnt_say_addresses_go_back_to_assist() -> None:
    r = {"id": HAIKU, "roles": ["main_session"], "retires": "2027-10-07", "replacement": None}
    text = model_watch.retirement_text(r, date(2027, 10, 1))
    assert text.endswith("keeps reviews going.")  # the session isn't a gate pin (OD-474)
    gate = model_watch.retirement_text(r | {"roles": ["actor"]}, date(2027, 10, 1))
    assert "go back to assist" in gate
