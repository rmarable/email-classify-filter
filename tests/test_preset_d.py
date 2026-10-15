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
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
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
    preset_d = cli._preset_d  # pyright: ignore[reportPrivateUsage]
    client = cast("LocalClient", Client())
    body: dict[str, object] = {"preset": "D"}
    preset_d(client, {"typesafe_key": False}, body)
    assert sent == [("POST", "/v1/models/keys/typesafe", {"key": "ts-key-0123456789abcdef"})]
    assert body["owner_agreed"] is True
    with pytest.raises(typer.Exit):
        preset_d(client, {"typesafe_key": True}, {})


# The pin and the mapping in upgrades (§2.4, S4-D12, P18).


def test_the_wheel_names_jevs_pin_and_mapping(tmp_path: Path) -> None:
    path = _wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("ecf_server/data/jev.lock", json.dumps({"model": "jev-1.14.0"}))
        z.writestr("ecf_server/data/jev_mapping.json", '{"bands": {"phishing": 0.4}}')
    rel = upgrade_check.read_wheel(path)
    assert rel.pins["jev"] == "jev-1.14.0"
    assert rel.pins["jev_mapping"] == upgrade_check.mapping_digest({"bands": {"phishing": 0.4}})
    fams, hit = upgrade_check.changes({"jev": PIN}, None, {"jev": "jev-1.14.0"}, None,
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
        conn.execute("UPDATE items SET status = 'observed' WHERE stable_id = ?", (sid,))
    jev_keys.WAKE.clear()
    reprocess._move(conn, clock, row(conn, sid), "reclassify", key)  # pyright: ignore[reportPrivateUsage]
    r = row(conn, sid)
    assert r["status"] == "awaiting_claude" and r["classification"] is None
    assert jev_keys.WAKE.is_set() and claude_queue.waiting_for_jev(conn) == {"d": 1}
    assert jev_dispatch.claimable(conn, clock.now()) == 1
    jev_keys.WAKE.clear()


# The model watch records TypeSafe's aliases (hand-off 6).


def test_the_watch_records_jevs_aliases(conn: sqlite3.Connection, clock: FakeClock,
                                        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from ecf_server import health  # noqa: PLC0415

    def online(_host: str) -> bool:
        return True

    monkeypatch.setattr(health, "resolves", online)
    seen: list[httpx.Request] = []

    def answer(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"models": [
            {"name": "jev-latest", "description": "x", "release_date": "2026-09-30"}]})  # fmt: skip

    s = MemorySecretStore()
    s.set(jev_keys.KEY_SECRET, KEY)
    model_watch.run(conn, clock, FakeNotifier(), s,
                    lambda: httpx.Client(transport=httpx.MockTransport(answer)))  # fmt: skip
    assert [str(r.url) for r in seen] == [model_watch.JEV_MODELS_URL]
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    st = model_watch.status(conn)
    assert st["jev"]["aliases"] == {"jev-latest": "2026-09-30"} and st["jev_pin"] == PIN
    from ecf.cli_models import watch_lines  # noqa: PLC0415

    assert any("jev-latest released 2026-09-30" in x for x in watch_lines(st))


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
