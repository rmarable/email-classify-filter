"""The pinned Claude models, the family override and what each preset's gate binds to (V1.4 step 2;
SPEC §7.5, §9.3; OD-014)."""

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
from ecf.schema import load_schema
from ecf_server import claude_pins, gate, ollama, review, stages, stepup
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_decide import KNOWN_BULK, MARKETING, make_classified
from tests.test_dev_mode import start_dev
from tests.test_gate import make_address

from .conftest import stop, wait_answering

DIGEST = ollama.load_pin().digest
LOCAL = claude_pins.local_key()  # the digest and the schema (OD-475)
NEW_SONNET = "claude-sonnet-5-6"


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
    assert lock["classifier"] == "claude-sonnet-5-5"
    assert lock["main_session"] == "claude-haiku-5-5"  # the session only, OD-474
    roles = {claude_pins.family(lock[r]) for r in claude_pins.ROLES if r != "main_session"}
    assert roles == {"sonnet", "opus"}  # Haiku classifies and acts on nothing (OD-461)
    assert set(claude_pins.lifecycle()) >= set(lock.values())
    assert claude_pins.family("gpt-5") is None
    assert claude_pins.family("claude-sonnet-5-5; rm") is None


def test_each_preset_binds_what_it_uses(conn: sqlite3.Connection, clock: FakeClock) -> None:
    lock = claude_pins.load_lock()
    a, b, c = (claude_pins.pins(conn, p) for p in "ABC")
    assert a == {"digest": LOCAL} and claude_pins.key(a) == LOCAL
    assert f"{DIGEST}+{load_schema().digest}" == LOCAL
    assert b == {"digest": LOCAL, "actor": lock["actor"], "actor_high": lock["actor_high"]}
    assert set(c) == {"classifier", "classifier_high", "actor", "actor_high", "schema"}
    assert len({claude_pins.key(p) for p in (a, b, c)}) == 3
    assert claude_pins.key(b).startswith("pins-")


def test_the_override_replaces_its_family_with_step_up_and_a_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    _preset(conn, "B")
    before = claude_pins.address_key(conn, "ap")
    out = _override(conn, clock, NEW_SONNET)
    eff = claude_pins.effective(conn)
    assert eff["actor"] == eff["classifier_high"] == NEW_SONNET
    assert eff["actor_high"] == "claude-opus-5-5"  # other families keep their pin
    assert out["affected"] == ["ap@acme.example"]
    assert claude_pins.address_key(conn, "ap") != before
    assert claude_pins.pins(conn, "A") == {"digest": LOCAL}  # A has no Claude pin
    posts = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]
    assert any("Security Notice" in p["card"]["title"] and NEW_SONNET in p["card"]["text"]
               for p in posts)  # fmt: skip
    audit = conn.execute("SELECT data FROM audit WHERE event = 'models.override_changed'")
    assert json.loads(audit.fetchone()[0]) == {"from": {}, "to": {"sonnet": NEW_SONNET}}
    with pytest.raises(ConflictError):
        claude_pins.set_override(conn, clock, FakeNotifier(), NEW_SONNET, nonce=None)
    _override(conn, clock, "claude-sonnet-5-5")  # the pinned ID: back to the release's pin
    assert claude_pins.overrides(conn) == {}
    assert claude_pins.address_key(conn, "ap") == before


def test_none_clears_and_bad_ids_are_refused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _override(conn, clock, NEW_SONNET)
    _override(conn, clock, "claude-opus-5-6")
    assert claude_pins.overrides(conn) == {"opus": "claude-opus-5-6", "sonnet": NEW_SONNET}
    _override(conn, clock, "none")
    assert claude_pins.effective(conn) == claude_pins.load_lock()
    for bad in ("gpt-5", "claude-3", ""):
        with pytest.raises(InvalidInputError):
            claude_pins.set_override(conn, clock, FakeNotifier(), bad, nonce=None)


def test_a_haiku_override_replaces_only_the_main_session(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    _preset(conn, "C")
    before = claude_pins.address_key(conn, "ap")
    out = _override(conn, clock, "claude-haiku-5-6")
    eff = claude_pins.effective(conn)
    assert eff["main_session"] == "claude-haiku-5-6"
    assert eff["classifier"] == "claude-sonnet-5-5"
    assert out["affected"] == []  # not a gate pin (OD-474): nothing goes back to assist
    assert claude_pins.address_key(conn, "ap") == before
    posts = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]
    assert not any("go back to assist" in p["card"]["text"] for p in posts)


def test_a_step_up_is_bound_to_the_override_it_was_issued_for(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    with pytest.raises(StepupRequiredError) as ei:
        claude_pins.set_override(conn, clock, FakeNotifier(), NEW_SONNET, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "claude_model_override",
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        claude_pins.set_override(conn, clock, FakeNotifier(), "claude-sonnet-5-7",
                                 nonce=issued.nonce_id)  # fmt: skip
    assert claude_pins.overrides(conn) == {}


def test_reviews_count_per_pin_key(conn: sqlite3.Connection, clock: FakeClock) -> None:
    make_address(conn, clock, "assist")
    _preset(conn, "B")
    b_key = claude_pins.address_key(conn, "ap")
    for i, key in enumerate((b_key, LOCAL, None)):
        sid = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid=f"k{i}")
        pinned = {"digest": DIGEST} | ({"pin_key": key} if key else {})
        with write_tx(conn):
            conn.execute("UPDATE items SET pinned_models = ?, review = ? WHERE stable_id = ?",
                         (json.dumps(pinned), json.dumps({"verdict": "correct",
                          "category_ok": True}), sid))  # fmt: skip
    assert review.reviewed(conn, "ap", b_key)["reviewed"] == 1
    assert review.reviewed(conn, "ap", LOCAL)["reviewed"] == 1  # an A key under this schema
    assert review.reviewed(conn, "ap", DIGEST)["reviewed"] == 1  # no pin_key: before V1.4, and
    # before v2's schema, so it no longer counts for the gate (OD-475)
    g = gate.compute(conn, "ap")
    assert (g.digest, g.reviewed, g.preset) == (b_key, 1, "B")
    assert dict(g.pins)["actor"] == "claude-sonnet-5-5"
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
    assert row is not None and row["pair_key"] == "claude/claude" and row["ollama_digest"] is None
    assert gate.stored_key(row) == g.digest
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "live"  # unchanged pins
    _override(conn, clock, "claude-opus-5-6")  # actor_high: a C pin
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"


def test_a_preset_a_gate_row_keeps_its_pre_v14_shape(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "assist")
    with write_tx(conn):
        gate.record(conn, gate.compute(conn, "ap"), clock.now(), passed=True)
    row = gate.stored(conn, "ap")
    assert row is not None and row["pinned_ids"] is None and row["ollama_digest"] == LOCAL
    assert row["pair_key"] == gate.PAIR


def test_the_override_from_the_cli(home: Path) -> None:
    """`ecf settings set claude_model_override` end to end, through the dev service's fake
    step-up; `ecf models status` shows the pins and the override."""
    p = Paths("dev", home)
    proc = start_dev(home)
    try:
        wait_answering(p, proc)
        cli = CliRunner()
        out = cli.invoke(app, ["--install", "dev", "settings", "set", "claude_model_override",
                               NEW_SONNET])  # fmt: skip
        assert out.exit_code == 0, out.output
        assert "Step-up: ecf: run Claude reviews with sonnet -> claude-sonnet-5-6" in out.output
        assert f"actor {NEW_SONNET}" in out.output
        bad = cli.invoke(app, ["--install", "dev", "settings", "set", "claude_model_override",
                               "gpt-5"])  # fmt: skip
        assert bad.exit_code != 0
        st = cli.invoke(app, ["--install", "dev", "models", "status"])
        assert f"override: sonnet -> {NEW_SONNET}" in st.output
        assert "actor_high claude-opus-5-5" in st.output
    finally:
        stop(proc)


@pytest.mark.usefixtures("extensions_on")
def test_a_schema_extension_changes_every_key_alike(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    """Preset A's and the fallback's gate, the B and C pins and unpinned eval keys all move to
    the effective schema's digest together (OD-478)."""
    from ecf_server import claude_eval, config, fallback  # noqa: PLC0415
    from tests.test_claude_review import extend  # noqa: PLC0415

    def keys() -> list[str]:
        unpinned = [claude_eval.Options(tmp_path, preset=p, actor_model=NEW_SONNET)
                    for p in ("B", "C")]  # fmt: skip
        return [gate.current_digest(conn), fallback.digest(conn),
                *(claude_pins.key(claude_pins.pins(conn, p)) for p in ("A", "B", "C")),
                *(claude_eval.models_for(conn, o)[2] for o in unpinned)]  # fmt: skip

    before = keys()
    extend(conn, clock)
    after = keys()
    assert all(a != b for a, b in zip(before, after, strict=True))
    effective = claude_pins.local_key(config.current_schema(conn))
    assert effective != LOCAL
    assert gate.current_digest(conn) == fallback.digest(conn) == effective
    assert claude_pins.pins(conn, "B")["digest"] == effective
    assert claude_pins.pins(conn, "C")["schema"] == config.current_schema(conn).digest
