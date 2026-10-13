"""ecf's fixed rules (v3.0.0 step 1c S3a, OD-500; SPEC §8.6): composition and order, the refusal
of fixed ids and removed values, the appended catch-all, the S3a bridge clauses, the cache (P8),
the v2 applied-rules conversion (P7), the `fixed_item` helper and the rules test's stand-in."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from importlib import resources
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.schema import (
    FIXED_FROM_V2,
    FIXED_ITEMS,
    REMOVED_VALUES,
    extend_schema,
    fixed_item,
    load_schema,
    load_schema_v2,
    load_schema_v2_0_0,
)
from ecf.yamlio import load_yaml
from ecf_server import config, health, rules, ruletest
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.rules import RuleInput

SCHEMA = load_schema()
STARTER = rules.load_starter_rules(SCHEMA)
FIXED = ("fraud_guard", "regulatory", "fraud_weak", "unverified_payment_sender",
         "spam_or_phishing")  # fmt: skip
DEFAULTS = ("bug_report", "invoice", "payment_confirmation_remittance", "account_security",
            "shipping", "appointment_school_family", "travel_finance", "marketing",
            "automated_notification", "requires_reply", "otherwise")  # fmt: skip
BASE: dict[str, Any] = {"category": "other", "priority": "low", "requires_action": False,
                        "requires_reply": False, "payment_related": False,
                        "deadline_mentioned": False, "sender_type": "customer",
                        "fraud_risk": "none"} | dict.fromkeys(FIXED_ITEMS, False)  # fmt: skip
V2_SHA256 = "fda6a6987f2852629fd315126b2a64864163c362f760916020678c186519f257"


def _rule(cls: dict[str, Any], facts: dict[str, Any] | None = None,
          rule_set: rules.CompiledRules = STARTER) -> str:  # fmt: skip
    return rule_set.evaluate(RuleInput(BASE | cls, {"sender_origin": "internal"}
                                       | (facts or {}))).rule_id  # fmt: skip


def _v2_doc() -> dict[str, Any]:
    text = resources.files("ecf_server.data").joinpath("starter_rules_v2.yaml").read_text("utf-8")
    return dict(load_yaml(text, source="v2"))


# ---- composition ------------------------------------------------------------------------------


def test_the_fixed_rules_run_first_then_the_default_rules() -> None:
    assert rules.fixed_ids() == FIXED
    assert tuple(r.id for r in STARTER.rules) == FIXED + DEFAULTS
    applied = rules.compose(SCHEMA, "version: 1\nrules:\n  - {id: mine, then: [flag]}\n")
    assert tuple(r.id for r in applied.rules) == (*FIXED, "mine")


def test_the_junk_rule_comes_before_the_default_rules() -> None:
    """A phishing email answered as another category is junked, not caught by its category's
    default rule (the junk rule moved from 12th to 5th)."""
    assert _rule({"category": "account_security", "phishing": True}) == "spam_or_phishing"
    assert _rule({"category": "account_security"}) == "account_security"


def test_compose_puts_the_fixed_rules_before_a_file_that_would_hide_fraud() -> None:
    hide_all = "version: 1\nrules:\n  - {id: hide_all, then: [archive]}\n"
    composed = rules.compose(SCHEMA, hide_all)
    assert _rule({"fraud_risk": "high"}, rule_set=composed) == "fraud_guard"
    assert _rule({}, rule_set=composed) == "hide_all"


def test_the_digest_covers_both_files() -> None:
    data = resources.files("ecf_server.data")
    h = hashlib.sha256(data.joinpath("fixed_rules.yaml").read_bytes()
                       + data.joinpath("starter_rules.yaml").read_bytes())  # fmt: skip
    assert rules.starter_digest() == h.hexdigest()


@pytest.mark.parametrize("schema", [load_schema_v2(), load_schema_v2_0_0()],
                         ids=["v2", "v2.0.0"])  # fmt: skip
def test_the_fixed_rules_compile_on_a_schema_without_the_items(schema: Any) -> None:
    """Results asked with an older schema (`--no-new-wording`) lack the items: false (P13)."""
    old = rules.load_starter_rules(schema)
    no_items = {k: v for k, v in BASE.items() if k not in FIXED_ITEMS}
    inp = RuleInput(no_items | {"category": "vendor_change_request"}, {})
    assert old.evaluate(inp).rule_id == "fraud_guard"
    assert old.evaluate(RuleInput(no_items, {})).rule_id == "otherwise"


# ---- the fixed items and the S3a bridge (P1, P2, P3) -------------------------------------------


EXTERNAL_MONEY = {"sender_origin": "external", "payment_keyword": True}


@pytest.mark.parametrize(
    ("cls", "facts", "rule"),
    [
        ({"bank_detail_change": True}, {}, "fraud_guard"),
        ({"category": "vendor_change_request"}, {}, "fraud_guard"),  # bridge
        ({"claims_internal": True}, EXTERNAL_MONEY, "fraud_guard"),
        ({"sender_type": "team"}, EXTERNAL_MONEY, "fraud_guard"),  # bridge
        ({"regulator_claim": True}, {}, "regulatory"),
        ({"category": "regulatory"}, {}, "regulatory"),  # bridge
        ({"sender_type": "regulator"}, {}, "regulatory"),  # kept source (P1)
        ({"claims_internal": True}, {"sender_origin": "external"}, "fraud_weak"),
        ({"sender_type": "team"}, {"sender_origin": "external"}, "fraud_weak"),  # bridge
        ({"private_individual": True}, EXTERNAL_MONEY, "fraud_weak"),
        ({"sender_type": "person"}, EXTERNAL_MONEY, "fraud_weak"),
        ({"sender_type": "friend"}, EXTERNAL_MONEY, "fraud_weak"),  # kept source (P2)
        ({"sender_type": "family"}, EXTERNAL_MONEY, "fraud_weak"),  # kept source (P2)
        ({"phishing": True}, {}, "spam_or_phishing"),
        ({"category": "spam_or_phishing"}, {}, "spam_or_phishing"),  # bridge
        ({"claims_internal": True}, {}, "otherwise"),  # from inside: nothing
        ({"private_individual": True}, {"sender_origin": "external"}, "otherwise"),  # no money
    ],
)  # fmt: skip
def test_each_item_and_its_sources_reach_the_fixed_rule(cls: dict[str, Any],
                                                        facts: dict[str, Any],
                                                        rule: str) -> None:  # fmt: skip
    assert _rule(cls, facts) == rule


@pytest.mark.parametrize("item", FIXED_ITEMS)
def test_fixed_item_reads_the_item_or_any_source(item: str) -> None:
    assert fixed_item({item: True}, item)
    assert not fixed_item({item: False}, item) and not fixed_item({}, item)
    for field, values in FIXED_FROM_V2[item]:
        for v in values:
            assert fixed_item({field: v, item: False}, item), (field, v)  # raise only
    assert not fixed_item({"category": "invoice", "sender_type": "vendor"}, item)


# ---- refusals and the catch-all --------------------------------------------------------------


@pytest.mark.parametrize("rid", FIXED)
def test_a_file_cant_redefine_a_fixed_rule(rid: str) -> None:
    with pytest.raises(InvalidInputError, match=f"rule {rid} is one of ecf's fixed rules, run"
                                                " first; remove it from your file"):  # fmt: skip
        rules.compile_rules(f"version: 1\nrules:\n  - {{id: {rid}, then: [flag]}}\n", SCHEMA)


@pytest.mark.parametrize(("key", "use"), sorted(REMOVED_VALUES.items()))
def test_a_condition_on_a_removed_value_names_its_replacement(key: tuple[str, str],
                                                              use: str) -> None:  # fmt: skip
    field, value = key
    for cond in (f"{{field: {field}, eq: {value}}}", f"{{field: {field}, in: [{value}]}}",
                 f"{{not: {{field: {field}, eq: {value}}}}}"):  # fmt: skip
        text = f"version: 1\nrules:\n  - {{id: a, when: {cond}, then: [flag]}}\n"
        with pytest.raises(InvalidInputError, match=f"use `{use}`"):
            rules.compile_rules(text, SCHEMA)
    in_if = f"version: 1\nrules:\n  - {{id: a, then: [{{flag: true, if: {{field: {field}, eq: {value}}}}}]}}\n"  # noqa: E501
    with pytest.raises(InvalidInputError, match=f"use `{use}`"):
        rules.compile_rules(in_if, SCHEMA)


def test_otherwise_is_only_a_last_rule_with_no_when() -> None:
    for text in ("version: 1\nrules:\n  - {id: otherwise}\n  - {id: rest}\n",
                 "version: 1\nrules:\n  - {id: otherwise, when: {trigger: fraud}}\n"):  # fmt: skip
        with pytest.raises(InvalidInputError, match="only for a last rule with no `when`"):
            rules.compile_rules(text, SCHEMA)
    ok = rules.compile_rules("version: 1\nrules:\n  - {id: otherwise, then: [flag]}\n", SCHEMA)
    assert [r.id for r in ok.rules] == ["otherwise"]


def test_a_file_without_a_catch_all_gets_one_and_never_raises() -> None:
    composed = rules.compose(SCHEMA, "version: 1\nrules:\n  - {id: a, when: {trigger: fraud}}\n")
    assert composed.rules[-1].id == "otherwise" and composed.rules[-1].when is None
    d = composed.evaluate(RuleInput(BASE, {}))
    assert (d.rule_id, d.actions, d.to_actor) == ("otherwise", (), True)


# ---- cache (P8) --------------------------------------------------------------------------------


def test_the_cache_is_keyed_on_the_schema_and_the_text() -> None:
    a = "version: 1\nrules:\n  - {id: a, then: [flag]}\n"
    assert rules.compose(SCHEMA, a) is rules.compose(SCHEMA, a)
    assert rules.compose(SCHEMA, a) is not rules.compose(SCHEMA, a.replace("flag", "leave"))
    ext = extend_schema(SCHEMA, {"category_values": {"legal_notice": "Letter from a lawyer."}})
    other = rules.compose(ext, a)
    assert other is not rules.compose(SCHEMA, a) and other.schema is ext
    assert rules.load_fixed_rules(ext) is not rules.load_fixed_rules(SCHEMA)
    with pytest.raises(InvalidInputError):  # a refusal is never cached as a success
        rules.compose(SCHEMA, "version: 1\nrules:\n  - {id: fraud_guard}\n")
    with pytest.raises(InvalidInputError):
        rules.compose(SCHEMA, "version: 1\nrules:\n  - {id: fraud_guard}\n")


def test_current_rules_follow_a_change_to_the_stored_rules(conn: sqlite3.Connection,
                                                          clock: FakeClock) -> None:  # fmt: skip
    first = config.current_rules(conn)
    assert first is config.current_rules(conn)
    _store(conn, clock, {"version": 1, "rules": [{"id": "mine", "then": ["flag"]}]})
    assert [r.id for r in config.current_rules(conn).rules] == [*FIXED, "mine"]


# ---- the v2 applied rules (P7) ---------------------------------------------------------------


def _store(conn: sqlite3.Connection, clock: FakeClock, doc: dict[str, Any]) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.rules', ?, ?, 't') ON CONFLICT (key) DO UPDATE SET"
                     " value = excluded.value", (config.canonical_json(doc),
                                                 to_ts(clock.now())))  # fmt: skip


def test_the_frozen_v2_starter_file_is_v2s() -> None:
    """starter_rules.yaml was this file byte for byte at v2.0.0, v2.1.0, v2.2.0 and v2.2.3
    (checked with git 2026-10-13)."""
    raw = resources.files("ecf_server.data").joinpath("starter_rules_v2.yaml").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == V2_SHA256


def test_a_v2_applied_set_loses_only_its_unchanged_fixed_rules() -> None:
    doc = _v2_doc()
    doc["rules"].insert(4, {"id": "mine", "when": {"field": "category", "eq": "invoice"},
                            "then": ["flag"]})  # fmt: skip
    c = rules.convert_v2(doc)
    assert c.stripped == FIXED and not c.customised and c.doc is not None
    assert [r["id"] for r in c.doc["rules"]] == ["mine", *DEFAULTS]
    assert rules.convert_v2(c.doc).doc is None  # idempotent
    rules.compile_rules(json.dumps(c.doc), SCHEMA)  # now compiles


def test_a_customised_fixed_rule_stops_the_conversion() -> None:
    doc = _v2_doc()
    doc["rules"][0]["then"] = ["flag"]  # fraud_guard without escalate
    c = rules.convert_v2(doc)
    assert c.doc is None and c.customised == ("fraud_guard",)
    assert rules.convert_v2({"version": 1, "rules": [{"id": "x"}]}).doc is None  # not v2
    assert rules.convert_v2(None).doc is None


def test_convert_rules_strips_audits_and_is_idempotent(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    _store(conn, clock, _v2_doc())
    assert config.rules_problem(conn) is not None  # before: refused (fixed ids)
    r = config.convert_rules(conn, to_ts(clock.now()))
    assert r == {"converted": True, "stripped": list(FIXED)}
    assert config.rules_problem(conn) is None
    assert tuple(x.id for x in config.current_rules(conn).rules) == FIXED + DEFAULTS
    audit = conn.execute("SELECT data FROM audit WHERE event = 'config.rules_converted'").fetchall()
    assert [json.loads(a[0]) for a in audit] == [{"stripped": list(FIXED)}]
    assert config.convert_rules(conn, to_ts(clock.now())) == {"converted": False}
    assert len(conn.execute("SELECT 1 FROM audit WHERE event = 'config.rules_converted'")
               .fetchall()) == 1  # fmt: skip


def test_a_customised_fixed_rule_falls_back_to_the_starter_rules_with_a_system_error(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server.notify import FakeNotifier  # noqa: PLC0415

    doc = _v2_doc()
    doc["rules"][1]["then"] = ["flag"]  # regulatory, customised
    _store(conn, clock, doc)
    assert config.convert_rules(conn, to_ts(clock.now())) == {
        "converted": False,
        "customised": ["regulatory"],
    }
    assert config.current(conn)["rules"] == doc  # left as it is
    assert config.current_rules(conn).rules == STARTER.rules
    config.schema_tick(conn, clock, FakeNotifier())
    [alert] = [a for a in health.open_alerts(conn) if a["kind"] == config.SCHEMA_ALERT]
    assert "rule regulatory is a changed copy of one of ecf's fixed rules" in alert["detail"]
    assert "remove it and the other fixed rules (fraud_guard," in alert["detail"]


# ---- the rules test's stand-in -----------------------------------------------------------------


def test_the_rules_test_stand_in_reaches_fraud_guard_through_the_items() -> None:
    """Card labels, then hand-set fixed items, then derived ones (S2 review F4): a card hand-set
    `bank_detail_change: true` reaches fraud_guard whatever its category, through any proposed
    rules."""
    hand = ruletest.stand_in({**BASE, "category": "invoice"} | {"bank_detail_change": None},
                             {"bank_detail_change": True})  # fmt: skip
    assert hand["bank_detail_change"] is None  # a card-stated label wins
    labels = {k: v for k, v in BASE.items() if k not in FIXED_ITEMS} | {"category": "invoice"}
    got = ruletest.stand_in(labels, {"bank_detail_change": True})
    assert got["bank_detail_change"] is True
    proposed = rules.compose(SCHEMA, "version: 1\nrules:\n  - {id: leave_all, then: [leave]}\n")
    assert proposed.evaluate(RuleInput(got, {})).rule_id == "fraud_guard"
    derived = ruletest.stand_in(labels | {"category": "vendor_change_request"}, None)
    assert derived["bank_detail_change"] is True  # derived from the v2 label


# ---- diff review A4, A7 (2026-10-13) ----------------------------------------------------------


def test_a_refused_set_is_cached_as_refused_and_logged_once(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A4: a refused applied set isn't compiled again (nor logged) for every item."""
    text = "version: 1\nrules:\n  - {id: fraud_guard, then: [flag]}\n  - {id: refused_a4}\n"
    calls: list[str] = []
    real = rules.compile_rules

    def counting(t: str, schema: Any, *, source: str = "rules") -> rules.CompiledRules:
        calls.append(t)
        return real(t, schema, source=source)

    monkeypatch.setattr(rules, "compile_rules", counting)
    for _ in range(3):
        with pytest.raises(InvalidInputError, match="fixed rules"):
            rules.compose(SCHEMA, text)
    assert calls == [text]
    logged: list[str] = []
    monkeypatch.setattr(config.log, "error", lambda event, **_kw: logged.append(event))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    _store(conn, clock, {"version": 1, "rules": [{"id": "fraud_guard", "then": ["flag"]},
                                                 {"id": "rest_a4"}]})  # fmt: skip
    for _ in range(3):
        assert config.current_rules(conn).rules == STARTER.rules
    assert logged == ["config.rules_broken"]


def test_a_set_of_only_unchanged_fixed_rules_becomes_the_default(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A7: nothing would be left, so the default rules apply (`rules: default`), audited."""
    doc = _v2_doc()
    doc["rules"] = [r for r in doc["rules"] if r["id"] in FIXED]
    c = rules.convert_v2(doc)
    assert c.to_default and c.doc is None and c.stripped == FIXED
    _store(conn, clock, doc)
    r = config.convert_rules(conn, to_ts(clock.now()))
    assert r == {"converted": True, "stripped": list(FIXED), "default": True}
    assert config.current(conn)["rules"] is None
    assert config.current_rules(conn).rules == STARTER.rules
    [data] = [json.loads(a[0]) for a in conn.execute(
        "SELECT data FROM audit WHERE event = 'config.rules_converted'")]  # fmt: skip
    assert data == {"stripped": list(FIXED), "default": True}
    assert config.convert_rules(conn, to_ts(clock.now())) == {"converted": False}
