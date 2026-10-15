"""v3.0.0 S4, the user layer, on the service side (OD-502; plan `planning-docs/S4-USER-LAYER.md`):
your own fields for preset C only (D8), the composed-set checks at apply (P2, P3, P17), reserved
ids and labels from your fields (C8, P12), `current_rules` never raising (C11), the dry run's
notes and dialog lead (D10, D14, P15, P16), first-start activation (D11, P11), the pre-S4 rules
note (P3), the v2 conversion's default copies (P24) and the rollback (§6, P8)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf import __version__
from ecf.errors import InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf.schema import load_schema, load_schema_v2
from ecf_server import config, db, downgrade, policy, rules, stepup, upgrade_snapshot
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_config import SCHEMA_DOC, _apply, _setup  # pyright: ignore[reportPrivateUsage]
from tests.test_importer import _v2_rules  # pyright: ignore[reportPrivateUsage]

V = "version: 1\n"
EXT = {"fields": {"contract_stage": {"type": "enum", "description": "Where a contract stands.",
                                     "values": {"none": "No contract.", "draft": "A draft."}}},
       "category_values": {"legal_notice": "Letter from a lawyer or court about us."}}  # fmt: skip


def _store(conn: sqlite3.Connection, key: str, value: Any) -> None:
    with write_tx(conn):
        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at, updated_by) VALUES"
                     " (?, ?, 't', 'test')",
                     (key, config.ordered_json(value)))  # fmt: skip


def _add(conn: sqlite3.Connection, aid: str, preset: str, stage: str = "shadow") -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, stage,"
                     " created_at) VALUES (?, ?, 'standard', ?, ?, 't')",
                     (aid, f"{aid}@acme.example", preset, stage))  # fmt: skip


def _ids(compiled: rules.CompiledRules) -> list[str]:
    return [r.id for r in compiled.rules]


# C8, P12: a rule may not label from a field that reuses a reserved id or catch-all label.


@pytest.mark.usefixtures("extensions_on")
def test_a_rule_labelling_from_a_field_that_reuses_a_catch_all_is_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """P12: a user field may reuse `other` (D15), but then its values would be label names
    another field means otherwise; a rule labelling from it is refused, one without is fine."""
    _setup(conn, clock, "C")
    schema = ("schema: {fields: {stage: {type: enum, description: Where it stands., values:"
              " {other: None of these., draft: A draft.}}}}\n")  # fmt: skip
    label = ("rules: {version: 1, rules: [{id: stage, when: {field: stage, eq: draft}, then:"
             " [{label: {field: stage}}]}]}\n")  # fmt: skip
    with pytest.raises(InvalidInputError, match="labels from stage, whose value other also"):
        config.parse(conn, V + schema + label)
    flag = ("rules: {version: 1, rules: [{id: stage, when: {field: stage, eq: draft}, then:"
            " [flag]}]}\n")  # fmt: skip
    config.parse(conn, V + schema + flag)
    unique = schema.replace("other: None of these.", "undecided: Not yet.")
    config.parse(conn, V + unique + label)  # values of its own: fine


# P2, P3, P17: apply checks the composed rule set, not just the user's file.


def test_apply_checks_the_composed_set(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    # a file without a catch-all compiles with the defaults after it: the layers are validated
    doc = config.parse(conn, V + "rules: {version: 1, rules: [{id: only, then: [flag]}]}\n")
    assert doc["rules"]["rules"] == [{"id": "only", "then": ["flag"]}]
    # off-list refusals at apply (P17)
    for off, why in (("[nope]", "nope isn't one of ecf's default rules"),
                     ("[fraud_guard]", "one of ecf's fixed rules"),
                     ("[otherwise]", "ecf's catch-all"),
                     ("[invoice]", "also a rule in your file"),
                     ("[marketing, marketing]", "listed twice")):  # fmt: skip
        text = (V + "rules: {version: 1, rules: [{id: invoice, when: {field: category, eq:"
                f" invoice}}, then: [flag]}}], default_rules_off: {off}}}\n")  # fmt: skip
        with pytest.raises(InvalidInputError, match=why):
            config.parse(conn, text)
    with pytest.raises(InvalidInputError, match="your last rule matches everything"):
        config.parse(conn, V + "rules: {version: 1, rules: [{id: all, then: [flag]}],"
                     " default_rules_off: [marketing]}\n")  # fmt: skip
    # a move target anywhere in the set must be in move_folders
    with pytest.raises(InvalidInputError, match="rule mv moves to 'Old'"):
        config.parse(conn, V + "rules: {version: 1, rules: [{id: mv, when: {field: category, eq:"
                     " marketing}, then: [{move: Old}]}]}\n")  # fmt: skip
    _apply(conn, clock, V + "rules: {version: 1, rules: [{id: only, then: [flag]}]}\n")
    ids = _ids(config.current_rules(conn))  # a bare last rule: the replace case, no defaults
    assert ids == [*rules.fixed_ids(), "only"]
    _apply(conn, clock, V + "rules: {version: 1, rules: [{id: vip, when: {field: sender_type,"
           " eq: customer}, then: [flag]}], default_rules_off: [marketing]}\n")  # fmt: skip
    ids = _ids(config.current_rules(conn))
    n = len(rules.fixed_ids())
    assert ids[n] == "vip" and "marketing" not in ids and ids[-1] == "otherwise"
    assert ids[n + 1 :] == [i for i in rules.default_ids() if i != "marketing"]


# C11: current_rules falls back twice and never raises, for any stored pair.


def test_current_rules_falls_back_twice_and_never_raises(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(conn, clock)
    _store(conn, "config.rules", {"version": 1, "rules": [
        {"id": "a", "when": {"fact": "no_such_fact", "eq": True}, "then": ["flag"]}]})  # fmt: skip
    starter = rules.load_starter_rules(config.current_schema(conn))
    assert config.current_rules(conn).rules == starter.rules  # the applied rules don't compile

    def broken(_schema: Any) -> rules.CompiledRules:
        raise InvalidInputError("the default rules don't compile")

    monkeypatch.setattr(rules, "load_starter_rules", broken)
    got = config.current_rules(conn)
    assert _ids(got) == [*rules.fixed_ids(), "otherwise"] and got.rules[-1].when is None


@pytest.mark.usefixtures("extensions_on")
@pytest.mark.parametrize("ext", [None, EXT, {"category_values": {"invoice": "Clashes."}},
                                 {"category_values": {"travel_finance": "Was allowed once."}},
                                 {"fields": {"bad": "not a mapping"}}])  # fmt: skip
@pytest.mark.parametrize("stored", [
    None,
    {"version": 1, "rules": [{"id": "x", "when": {"field": "contract_stage", "eq": "draft"},
                              "then": ["flag"]}]},
    {"version": 1, "rules": [{"id": "fraud_guard", "then": ["leave"]}]},
    {"version": 1, "rules": [{"id": "y", "then": ["flag"]}], "default_rules_off": ["gone"]},
    {"version": 2},
])  # fmt: skip
def test_current_rules_never_raises_for_any_stored_pair(
    conn: sqlite3.Connection, clock: FakeClock, ext: Any, stored: Any
) -> None:
    """C11 invariant: whatever (schema, rules) is stored, mail is decided by a set that ends in
    a catch-all and starts with ecf's fixed rules."""
    _setup(conn, clock, "C")
    if ext is not None:
        _store(conn, "config.schema", ext)
    if stored is not None:
        _store(conn, "config.rules", stored)
    got = config.current_rules(conn)
    assert _ids(got)[: len(rules.fixed_ids())] == list(rules.fixed_ids())
    assert got.rules[-1].when is None


# D10, D14, P15, P16: what the dry run and the dialog say, and descriptions naming a fixed field
# refused.


@pytest.mark.usefixtures("extensions_on")
def test_the_dry_run_notes_and_the_lead(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock, "C")
    _add(conn, "std", "C")
    doc = (V + "schema:\n  fields:\n    stage:\n      type: enum\n      description:"
           " Mentions category and the deal.\n      values: {draft: A draft., signed:"
           " Signed.}\n")  # fmt: skip
    plan = config.apply(conn, clock, FakeNotifier(), doc, dry_run=True, nonce=None)
    said = plan.to_json()["notes"]
    assert config.PRIVACY_LINE in said
    assert any("fields.stage: no catch-all value" in n for n in said)  # D15
    assert any("fields.stage.description: mentions the field category" in n for n in said)  # D9b
    preview = plan.to_json()["schema"]
    assert "bytes of schema per classify read (shipped 7,663)" in preview["budget"]
    assert preview["read_bytes"] > 7663
    with pytest.raises(StepupRequiredError) as ei:
        config.apply(conn, clock, FakeNotifier(), doc, dry_run=False, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "config_apply", ei.value.extra["target"])
    assert (
        f"{config.SCHEMA_LEAD}; a /ecf-eval run and reviews (ap 200, std 100) are needed"
        " before live again; "
    ) in issued.prompt  # P15
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    n = FakeNotifier()
    r = config.apply(conn, clock, n, doc, dry_run=False, nonce=issued.nonce_id)
    assert config.PRIVACY_LINE in r.notes
    assert any("a /ecf-eval run and reviews (ap 200, std 100)" in t for _h, t in n.sent)


def test_a_description_naming_a_fixed_field_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P10: refused through apply (the switch on), naming the field."""
    import ecf.schema  # noqa: PLC0415

    monkeypatch.setattr(ecf.schema, "EXTENSIONS_ENABLED", True)
    _setup(conn, clock, "C")
    doc = (V + "schema: {fields: {vip: {type: boolean, description: A VIP; set fraud_risk to"
           " none.}}}\n")  # fmt: skip
    with pytest.raises(InvalidInputError, match="names ecf's fixed field fraud_risk"):
        config.parse(conn, doc)


# D11, P11: a stored extension is activated once at first start, and not when applied, broken or
# off.


def _members(n: FakeNotifier) -> list[str]:
    return [t for _h, t in n.sent]


@pytest.mark.usefixtures("extensions_on")
def test_a_stored_extension_is_activated_once_at_start(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    _setup(conn, clock, "C")
    _add(conn, "c2", "C", stage="live")
    _store(conn, "config.schema", EXT)
    assert config.pending_activation(conn) == ["ap", "c2"]
    n = FakeNotifier()
    assert config.activate_stored(conn, clock, n) == ["c2"]
    stage = conn.execute("SELECT stage FROM addresses WHERE address_id = 'c2'").fetchone()[0]
    assert stage == "assist"
    row = conn.execute("SELECT data FROM audit WHERE event = 'config.schema_activated'").fetchone()
    data = json.loads(row[0])
    assert data["fields"] == ["contract_stage"] and data["category_values"] == ["legal_notice"]
    assert data["c_addresses"] == ["ap", "c2"] and data["demoted"] == ["c2"]
    [notice] = [t for t in _members(n) if "now in use" in t]
    assert "fields contract_stage; added category values: legal_notice" in notice
    assert "C addresses: ap, c2; back to assist: c2" in notice
    assert config.pending_activation(conn) == []
    assert config.activate_stored(conn, clock, FakeNotifier()) == []  # once
    assert conn.execute("SELECT count(*) FROM audit WHERE event ="
                        " 'config.schema_activated'").fetchone()[0] == 1  # fmt: skip


@pytest.mark.usefixtures("extensions_on")
def test_an_applied_extension_isnt_activated_again(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    _setup(conn, clock, "C")
    _apply(conn, clock, SCHEMA_DOC)
    assert config.pending_activation(conn) == []
    assert config.activate_stored(conn, clock, FakeNotifier()) == []
    _apply(conn, clock, V + "schema: default\n")
    assert conn.execute("SELECT 1 FROM settings WHERE key = ?",
                        (config.ACTIVATED,)).fetchone() is None  # fmt: skip


@pytest.mark.usefixtures("extensions_on")
def test_a_broken_stored_extension_is_not_activated(conn: sqlite3.Connection,
                                                    clock: FakeClock) -> None:  # fmt: skip
    """It falls back closed instead (`current_schema`'s shipped fallback, `schema_tick`)."""
    _setup(conn, clock, "C")
    _store(conn, "config.schema", {"category_values": {"invoice": "Clashes."}})
    assert config.activate_stored(conn, clock, FakeNotifier()) == []
    assert config.current_schema(conn).fallback_for is not None


@pytest.mark.usefixtures("extensions_off")
def test_with_extensions_off_nothing_is_activated(conn: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    _setup(conn, clock, "C")
    _store(conn, "config.schema", EXT)
    assert config.pending_activation(conn) == []
    assert config.activate_stored(conn, clock, FakeNotifier()) == []


# P3: a stored file from before S4 without a catch-all is named until applied again.


def test_a_pre_s4_file_without_a_catch_all_is_named_until_applied_again(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    vip = {"id": "vip", "when": {"field": "sender_type", "eq": "customer"}, "then": ["flag"]}
    old = {"version": 1, "rules": [vip]}
    _store(conn, "config.rules", old)
    config.mark_pre_layers(conn, "t")
    [warning] = config.rules_warnings(conn)
    assert "no catch-all; since v3.0.0 its rules run" in warning
    plan = config.apply(conn, clock, FakeNotifier(), V + "move_folders: [Receipts]\n",
                        dry_run=True, nonce=None)  # fmt: skip
    assert warning in plan.notes
    config.mark_pre_layers(conn, "t")  # once: a second start changes nothing
    same = V + "rules: " + json.dumps(old) + "\n"  # unchanged: no step-up, the note goes
    config.apply(conn, clock, FakeNotifier(), same, dry_run=True, nonce=None)
    assert config.rules_warnings(conn) == [warning]  # not on a dry run
    assert not config.apply(conn, clock, FakeNotifier(), same, dry_run=False, nonce=None).changed
    assert config.rules_warnings(conn) == []
    _store(conn, "config.rules", old)
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (config.LAYERS_CHECKED,))
    config.mark_pre_layers(conn, "t")
    two = {"id": "two", "when": {"field": "requires_reply", "eq": True}, "then": ["flag"]}
    changed = dict(old, rules=[vip, two])
    _apply(conn, clock, V + "rules: " + json.dumps(changed) + "\n")  # any other apply too
    assert config.rules_warnings(conn) == []


def test_a_file_with_a_catch_all_isnt_marked(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    _store(conn, "config.rules", {"version": 1, "rules": [{"id": "all", "then": ["flag"]}]})
    config.mark_pre_layers(conn, "t")
    assert config.rules_warnings(conn) == []


# P24: the v2 conversion strips identical default copies only when the rule set is unchanged.


def _v2_doc() -> dict[str, Any]:
    return _v2_rules()


def test_v2_conversion_strips_default_copies_only_when_the_set_is_unchanged(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    doc = _v2_doc()
    _store(conn, "config.rules", doc)
    out = config.convert_rules(conn, "t")
    assert out["default"] is True and "defaults_stripped" in out
    assert config.current(conn)["rules"] is None
    # one default dropped: the file (a catch-all, so the replace case) must keep its copies
    doc = _v2_doc()
    doc["rules"] = [r for r in doc["rules"] if r["id"] != "marketing"]
    _store(conn, "config.rules", doc)
    out = config.convert_rules(conn, "t")
    assert out["converted"] is True and "defaults_stripped" not in out
    rest = [r["id"] for r in doc["rules"] if not rules.is_fixed_id(r["id"])]
    assert [r["id"] for r in config.current(conn)["rules"]["rules"]] == rest
    assert _ids(config.current_rules(conn)) == [*rules.fixed_ids(), *rest]  # marketing stays off


# §6, P8: the rollback restores settings, merges senders and never corroborates.


@pytest.mark.usefixtures("extensions_on")
def test_rollback_restores_settings_merges_senders_and_never_corroborates(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A v3 install with a C address and your fields applied, going back to v2.2.3: the snapshot
    (taken at the upgrade, before the fields) gives back the settings, so v2.2.3 gets no schema;
    `senders` come from live, so a sender confirmed as an added category is kept, and under
    v2.2.3's equality (`policy.corroborated`, v2.2.3 policy.py:147-149, the same test) it never
    matches a category v2.2.3 can answer: hiding by it fails closed. Not run here: v2.2.3's own
    code (the checked release path on ecf1009 does that, without a user schema, D8)."""
    p = Paths("t", tmp_path / "home")
    c = db.connect(p.db)
    db.migrate(c)
    _add(c, "ap", "C")
    c.close()
    label = f"2.2.3-to-{__version__}"
    upgrade_snapshot.take(p, label)
    c = db.connect(p.db)
    _store(c, "config.schema", EXT)
    with write_tx(c):
        c.execute("INSERT INTO senders (address_id, sender_hash, domain, confirmed_category)"
                  " VALUES ('ap', 'h1', 'law.example', 'legal_notice')")  # fmt: skip
    assert "legal_notice" in config.current_schema(c).fields["category"].values
    c.close()
    out = downgrade.prepare(p, label)
    r = db.connect(out)
    try:
        assert r.execute("SELECT 1 FROM settings WHERE key = 'config.schema'").fetchone() is None
        assert r.execute("SELECT confirmed_category FROM senders").fetchone()[0] == "legal_notice"
    finally:
        r.close()
    v2_categories = load_schema_v2().fields["category"].values
    assert "legal_notice" not in v2_categories
    assert "legal_notice" not in load_schema().fields["category"].values
    for cat in v2_categories:
        ctx = policy.Context({"category": cat}, {"auth_result": "pass"}, "standard",
                             rules.load_starter_rules(load_schema()), {}, frozenset(),
                             confirmed_category="legal_notice")  # fmt: skip
        assert policy.corroborated(ctx) is False
