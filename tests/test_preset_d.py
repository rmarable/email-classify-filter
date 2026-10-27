"""Preset D on an address (v3.0.0 step 4; OD-509; plan v3.0.0-STEP4 §3, §5, §6, §7): adding
one (the key, the owner's agreement, step-up), the consent marker on resume, the pin in upgrades,
rollback, cards, the estimate, reprocessing, the model watch and migration 0040."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from ecf import upgrade_check
from ecf.client import LocalClient
from ecf.errors import ConflictError, InternalError, InvalidInputError, StepupRequiredError
from ecf_server import (
    addresses,
    backfill,
    cards,
    claude_queue,
    db,
    jev,
    jev_dispatch,
    jev_keys,
    jev_mapping,
    model_watch,
    pause,
    reprocess,
    stepup,
    upgrade_state,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import Env, Mail, call, make_state, req
from tests.test_claude_review import REQUEST, add, row, waiting_item
from tests.test_upgrade_check import _wheel  # pyright: ignore[reportPrivateUsage]

PIN = jev.load_pin()
KEY = "ts-test-key-CANARY-91c2"  # invented


@pytest.fixture(autouse=True)
def d_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preset D is held back from v3.0.0 (OD-516); its dormant code is tested with it on."""
    monkeypatch.setattr(addresses, "D_AVAILABLE", True)


@pytest.fixture
def env(conn: sqlite3.Connection, clock: FakeClock) -> Env:
    return conn, clock, MemorySecretStore(), Mail()


def _add(env: Env, nonce: str | None = None, **kw: Any) -> dict[str, Any]:
    conn, clock, secrets, mail = env
    return addresses.add_address(
        conn, clock, secrets, mail, req(preset="D", stepup_nonce=nonce, **kw), actor="os_user"
    )


def _nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


# Adding a D address (§3.1, P9, P22): the key by its name, typed agreement, step-up, the marker.


def test_adding_d_needs_the_key_the_owners_agreement_and_step_up(env: Env) -> None:
    conn, clock, secrets, mail = env
    with pytest.raises(InvalidInputError, match="needs the TypeSafe API key first"):
        _add(env, owner_agreed=True)
    secrets.set(jev_keys.KEY_SECRET, KEY)
    with pytest.raises(InvalidInputError, match="owner's agreement is needed"):
        _add(env)
    with pytest.raises(StepupRequiredError) as ei:
        _add(env, owner_agreed=True)
    assert ei.value.extra == {"purpose": addresses.ADD_D,
                              "target": {"email": "ap@acme.example"}}  # fmt: skip
    assert mail.logins == []  # nothing reaches the mailbox before the step-up
    a = _add(env, _nonce(conn, clock, ei.value), owner_agreed=True)
    assert a["preset"] == "D" and a["jev_consent"] is True
    events = [(r[0], r[1]) for r in conn.execute("SELECT event, data FROM audit WHERE event"
                                                 " LIKE 'address.%' ORDER BY id")]  # fmt: skip
    assert ("address.consent_confirmed", '{"to": "typesafe"}') in events
    assert KEY not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM audit")])


def test_d_is_refused_while_your_own_fields_are_stored(env: Env) -> None:
    from tests.test_claude_review import extend  # noqa: PLC0415

    conn, clock, secrets, _ = env
    secrets.set(jev_keys.KEY_SECRET, KEY)
    extend(conn, clock)
    with pytest.raises(InvalidInputError, match="shipped schema only"):
        _add(env, owner_agreed=True)


def test_a_c_address_added_again_drops_an_old_agreement(env: Env) -> None:
    conn, clock, secrets, _ = env
    secrets.set(jev_keys.KEY_SECRET, KEY)
    with pytest.raises(StepupRequiredError) as ei:
        _add(env, owner_agreed=True)
    _add(env, _nonce(conn, clock, ei.value), owner_agreed=True)
    addresses.remove_address(conn, clock, secrets, "ap", actor="os_user")
    secrets.set("mailbox/ap", "x")
    a = addresses.add_address(conn, clock, secrets, env[3], req(org_domains=None),
                              actor="os_user")  # fmt: skip
    assert a["preset"] == "C" and a["jev_consent"] is False


def test_the_api_passes_the_agreement_and_lists_the_key(conn: sqlite3.Connection,
                                                        db_path: Path) -> None:  # fmt: skip
    secrets = MemorySecretStore()
    state = make_state(db_path, secrets)
    assert call(state, "GET", "/v1/addresses").json()["typesafe_key"] is False
    body = {
        "email": "ap@acme.example",
        "imap_host": "imap.acme.example",
        "sensitivity": "standard",
        "preset": "D",
        "app_password": "right-password",
        "org_domains": ["acme.example"],
    }
    r = call(state, "POST", "/v1/addresses", body)
    assert r.status_code == 400 and "TypeSafe API key" in r.text
    secrets.set(jev_keys.KEY_SECRET, KEY)
    assert call(state, "GET", "/v1/addresses").json()["typesafe_key"] is True
    r = call(state, "POST", "/v1/addresses", body)
    assert r.status_code == 400 and "agreement" in r.text
    r = call(state, "POST", "/v1/addresses", body | {"owner_agreed": True})
    assert r.status_code == 403 and r.json()["purpose"] == addresses.ADD_D  # step-up next


def test_resuming_d_without_the_agreement_asks_again(conn: sqlite3.Connection,
                                                     clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "d", "D")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1")
    assert pause.awaiting_consent(conn) == ["d"]
    with pytest.raises(ConflictError, match="preset D, so its mail goes to TypeSafe"):
        pause.set_paused(conn, clock, "d", False, actor="os_user")
    assert pause.set_paused(conn, clock, pause.ALL, False, actor="os_user") == []  # skipped
    assert pause.set_paused(conn, clock, "d", False, actor="os_user", owner_agreed=True) == ["d"]
    assert addresses.get_address(conn, "d")["jev_consent"] is True
    assert conn.execute("SELECT data FROM audit WHERE event = 'address.consent_confirmed'"
                        ).fetchone()[0] == '{"to": "typesafe"}'  # fmt: skip
    pause.set_paused(conn, clock, "d", True, actor="os_user")
    assert pause.set_paused(conn, clock, "d", False, actor="os_user") == ["d"]  # ordinary now


def test_the_cli_asks_for_the_key_and_the_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer  # noqa: PLC0415

    from ecf import cli  # noqa: PLC0415

    sent: list[tuple[str, str, Any]] = []

    class Client:
        def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
            sent.append((method, path, body))
            return {}

    answers = ["y", "n"]

    def secret(_prompt: str) -> str:
        return "ts-key-0123456789abcdef"

    def prompt(*_a: object, **_k: object) -> str:
        return answers.pop(0)

    monkeypatch.setattr(cli, "hidden", secret)
    monkeypatch.setattr(typer, "prompt", prompt)
    monkeypatch.setattr(cli, "require_terminal", lambda: None)
    preset_d = cli._preset_d  # pyright: ignore[reportPrivateUsage]
    client = cast("LocalClient", Client())
    body: dict[str, object] = {"preset": "D"}
    preset_d(client, {"typesafe_key": False}, body)
    assert sent == [("POST", "/v1/models/keys/typesafe", {"key": "ts-key-0123456789abcdef"})]
    assert body["owner_agreed"] is True
    with pytest.raises(typer.Exit):
        preset_d(client, {"typesafe_key": True}, {})


def test_the_d_questions_need_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """R12: without a terminal the agreement is never asked (nor the key), as in cli_items."""
    import typer  # noqa: PLC0415

    from ecf import cli  # noqa: PLC0415

    asked: list[str] = []

    def ask(text: str, **_k: object) -> str:
        asked.append(text)
        return "y"

    monkeypatch.setattr(typer, "prompt", ask)
    monkeypatch.setattr(cli, "hidden", ask)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("y\n"))  # not a terminal
    preset_d = cli._preset_d  # pyright: ignore[reportPrivateUsage]
    body: dict[str, object] = {"preset": "D"}
    with pytest.raises(InvalidInputError):
        preset_d(cast("LocalClient", object()), {"typesafe_key": True}, body)
    assert asked == [] and "owner_agreed" not in body


# The pin and the mapping in upgrades (§2.4, S4-D12, P18).


def test_the_wheel_names_jevs_pin_and_mapping(tmp_path: Path) -> None:
    path = _wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("ecf_server/data/jev.lock", json.dumps({"model": "jev-1.15.0"}))
        z.writestr("ecf_server/data/jev_mapping.json", '{"bands": {"phishing": 0.4}}')
    rel = upgrade_check.read_wheel(path)
    assert rel.pins["jev"] == "jev-1.15.0"
    assert rel.pins["jev_mapping"] == upgrade_check.mapping_digest({"bands": {"phishing": 0.4}})
    fams, hit = upgrade_check.changes({"jev": PIN}, None, {"jev": "jev-1.15.0"}, None,
                                      {"jev": ["d"], "actor": ["c", "d"]})  # fmt: skip
    assert (fams, hit) == (["jev"], ["d"])


def test_the_client_digests_a_mapping_as_the_service_does() -> None:
    """The upgrade check (`ecf`, which can't import the service) and Jev's gate key
    (`jev_mapping.digest`) agree on the shipped mapping and on a banded one, whatever its
    formatting or comment."""
    shipped = json.loads((Path(jev_mapping.__file__).parent / "data" / jev_mapping.FILE)
                         .read_text("utf-8"))  # fmt: skip
    assert upgrade_check.mapping_digest(shipped) == jev_mapping.digest()
    doc = {"_comment": "x", "version": 1, "bands": {"phishing": 0.3, "bank_detail_change": 0.25},
           "bars": {"sender_type": 1}, "none_bars": {"fraud_risk": 0.6}}  # fmt: skip
    assert upgrade_check.mapping_digest(doc) == jev_mapping.digest(jev_mapping.parse(doc))
    same = upgrade_check.mapping_digest(doc | {"_comment": "y"})
    assert same == upgrade_check.mapping_digest(doc)
    # R14: a bool is no number to either side (the service's `_number` refuses it)
    with_bool = doc | {"bars": {"sender_type": 1, "fraud_risk": True}}
    assert upgrade_check.mapping_digest(with_bool) == upgrade_check.mapping_digest(doc)
    with pytest.raises(InternalError):
        jev_mapping.parse(with_bool)


def test_the_service_names_ds_pin_users(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "d", "D")
    st = upgrade_state.state(conn, clock, api_version=1, sessions=0)
    assert st["pins"]["jev"] == PIN and st["pins"]["jev_mapping"] == jev_mapping.digest()
    assert st["pin_users"]["jev"] == ["d"] and st["pin_users"]["actor"] == ["c", "d"]
    assert "d" not in st["pin_users"]["classifier"]


# Records, cards, estimates, reprocessing (§5).


def test_migration_0040_adds_jevs_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_calls)")}
    assert {"backend", "model", "fault", "ms", "tries"} <= cols


def test_cards_name_jev(conn: sqlite3.Connection) -> None:
    assert cards.model_name(PIN) == f"TypeSafe Jev {PIN.removeprefix('jev-')}"
    assert cards.model_name("jev-0.0.1") == cards.NOT_INSTALLED  # not this release's pin


def test_ds_estimate_adds_typesafes_part(conn: sqlite3.Connection, clock: FakeClock) -> None:
    e = backfill.estimate(conn, clock.now(), 1000, "D")
    assert e["jev"]["source"] == backfill.JEV_FALLBACK_SOURCE and e["claude_note"]
    assert e["jev"]["usd"] == round(1000 * 2374 * 0.042 / 1e6, 4) and e["jev"]["minutes"] is None
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, set_version, created_at, digest,"
                     " metrics, gate_passed, path) VALUES ('r1', 'jev/hosted', 'v', ?, 'k', ?, 0,"
                     " '')",
                     (to_ts(clock.now()), json.dumps({"cases": 100, "jev": {
                         "input_tokens_billed": 150_000}})))  # fmt: skip
        conn.execute(
            "INSERT INTO model_calls (ts, role, outcome, digest, backend, ms) VALUES"
            " (?, 'classifier', 'ok', 'x', 'jev', 120)",
            (to_ts(clock.now()),),
        )
    e = backfill.estimate(conn, clock.now(), 1000, "D")
    assert e["jev"]["tokens_per_email"] == 1500 and "latest Jev eval run" in e["jev"]["source"]
    assert e["jev"]["minutes"] == 2.0
    assert "jev" not in backfill.estimate(conn, clock.now(), 1000, "C")


def test_reprocessing_d_goes_to_the_drain(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "d", "D")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET overrides = json_set(overrides, ?, 't')",
                     (f"$.{jev_keys.CONSENT}",))  # fmt: skip
    jev_dispatch.refresh_keys(conn, clock)
    key = reprocess.current_key(conn, "d")
    assert key["provider"] == "jev" and key["classifier"] == PIN
    assert key["prompt"] == jev_keys.stored(conn)["prompt_digest"]
    sid = waiting_item(conn, clock, "d", classification=REQUEST)
    with write_tx(conn), db.items_writer("transition"):
        conn.execute("UPDATE items SET status = 'observed', facts = json_set(coalesce(facts,"
                     " '{}'), '$.jev', json('{\"unsure\": [\"phishing\"]}'),"
                     " '$.jev_attempts', 1) WHERE stable_id = ?", (sid,))  # fmt: skip
    jev_keys.WAKE.clear()
    reprocess._move(conn, clock, row(conn, sid), "reclassify", key)  # pyright: ignore[reportPrivateUsage]
    r = row(conn, sid)
    assert r["status"] == "awaiting_claude" and r["classification"] is None
    facts = json.loads(r["facts"])  # R7: Jev's old caution goes with the old classification
    assert "jev" not in facts and "jev_attempts" not in facts
    assert jev_keys.WAKE.is_set() and claude_queue.waiting_for_jev(conn) == {"d": 1}
    assert jev_dispatch.claimable(conn, clock.now()) == 1
    jev_keys.WAKE.clear()


# The model watch records TypeSafe's aliases (hand-off 6) and the version jev-latest answers as,
# whenever the TypeSafe key is stored, D held back or not (OD-561).


def _watch_jev(  # noqa: PLR0913 - one watch's knobs, all keyword-only after `latest`
        conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
        latest: str | None, *, status: int = 200, key: str | None = KEY,
        post_status: int | None = None, post_body: bytes | None = None,
        resolves: bool = True, released: str = "2026-09-30",
) -> tuple[list[httpx.Request], list[dict[str, Any]]]:  # fmt: skip
    """One watch with only the TypeSafe key (D held back), jev-latest answering as `latest` (or
    no `model` when None); the requests sent and the `jev.watch` log lines."""
    from ecf_server import health  # noqa: PLC0415

    monkeypatch.setattr(addresses, "D_AVAILABLE", False)

    def online(_host: str) -> bool:
        return resolves

    monkeypatch.setattr(health, "resolves", online)
    logged: list[dict[str, Any]] = []

    def info(event: str, **kw: Any) -> None:
        if event == "jev.watch":
            logged.append(kw)

    monkeypatch.setattr(model_watch.log, "info", info)
    seen: list[httpx.Request] = []

    def answer(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        if r.method == "GET":
            listed = [{"name": "jev-latest", "description": "x", "release_date": released}]
            return httpx.Response(status, json={"models": listed})
        if post_body is not None:
            return httpx.Response(post_status or status, content=post_body)
        reply: dict[str, Any] = {"answers": {"check": {"type": "noul", "noul": 0.9}},
                                 "usage": {"input_tokens": 279, "output_tokens": 20}}  # fmt: skip
        answered = reply | ({"model": latest} if latest else {})
        return httpx.Response(post_status or status, json=answered)

    s = MemorySecretStore()
    if key:
        s.set(jev_keys.KEY_SECRET, key)
    model_watch.run(conn, clock, FakeNotifier(), s,
                    lambda: httpx.Client(transport=httpx.MockTransport(answer)))  # fmt: skip
    return seen, logged


def test_the_watch_asks_typesafe_while_d_is_held_back(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """The listing, then the key check's request to jev-latest: no email content, the key only
    in the Authorization header, and the log line without the key or a body."""
    seen, logged = _watch_jev(conn, clock, monkeypatch, PIN)
    sent = [(r.method, str(r.url)) for r in seen]
    assert sent == [("GET", model_watch.JEV_MODELS_URL), ("POST", model_watch.JEV_ASK_URL)]
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in seen)
    assert seen[1].headers["accept"] == "application/json"  # D9: Jev's own header set
    assert "content-type" not in seen[0].headers and seen[1].headers["content-type"]
    assert json.loads(seen[1].content) == {"model": "jev-latest",
                                           "questions": jev.KEY_CHECK_QUESTIONS,
                                           "state": jev.KEY_CHECK_STATE}  # fmt: skip
    assert KEY.encode() not in seen[1].content
    assert logged == [{"status": 200, "http_ms": logged[0]["http_ms"], "usage_in": 279,
                       "model": PIN}]  # fmt: skip
    st = model_watch.status(conn)
    assert st["jev"]["aliases"] == {"jev-latest": "2026-09-30"} and st["jev_pin"] == PIN
    assert st["jev"]["latest"] == PIN and st["jev"]["newer"] is None
    assert model_watch.daily_lines(conn) == []
    from ecf.cli_models import watch_lines  # noqa: PLC0415

    lines = watch_lines(st)
    assert any("jev-latest released 2026-09-30" in x for x in lines)
    assert any(f"jev-latest answers as {PIN}, the pinned version" in x for x in lines)


def test_a_newer_jev_goes_on_the_daily_summary_once(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    st = model_watch.status(conn)
    assert st["jev"]["newer"]["version"] == "jev-9.9.0" and st["jev"]["newer"]["pinned"] == PIN
    from ecf.cli_models import watch_lines  # noqa: PLC0415

    assert any("jev-latest is now jev-9.9.0" in x and f"the pin is {PIN}" in x
               for x in watch_lines(st))  # fmt: skip
    lines = model_watch.daily_lines(conn)
    assert len(lines) == 1 and "jev-9.9.0" in lines[0] and PIN in lines[0]
    with write_tx(conn):
        model_watch.mark_reported(conn, to_ts(clock.now()))
    assert model_watch.daily_lines(conn) == []
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")  # the same version: not reported again
    assert model_watch.daily_lines(conn) == []
    _watch_jev(conn, clock, monkeypatch, "jev-9.10.0")  # another one replaces it
    assert len(model_watch.daily_lines(conn)) == 1
    _watch_jev(conn, clock, monkeypatch, PIN)  # back to the pin: the flag goes
    assert model_watch.status(conn)["jev"]["newer"] is None
    assert model_watch.daily_lines(conn) == []


def test_a_release_that_pins_the_newer_jev_drops_the_flag(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    monkeypatch.setattr(jev_keys, "pin", lambda: "jev-9.9.0")
    assert model_watch.status(conn)["jev"]["newer"] is None
    assert model_watch.daily_lines(conn) == []


@pytest.mark.parametrize(("latest", "status", "error"), [
    (None, 200, "an answer ecf can't read"),
    ("jev-9.9.0", 401, "the TypeSafe key was refused, HTTP 401: replace it with `ecf models"
     " api-key set --provider typesafe`"),
    ("jev-latest", 200, "an answer ecf can't read"),  # D5: an alias, never a version
    ("x" * 5000, 200, "an answer ecf can't read"),
    ("jev-9.9.0", 500, "HTTP 500"),
])  # fmt: skip
def test_a_failed_typesafe_check_raises_no_flag(conn: sqlite3.Connection, clock: FakeClock,
                                                monkeypatch: pytest.MonkeyPatch,
                                                latest: str | None, status: int,
                                                error: str) -> None:  # fmt: skip
    _watch_jev(conn, clock, monkeypatch, latest, status=status)
    j = model_watch.status(conn)["jev"]
    assert error in j["error"] and not j.get("newer")
    assert model_watch.daily_lines(conn) == []
    assert not conn.execute("SELECT 1 FROM alerts").fetchone()


def test_the_daily_summary_mark_isnt_lost_to_a_slow_check(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D1: the summary marks the newer version while the next check is waiting on TypeSafe; the
    check's write keeps the mark."""
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    real = model_watch._jev_latest  # pyright: ignore[reportPrivateUsage]

    def slow(client: httpx.Client, headers: dict[str, str]) -> str:
        got = real(client, headers)
        with write_tx(conn):  # the daily summary runs meanwhile
            model_watch.mark_reported(conn, to_ts(clock.now()))
        return got

    monkeypatch.setattr(model_watch, "_jev_latest", slow)
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    assert model_watch.daily_lines(conn) == []


def test_a_failed_check_keeps_the_newer_version_on_show(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D2, D7: a failed check keeps last week's flag on `ecf models status`, and a failed version
    request keeps the aliases it just read."""
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0", post_status=500, released="2026-10-20")
    st = model_watch.status(conn)
    assert st["jev"]["error"] == "HTTP 500"
    assert st["jev"]["aliases"] == {"jev-latest": "2026-10-20"}  # the listing this check read
    from ecf.cli_models import watch_lines  # noqa: PLC0415

    lines = watch_lines(st)
    assert any("couldn't read them: HTTP 500" in x for x in lines)
    assert any("jev-latest is now jev-9.9.0" in x for x in lines)


@pytest.mark.parametrize("body", [b"not json", b'{"model": "jev-9.9.0", "usage": [1]}'])
def test_an_odd_reply_is_logged_and_read_safely(conn: sqlite3.Connection, clock: FakeClock,
                                                monkeypatch: pytest.MonkeyPatch,
                                                body: bytes) -> None:  # fmt: skip
    """D7: a 200 that isn't JSON fails the check; usage that isn't an object doesn't, and either
    way the log line is written."""
    _, logged = _watch_jev(conn, clock, monkeypatch, None, post_body=body)
    assert len(logged) == 1 and logged[0]["status"] == 200 and logged[0]["usage_in"] is None
    j = model_watch.status(conn)["jev"]
    if body == b"not json":
        assert j["error"] == "an answer ecf can't read"
    else:
        assert j["error"] is None and j["newer"]["version"] == "jev-9.9.0"


def test_typesafe_offline_keeps_the_weekly_cadence(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D3: an unresolvable TypeSafe host is a failed check, never an hour-on retry for the whole
    watch."""
    seen, _ = _watch_jev(conn, clock, monkeypatch, PIN, resolves=False)
    assert seen == []
    st = model_watch.status(conn)
    assert st["jev"]["error"] == "the host doesn't resolve"
    assert st["next_at"] == to_ts(clock.now() + model_watch.EVERY)


def test_the_typesafe_key_resets_the_watch(conn: sqlite3.Connection, clock: FakeClock,
                                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D6: setting the key brings the watch to the next tick; clearing it drops the TypeSafe
    record, so no stale version line or summary line is left."""
    assert (jev.WATCH_NEXT, jev.WATCH_RECORD) == (model_watch.NEXT, model_watch.JEV)
    _watch_jev(conn, clock, monkeypatch, "jev-9.9.0")
    assert model_watch.status(conn)["next_at"] is not None
    s = MemorySecretStore()

    def ok(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": PIN, "answers": {}})

    jev.set_key(conn, clock, s, KEY, httpx.MockTransport(ok))
    assert model_watch.status(conn)["next_at"] is None
    jev.set_key(conn, clock, s, None)
    assert model_watch.status(conn)["jev"] is None and model_watch.daily_lines(conn) == []


def test_the_watch_leaves_typesafe_alone_without_its_key(
        conn: sqlite3.Connection, clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    seen, _ = _watch_jev(conn, clock, monkeypatch, PIN, key=None)
    assert seen == [] and model_watch.status(conn)["jev"] is None


# Rollback drops D's gate rows (§6, S4-D13).


def test_downgrade_drops_ds_gate_rows(tmp_path: Path) -> None:
    from ecf import __version__  # noqa: PLC0415
    from ecf.paths import Paths  # noqa: PLC0415
    from ecf_server import downgrade, upgrade_snapshot  # noqa: PLC0415

    paths = Paths("t", tmp_path / "home")
    c = db.connect(paths.db)
    db.migrate(c)
    with write_tx(c):
        c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                  " VALUES ('ap', 'ap@acme.example', 'standard', 'C', 't')")  # fmt: skip
    c.close()
    label = f"0.0.9-to-{__version__}"
    upgrade_snapshot.take(paths, label)
    c = db.connect(paths.db)
    with write_tx(c):
        for pair in ("claude/claude", "jev/claude"):
            c.execute("INSERT INTO gate (address_id, pair_key, reviewed, correct, fraud_misses,"
                      " unsafe) VALUES ('ap', ?, 1, 1, 0, 0)", (pair,))  # fmt: skip
    c.close()
    out = downgrade.prepare(paths, label)
    r = sqlite3.connect(out)
    assert [x[0] for x in r.execute("SELECT pair_key FROM gate")] == ["claude/claude"]
    r.close()


def test_resume_asks_the_d_question_in_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer  # noqa: PLC0415

    from ecf import cli_items  # noqa: PLC0415

    listed = {"addresses": [{"address_id": "d", "preset": "D", "paused": True,
                             "jev_consent": False}]}  # fmt: skip

    class Client:
        def get(self, path: str) -> dict[str, Any]:
            return {"converted": []} if path == "/v1/upgrade/state" else listed

    answers = ["y", "n"]
    asked: list[str] = []

    def prompt(text: str, **_k: object) -> str:
        asked.append(text)
        return answers.pop(0)

    monkeypatch.setattr(cli_items, "require_terminal", lambda: None)
    monkeypatch.setattr(typer, "prompt", prompt)
    client = cast("LocalClient", Client())
    owner_agreed = cli_items._owner_agreed  # pyright: ignore[reportPrivateUsage]
    assert owner_agreed(client, "d") is True and "TypeSafe" in asked[0]
    with pytest.raises(typer.Exit):
        owner_agreed(client, "d")
    listed["addresses"][0]["jev_consent"] = True
    assert owner_agreed(client, "d") is False  # nothing to ask


def test_the_service_tick_runs_jevs_part(monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:  # fmt: skip
    from ecf.paths import Paths  # noqa: PLC0415
    from ecf_server import db, service  # noqa: PLC0415

    seen: list[dict[str, Any]] = []

    def fake_tick(_conn: sqlite3.Connection, _clock: Any, live: Any, _n: Any, _connect: Any,
                  _store: Any, *, awake: float, woke: bool) -> dict[str, Any]:  # fmt: skip
        seen.append({"awake": awake, "woke": woke, "live": live})
        return {}

    monkeypatch.setattr(jev_dispatch, "tick", fake_tick)
    svc = service.Service(Paths("t", tmp_path / "home"), FakeClock(), dev=True)
    svc.state.db_path = svc.paths.db
    c = db.connect(svc.paths.db)
    db.migrate(c)
    c.close()
    svc.tick()
    assert len(seen) == 1 and seen[0]["live"] is svc.state.jev and seen[0]["woke"] is False
