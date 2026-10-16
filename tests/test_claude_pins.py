"""The pinned Claude models, the family override and what each preset's gate binds to (V1.4 step 2;
SPEC §7.5, §9.3; OD-014). Presets A and B, and the local key, went in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf.plugin_digest import actor_prompt_digest
from ecf.schema import load_schema
from ecf_server import (
    claude_pins,
    claude_review,
    db,
    gate,
    review,
    signals,
    stages,
    stepup,
)
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_decide import KNOWN_BULK, MARKETING, make_classified
from tests.test_dev_mode import start_dev
from tests.test_gate import make_address

from .conftest import stop, wait_answering

DIGEST = "6114515d" + "0" * 56  # an Ollama digest, as items before V1.4 recorded it
LOCAL = f"{DIGEST}+schema-x+0123456789abcdef"  # preset A's key before v3.0.0 (OD-475)
NEW_OPUS = "claude-opus-5-6"


def _preset(conn: sqlite3.Connection, preset: str, stage: str | None = None) -> None:
    with write_tx(conn):
        conn.execute("UPDATE addresses SET preset = ?, stage = coalesce(?, stage)",
                     (preset, stage))  # fmt: skip


def _override(conn: sqlite3.Connection, clock: FakeClock, value: str) -> dict[str, Any]:
    notifier = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        claude_pins.set_override(conn, clock, notifier, value, nonce=None)
    target = ei.value.extra["target"]
    issued = stepup.issue(conn, clock, FakeStepper(), "claude_model_override", target)
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return claude_pins.set_override(conn, clock, notifier, value, nonce=issued.nonce_id)


def test_the_lock_names_every_role_with_a_claude_id() -> None:
    lock = claude_pins.load_lock()
    assert set(lock) == set(claude_pins.ROLES)
    assert lock["classifier"] == "claude-opus-5-5"
    assert lock["main_session"] == "claude-haiku-5-5"  # the session only, OD-474
    roles = {claude_pins.family(lock[r]) for r in claude_pins.ROLES if r != "main_session"}
    assert roles == {"opus"}  # every gate role (OD-520, OD-521); Haiku acts on nothing (OD-461)
    assert set(claude_pins.lifecycle()) >= set(lock.values())
    assert claude_pins.family("gpt-5") is None
    assert claude_pins.family("claude-sonnet-5-5; rm") is None


def test_each_preset_binds_what_it_uses(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert claude_pins.GATE_ROLES == {"C": ("classifier", "classifier_high", "actor",
                                            "actor_high"),
                                      "D": ("actor", "actor_high")}  # step 4  # fmt: skip
    assert claude_pins.PAIR == {"C": "claude/claude", "D": "jev/claude"}
    c = claude_pins.pins(conn, "C")
    assert set(c) == {"classifier", "classifier_high", "actor", "actor_high", "schema", "prompt",
                      "actor_prompt"}  # fmt: skip
    assert c["prompt"] == claude_review.prompt_digest(load_schema())[:16]  # Q4
    assert c["actor_prompt"] == actor_prompt_digest()[:16]  # OD-523
    d = claude_pins.pins(conn, "D")  # held back from v3.0.0 (OD-516); its actor is Claude's
    assert d["actor_prompt"] == c["actor_prompt"]
    assert claude_pins.key(c).startswith("pins-")
    assert claude_pins.key({"digest": DIGEST}) == DIGEST  # a pre-V1.4 record still reads
    for gone in ("A", "B"):
        with pytest.raises(KeyError):
            claude_pins.pins(conn, gone)


def test_the_override_replaces_its_family_with_step_up_and_a_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    before = claude_pins.address_key(conn, "ap")
    out = _override(conn, clock, NEW_OPUS)
    eff = claude_pins.effective(conn)
    assert eff["classifier"] == eff["classifier_high"] == eff["actor"] == eff["actor_high"]
    assert eff["actor"] == NEW_OPUS
    assert eff["main_session"] == "claude-haiku-5-5"  # other families keep their pin
    assert out["affected"] == ["ap@acme.example"]
    assert claude_pins.address_key(conn, "ap") != before
    posts = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]
    assert any("Security Notice" in p["card"]["title"] and NEW_OPUS in p["card"]["text"]
               for p in posts)  # fmt: skip
    audit = conn.execute("SELECT data FROM audit WHERE event = 'models.override_changed'")
    assert json.loads(audit.fetchone()[0]) == {"from": {}, "to": {"opus": NEW_OPUS}}
    with pytest.raises(ConflictError):
        claude_pins.set_override(conn, clock, FakeNotifier(), NEW_OPUS, nonce=None)
    _override(conn, clock, "claude-opus-5-5")  # the pinned ID: back to the release's pin
    assert claude_pins.overrides(conn) == {}
    assert claude_pins.address_key(conn, "ap") == before


def test_none_clears_and_bad_ids_are_refused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _override(conn, clock, NEW_OPUS)
    _override(conn, clock, "claude-haiku-5-6")
    assert claude_pins.overrides(conn) == {"haiku": "claude-haiku-5-6", "opus": NEW_OPUS}
    _override(conn, clock, "none")
    assert claude_pins.effective(conn) == claude_pins.load_lock()
    for bad in ("gpt-5", "claude-3", ""):
        with pytest.raises(InvalidInputError):
            claude_pins.set_override(conn, clock, FakeNotifier(), bad, nonce=None)
    with pytest.raises(InvalidInputError, match="ecf pins no sonnet model"):  # OD-520, OD-521
        claude_pins.set_override(conn, clock, FakeNotifier(), "claude-sonnet-5-5", nonce=None)


def test_a_haiku_override_replaces_only_the_main_session(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    _preset(conn, "C")
    before = claude_pins.address_key(conn, "ap")
    out = _override(conn, clock, "claude-haiku-5-6")
    eff = claude_pins.effective(conn)
    assert eff["main_session"] == "claude-haiku-5-6"
    assert eff["classifier"] == "claude-opus-5-5"
    assert out["affected"] == []  # not a gate pin (OD-474): nothing goes back to assist
    assert claude_pins.address_key(conn, "ap") == before
    posts = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]
    assert not any("go back to assist" in p["card"]["text"] for p in posts)


def test_a_step_up_is_bound_to_the_override_it_was_issued_for(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    with pytest.raises(StepupRequiredError) as ei:
        claude_pins.set_override(conn, clock, FakeNotifier(), NEW_OPUS, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "claude_model_override",
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        claude_pins.set_override(conn, clock, FakeNotifier(), "claude-opus-5-7",
                                 nonce=issued.nonce_id)  # fmt: skip
    assert claude_pins.overrides(conn) == {}


def test_reviews_count_per_pin_key(conn: sqlite3.Connection, clock: FakeClock) -> None:
    make_address(conn, clock, "assist")
    b_key = claude_pins.address_key(conn, "ap")  # C's (every address from v3.0.0, ADR 0031)
    for i, key in enumerate((b_key, LOCAL, None)):
        sid = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid=f"k{i}")
        pinned = {"digest": DIGEST} | ({"pin_key": key} if key else {})
        with write_tx(conn):
            conn.execute("UPDATE items SET pinned_models = ?, review = ? WHERE stable_id = ?",
                         (json.dumps(pinned), json.dumps({"verdict": "correct",
                          "category_ok": True}), sid))  # fmt: skip
    assert review.reviewed(conn, "ap", b_key)["reviewed"] == 1
    assert review.reviewed(conn, "ap", LOCAL)["reviewed"] == 1  # an A key, before v3.0.0
    assert review.reviewed(conn, "ap", DIGEST)["reviewed"] == 1  # no pin_key: before V1.4, and
    # before v2's schema, so it no longer counts for the gate (OD-475)
    g = gate.compute(conn, "ap")
    assert (g.digest, g.reviewed, g.preset) == (b_key, 1, "C")
    assert dict(g.pins)["actor"] == "claude-opus-5-5"
    synthetic = next(c for c in g.checks if c.name == "synthetic")
    assert not synthetic.ok and "/ecf-eval" in synthetic.detail


def test_an_override_drops_a_live_claude_address_to_assist(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    _preset(conn, "C", "live")
    g = gate.compute(conn, "ap")
    with write_tx(conn):
        gate.record(conn, g, clock.now(), passed=True)
    row = gate.stored(conn, "ap")
    assert row is not None and row["pair_key"] == "claude/claude"
    assert gate.stored_key(row) == g.digest
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "live"  # unchanged pins
    _override(conn, clock, "claude-opus-5-6")  # every C gate role
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"


def test_a_change_to_the_actors_instructions_drops_a_live_address_to_assist(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v3.0.1 (OD-523): `agents/actor.md` is in the gate key, so a release that edits it starts
    every gate again."""
    make_address(conn, clock, "assist")
    _preset(conn, "C", "live")
    g = gate.compute(conn, "ap")
    with write_tx(conn):
        gate.record(conn, g, clock.now(), passed=True)
    before = claude_pins.address_key(conn, "ap")
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "live"
    monkeypatch.setattr(claude_pins, "actor_prompt_digest", lambda: "e" * 64)
    assert claude_pins.address_key(conn, "ap") != before
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"


def test_the_override_from_the_cli(home: Path) -> None:
    """`ecf settings set claude_model_override` end to end, through the dev service's fake
    step-up; `ecf models status` shows the pins and the override."""
    p = Paths("dev", home)
    proc = start_dev(home)
    try:
        wait_answering(p, proc)
        cli = CliRunner()
        out = cli.invoke(app, ["--install", "dev", "settings", "set", "claude_model_override",
                               NEW_OPUS])  # fmt: skip
        assert out.exit_code == 0, out.output
        assert "Step-up: ecf: run Claude reviews with opus -> claude-opus-5-6" in out.output
        assert f"actor {NEW_OPUS}" in out.output
        bad = cli.invoke(app, ["--install", "dev", "settings", "set", "claude_model_override",
                               "gpt-5"])  # fmt: skip
        assert bad.exit_code != 0
        st = cli.invoke(app, ["--install", "dev", "models", "status"])
        assert f"override: opus -> {NEW_OPUS}" in st.output
        assert "main_session claude-haiku-5-5" in st.output
    finally:
        stop(proc)


@pytest.mark.usefixtures("extensions_on")
def test_user_fields_move_the_claude_classifier_keys(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    """S4 (D3; OD-502): C's pins and an unpinned C eval key hold the whole effective schema
    digest, so your own fields move them."""
    from ecf_server import claude_eval, config  # noqa: PLC0415
    from tests.test_claude_review import extend  # noqa: PLC0415

    def c_keys() -> list[str]:
        c = claude_eval.Options(tmp_path, preset="C", actor_model=NEW_OPUS)
        return [claude_pins.key(claude_pins.pins(conn, "C")), claude_eval.models_for(conn, c)[2]]

    before_c = c_keys()
    extend(conn, clock)
    assert all(a != b for a, b in zip(before_c, c_keys(), strict=True))
    assert claude_pins.pins(conn, "C")["schema"] == config.current_schema(conn).digest


def test_the_c_key_moves_with_what_claude_is_asked(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q4: the C pin key takes a Claude-side digest (the schema and the signals' wording), so a
    signal reworded restarts C's gate."""

    def c_key() -> str:
        return claude_pins.key(claude_pins.pins(conn, "C"))

    before = c_key()
    reworded = signals.WORDING | {"crypto_payment": "Pay this wallet."}
    monkeypatch.setattr(signals, "WORDING", reworded)
    assert c_key() != before


@pytest.mark.parametrize(("stored", "left"), [
    ({"sonnet": "claude-sonnet-5-6"}, None),
    ({"opus": NEW_OPUS, "sonnet": "claude-sonnet-5-6"}, {"opus": NEW_OPUS}),
    ({"haiku": "claude-haiku-5-6"}, {"haiku": "claude-haiku-5-6"}),
    (None, None),
])  # fmt: skip
def test_0041_removes_a_stored_sonnet_override(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, stored: dict[str, str] | None,
    left: dict[str, str] | None,
) -> None:  # fmt: skip
    """OD-520, OD-521: no Sonnet pin from v3.0.0, so a `sonnet` override would be orphaned; the
    other families' overrides stay, and the change is audited."""
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 41])
    c = db.connect(db_path)
    db.migrate(c)
    if stored is not None:
        c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 't',"
                  " 'os_user')", (claude_pins.OVERRIDE_KEY, json.dumps(stored)))  # fmt: skip
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    assert db.migrate(c) == ["0041_sonnet_override_removed.sql"]
    assert claude_pins.overrides(c) == (left or {})
    row = c.execute("SELECT 1 FROM settings WHERE key = ?", (claude_pins.OVERRIDE_KEY,))
    assert (row.fetchone() is not None) == (left is not None)
    audit = [json.loads(r[0]) for r in c.execute("SELECT data FROM audit WHERE event ="
                                                 " 'models.override_changed' AND actor ="
                                                 " 'migration'")]  # fmt: skip
    assert audit == ([{"from": stored, "to": left or {}}] if stored and "sonnet" in stored
                     else [])  # fmt: skip
    assert db.migrate(c) == []
    c.close()
