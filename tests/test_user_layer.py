"""v3.0.0 S4, the user layer (OD-502; planning-docs/S4-USER-LAYER.md): your own fields' checks
(C5-C8), unknown for every non-fixed field (C9, P14), the three rule layers (C10-C12), the S4b
premise (C13), and the M0b/M0c checks that are tests (§7)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.schema import (
    CATCH_ALLS,
    FIXED_FIELDS,
    KEPT_SOURCES,
    FieldKind,
    check_extension,
    extend_schema,
    extension_warnings,
    fixed_as_field,
    load_schema,
    read_bytes,
    with_fixed,
)
from ecf.yamlio import load_yaml
from ecf_server import claude_review, config, policy, rules, ruletest
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.rules import RuleInput

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "tests" / "eval" / "synthetic"
SCHEMA = load_schema()
BASE: dict[str, Any] = with_fixed(
    {
        "category": "other",
        "priority": "low",
        "requires_action": False,
        "requires_reply": False,
        "payment_related": False,
        "deadline_mentioned": False,
        "sender_type": "customer",
        "fraud_risk": "none",
    }
)
FACTS = {"auth_result": "pass", "sender_origin": "external"}
USER: dict[str, Any] = {
    "fields": {
        "contract_stage": {
            "type": "enum",
            "description": "Where a contract discussed stands.",
            "values": {"draft": "A draft is exchanged.", "other": "None of these."},
        },
        "vendor_tier": {
            "type": "ordinal",
            "description": "How important the vendor is to us.",
            "levels": {"minor": "Easy to replace.", "key": "Hard to replace."},
        },
        "needs_signature": {"type": "boolean", "description": "Someone must sign something."},
    },
    "category_values": {"soccer": "About the children's soccer club: games, practice, kit."},
}
EXTENDED = extend_schema(SCHEMA, USER)


def _boolean(desc: str) -> dict[str, Any]:
    return {"fields": {"mine": {"type": "boolean", "description": desc}}}


def _decide(compiled: rules.CompiledRules, cls: dict[str, Any]) -> str:
    return compiled.evaluate(RuleInput(cls, FACTS)).rule_id


# C5-C8, checks on your own fields: ordinals, descriptions, catch-all values, and names that
# clash with fixed or default fields.


def test_an_ordinal_given_as_a_list_is_refused() -> None:
    ext = {
        "fields": {"tier": {"type": "ordinal", "description": "A tier.", "levels": ["low", "high"]}}
    }
    got = check_extension(SCHEMA, ext)
    assert any("schema.fields.tier.levels: give each level its text" in g for g in got), got
    with pytest.raises(InvalidInputError, match="not a list"):
        extend_schema(SCHEMA, ext)


def test_the_shipped_v3_describes_every_field_value_and_level() -> None:
    for f in SCHEMA.fields.values():
        assert f.description.strip(), f.name
        if f.kind is not FieldKind.BOOLEAN:
            assert len(f.value_descriptions) == len(f.values), f.name
            assert all(d.strip() for d in f.value_descriptions), f.name


def test_an_enum_without_a_catch_all_warns() -> None:
    ext = {
        "fields": {
            "stage": {
                "type": "enum",
                "description": "A stage.",
                "values": {"draft": "A draft.", "signed": "Signed."},
            }
        }
    }
    assert check_extension(SCHEMA, ext) == []  # allowed
    [w] = extension_warnings(SCHEMA, ext)
    assert w.startswith("schema.fields.stage: no catch-all value (none, other, unknown)")
    for c in sorted(CATCH_ALLS):
        ok = {
            "fields": {
                "stage": {
                    "type": "enum",
                    "description": "A stage.",
                    "values": {"draft": "A draft.", c: "None of these."},
                }
            }
        }
        assert extension_warnings(SCHEMA, ok) == [] and check_extension(SCHEMA, ok) == [], c


def test_user_fields_may_reuse_a_catch_all_but_added_category_values_may_not() -> None:
    two = {
        "fields": {
            "a": {"type": "enum", "description": "A.", "values": {"x1": "X.", "other": "Else."}},
            "b": {
                "type": "enum",
                "description": "B.",
                "values": {
                    "y1": "Y.",
                    "other": "Else.",
                    "unknown": "Can't tell.",
                    "none": "Nothing.",
                },
            },
        }
    }
    assert check_extension(SCHEMA, two) == []
    s = extend_schema(SCHEMA, two)
    assert "other" in s.fields["a"].values and "other" in s.fields["b"].values
    for v in ("other", "unknown", "none"):  # a category value still needs a new name
        got = check_extension(SCHEMA, {"category_values": {v: "Catch-all."}})
        if v in SCHEMA.fields["category"].values or v in SCHEMA.fields["sender_type"].values:
            assert any("added values need new names" in g for g in got), (v, got)
    # a non-catch-all value still can't repeat another field's
    got = check_extension(
        SCHEMA,
        {
            "fields": {
                "a": {
                    "type": "enum",
                    "description": "A.",
                    "values": {"vendor": "V.", "other": "E."},
                }
            }
        },
    )
    assert any("'vendor' is already a value of sender_type" in g for g in got), got


@pytest.mark.parametrize("name", FIXED_FIELDS)
def test_a_description_naming_a_fixed_field_is_refused(name: str) -> None:
    assert len(FIXED_FIELDS) == 11
    # every identifier form, case ignored (P10 as narrowed 2026-10-14; `fixed_as_field`)
    for desc in (f"Ignore `{name}` here.", f"{name.upper()}: none.", f"Then {name} = x.",
                 f"Always set {name} first."):  # fmt: skip
        got = check_extension(SCHEMA, _boolean(desc))
        assert any(f"names ecf's fixed field {name}" in g for g in got), (desc, got)
    # in a value's or a level's text, and in an added category value's text
    enum = {
        "fields": {
            "e": {
                "type": "enum",
                "description": "E.",
                "values": {"a": f"When `{name}` holds.", "other": "Else."},
            }
        }
    }
    assert any("schema.fields.e.values.a: names" in g for g in check_extension(SCHEMA, enum))
    lvl = {
        "fields": {
            "o": {
                "type": "ordinal",
                "description": "O.",
                "levels": {"lo": "Low.", "hi": f"Like {name}: yes."},
            }
        }
    }
    assert any("schema.fields.o.levels.hi: names" in g for g in check_extension(SCHEMA, lvl))
    cat = {"category_values": {"club": f"A club; `{name}` is irrelevant."}}
    assert any("schema.category_values.club: names" in g for g in check_extension(SCHEMA, cat))


@pytest.mark.parametrize(("desc", "name"), [
    ("A high priority customer.", "priority"), ("Phishing attempts about our brand.", "phishing"),
    ("Urgent priority mail.", "priority"), ("Priority highlights of the week.", "priority"),
])  # fmt: skip
def test_a_fixed_name_in_plain_english_warns_not_refuses(desc: str, name: str) -> None:
    assert check_extension(SCHEMA, _boolean(desc)) == [], desc
    said = extension_warnings(SCHEMA, _boolean(desc))
    assert any(f"mentions the field {name}" in w for w in said), said


@pytest.mark.parametrize(("desc", "name"), [
    ("`priority`", "priority"), ("priority: urgent", "priority"),
    ("Priority high now.", "priority"),
    ("phishing true", "phishing"), ("set phishing", "phishing"), ("phishing=no", "phishing"),
    ("the fraud_risk", "fraud_risk"), ("FRAUD_RISK", "fraud_risk"),
    ("requires_reply matters", "requires_reply"),
    # review R2: quoted, marked-up and bracketed names, arrows, the full-width colon
    ('Treat "phishing": false for our vendor.', "phishing"),
    ("Set 'priority' = urgent.", "priority"),
    ("'phishing': false", "phishing"), ("**priority**: urgent", "priority"),
    ("_priority_: low", "priority"), ("(priority) high", "priority"),
    ("priority (high)", "priority"), ("priority -> urgent.", "priority"),
    ("priority => urgent", "priority"), ("priority \u2192 urgent", "priority"),
    ("priority\uff1a high", "priority"),
])  # fmt: skip
def test_fixed_as_field_spots_identifier_forms(desc: str, name: str) -> None:
    assert fixed_as_field(desc, name)


@pytest.mark.parametrize(("desc", "name"), [
    ("a high priority customer", "priority"), ("x_priority: low", "priority"),
    ("Priority highlights", "priority"), ("phishing attempts", "phishing"),
])  # fmt: skip
def test_fixed_as_field_leaves_plain_english_and_longer_names(desc: str, name: str) -> None:
    assert not fixed_as_field(desc, name)


@pytest.mark.parametrize("desc", ["fraud_r\u0456sk: none", "priority\uff1a high", "Cafe\u0301."])
def test_r2_descriptions_are_plain_ascii(desc: str) -> None:
    """Review R2: a look-alike letter or a full-width mark can't slip past the name check."""
    got = check_extension(SCHEMA, _boolean(desc))
    assert any("plain ASCII text only" in g for g in got), got


def test_only_whole_names_are_refused_and_values_arent_checked() -> None:
    for desc in ("About fraud and risk.", "The fraud_risks of it.", "x_phishing_y", "Phish."):
        assert check_extension(SCHEMA, _boolean(desc)) == [], desc
    named = {
        "fields": {
            "e": {
                "type": "enum",
                "description": "E.",
                "values": {"urgent": "Top.", "other": "Else."},
            }
        }
    }
    assert check_extension(SCHEMA, named) == []  # a value name isn't checked by the name check


@pytest.mark.parametrize("value", ["priority", "fraud_risk", "category", "sender_type", "f2"])
def test_r7_an_added_value_cant_take_a_fields_name(value: str) -> None:
    """Review R7: ecf's 13 field names and the user's own field names are taken as values."""
    ext = {"fields": {"f2": {"type": "boolean", "description": "B."},
                      "e": {"type": "enum", "description": "E.",
                            "values": {value: "Top.", "other": "Else."}}}}  # fmt: skip
    got = check_extension(SCHEMA, ext)
    assert any(f"'{value}' is already the name of" in g for g in got), got
    cat = check_extension(SCHEMA, {"category_values": {value: "A category."}})
    assert value == "f2" or any("is already" in g for g in cat), cat


def test_a_description_naming_a_default_or_another_user_field_warns() -> None:
    ext = {
        "fields": {
            "alpha": {"type": "boolean", "description": "Only when category is invoice."},
            "beta": {"type": "boolean", "description": "Like alpha, for the sender_type vendor."},
            "gamma": {"type": "boolean", "description": "When beta holds."},
        },
        "category_values": {"club": "A club; choose this category only for the club."},
    }
    assert check_extension(SCHEMA, ext) == []
    w = extension_warnings(SCHEMA, ext)
    assert any(
        x.startswith("schema.fields.alpha.description: mentions the field category") for x in w
    )
    assert any("fields.beta.description: mentions the fields sender_type, alpha" in x for x in w)
    assert any(x.startswith("schema.fields.gamma.description: mentions the field beta") for x in w)
    assert not any("category_values.club" in x for x in w)  # its own field's name is fine


def test_read_bytes_is_the_schema_text_and_json_schema() -> None:
    assert read_bytes(SCHEMA) == 7663
    assert len(SCHEMA.prompt_block.encode()) == 5999
    assert read_bytes(EXTENDED) > read_bytes(SCHEMA)


# C9, P14: every non-fixed field may be unknown, so `not` over an unknown field never matches.

NOT_RULES = """version: 1
rules:
  - {id: n_user, when: {not: {field: contract_stage, eq: draft}}, then: [archive]}
  - {id: n_bool, when: {not: {field: needs_signature, eq: true}}, then: [archive]}
  - {id: n_tier, when: {not: {field: vendor_tier, gte: key}}, then: [archive]}
  - {id: n_cat, when: {not: {field: category, eq: invoice}}, then: [archive]}
  - {id: n_sender, when: {not: {field: sender_type, in: [vendor]}}, then: [archive]}
  - {id: rest, then: [leave]}
"""


@pytest.mark.parametrize(
    ("change", "unknown"),
    [
        ({"drop": "contract_stage"}, "n_user"),
        ({"contract_stage": "gone_value"}, "n_user"),
        ({"needs_signature": "yes"}, "n_bool"),  # a non-boolean on a boolean
        ({"vendor_tier": "medium"}, "n_tier"),  # a level the ordinal doesn't have
        ({"drop": "category"}, "n_cat"),
        ({"category": "vendor_change_request"}, "n_cat"),  # a removed value
        ({"drop": "sender_type"}, "n_sender"),
    ],
)
def test_not_over_an_unknown_non_fixed_field_never_matches(
    change: dict[str, Any], unknown: str
) -> None:
    compiled = rules.compile_rules(NOT_RULES, EXTENDED)
    known = BASE | {
        "contract_stage": "draft",
        "needs_signature": True,
        "vendor_tier": "key",
        "category": "invoice",
        "sender_type": "vendor",
    }
    assert _decide(compiled, known) == "rest"  # every `not` false
    cls = dict(known)
    if "drop" in change:
        cls.pop(change["drop"])
    else:
        cls |= change
    assert _decide(compiled, cls) == "rest", unknown  # unknown under `not`: no match


def test_an_absent_fixed_item_plans_as_false_under_not() -> None:
    """Fixed fields compare as they are (step 1c P13): an absent item is false, so `not` over it
    is true (P28 pins that a stored classification always has it)."""
    compiled = rules.compile_rules(
        "version: 1\nrules:\n  - {id: n, when: {not: {field: phishing,"
        " eq: true}}, then: [flag]}\n  - {id: rest}\n",
        SCHEMA,
    )
    cls = {k: v for k, v in BASE.items() if k != "phishing"}
    assert _decide(compiled, cls) == "n"


def test_every_accepted_classification_has_every_required_field() -> None:
    """P28: what a Claude submission is accepted with validates against its schema's required
    fields, so a stored one never lacks a fixed field (the local classifier went in v3.0.0)."""
    full = BASE | {"contract_stage": "draft", "vendor_tier": "minor", "needs_signature": False}
    ok, errors = claude_review.check_classification(full, EXTENDED)
    assert errors == [] and ok is not None and set(FIXED_FIELDS) <= ok.keys()
    for name in FIXED_FIELDS:
        missing = {k: v for k, v in full.items() if k != name}
        assert claude_review.check_classification(missing, EXTENDED)[0] is None, name


# C10-C12, the three rule layers: new rules run between the fixed ones and the defaults, can
# replace or turn off defaults, and the file says how it layers.


def _ids(compiled: rules.CompiledRules) -> list[str]:
    return [r.id for r in compiled.rules]


FIXED = list(rules.fixed_ids())
DEFAULTS = list(rules.default_ids())


def test_new_rules_run_after_the_fixed_ones_and_before_the_defaults() -> None:
    text = "version: 1\nrules:\n  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
    got = rules.compose(SCHEMA, text)
    assert _ids(got) == [*FIXED, "mine", *DEFAULTS]
    assert _decide(got, BASE | {"category": "invoice"}) == "invoice"  # defaults still run


def test_a_same_id_rule_replaces_its_default_in_place() -> None:
    text = (
        "version: 1\nrules:\n  - {id: requires_reply, when: {field: requires_reply, eq: true},"
        " then: [flag, escalate]}\n  - {id: mine, when: {field: priority, eq: urgent},"
        " then: [flag]}\n"
    )
    got = rules.compose(SCHEMA, text)
    assert _ids(got) == [*FIXED, "mine", *DEFAULTS]  # spliced at its position (P4)
    reply = BASE | {"category": "invoice", "requires_reply": True}
    assert _decide(got, reply) == "invoice"  # it doesn't pre-empt the defaults before it
    d = got.evaluate(RuleInput(BASE | {"requires_reply": True}, FACTS))
    assert d.rule_id == "requires_reply" and [a.name for a in d.actions] == ["flag", "escalate"]


def test_default_rules_off_leaves_them_out() -> None:
    text = (
        "version: 1\ndefault_rules_off: [invoice, marketing]\nrules:\n"
        "  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
    )
    got = rules.compose(SCHEMA, text)
    assert _ids(got) == [
        *FIXED,
        "mine",
        *(i for i in DEFAULTS if i not in ("invoice", "marketing")),
    ]
    assert got.off == ("invoice", "marketing")
    assert _decide(got, BASE | {"category": "invoice"}) == "otherwise"


def test_a_file_ending_in_a_catch_all_replaces_the_defaults() -> None:
    text = (
        "version: 1\nrules:\n  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
        "  - {id: rest, then: [leave]}\n"
    )
    got = rules.compose(SCHEMA, text)
    assert _ids(got) == [*FIXED, "mine", "rest"]
    assert rules.catch_all(rules.compile_rules(text, SCHEMA))
    # the starter file as a user file is the replace case: the same set as the default
    starter = (ROOT / "src/ecf_server/data/starter_rules.yaml").read_text()
    assert _ids(rules.compose(SCHEMA, starter)) == _ids(rules.compose(SCHEMA))


@pytest.mark.parametrize(
    ("off", "why"),
    [
        ("[nope]", "nope isn't one of ecf's default rules"),
        ("[fraud_guard]", "one of ecf's fixed rules"),
        ("[otherwise]", "ecf's catch-all and can't be turned off"),
        ("[mine]", "isn't one of ecf's default rules"),
        ("[invoice, invoice]", "listed twice"),
        ("[marketing]", "also a rule in your file"),
    ],
)
def test_check_layers_refuses(off: str, why: str) -> None:
    text = (
        f"version: 1\ndefault_rules_off: {off}\nrules:\n"
        "  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
        "  - {id: marketing, when: {field: category, eq: marketing}, then: [flag]}\n"
    )
    with pytest.raises(InvalidInputError, match=why):
        rules.check_layers(text, SCHEMA)


def test_an_off_list_in_a_catch_all_file_is_refused() -> None:
    text = "version: 1\ndefault_rules_off: [invoice]\nrules:\n  - {id: rest, then: [leave]}\n"
    with pytest.raises(InvalidInputError, match="your last rule matches everything"):
        rules.check_layers(text, SCHEMA)


def test_an_unknown_off_id_at_load_is_skipped_with_a_warning() -> None:
    text = (
        "version: 1\ndefault_rules_off: [renamed_later, otherwise]\nrules:\n"
        "  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
    )
    got = rules.compose(SCHEMA, text)  # no refusal at load
    assert _ids(got) == [*FIXED, "mine", *DEFAULTS]
    assert rules.layer_warnings(got) == [
        "default_rules_off: renamed_later isn't one of ecf's default rules; skipped",
        "default_rules_off: otherwise isn't one of ecf's default rules; skipped",
    ]


def test_layer_lines_say_how_the_file_layers() -> None:
    text = (
        "version: 1\ndefault_rules_off: [shipping]\nrules:\n"
        "  - {id: invoice, when: {field: category, eq: invoice}, then: [{label: invoice},"
        " archive]}\n  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
    )
    lines = rules.layer_lines(text, SCHEMA)
    assert lines[0] == (
        "rules: your rules run after ecf's fixed rules and before ecf's default rules"
    )
    assert (
        "rules: your rule invoice replaces ecf's default invoice, at its position (2 of 11"
        " default rules); rename it to keep both"
    ) in lines
    assert "rules: ecf's default rule shipping is off" in lines
    assert lines[-1] == (
        "rules: your rules that can hide mail (when ecf's checks allow it): invoice"
    )
    catch = rules.layer_lines("version: 1\nrules:\n  - {id: rest, then: [leave]}\n", SCHEMA)
    assert catch[0].startswith("rules: your last rule matches everything, so ecf's default rules")


def test_the_cache_is_keyed_on_the_off_list() -> None:
    body = "rules:\n  - {id: mine, when: {field: priority, eq: urgent}, then: [flag]}\n"
    rules._CACHE.clear()  # pyright: ignore[reportPrivateUsage]  # a full cache clears between calls
    a = rules.compose(SCHEMA, f"version: 1\n{body}")
    b = rules.compose(SCHEMA, f"version: 1\ndefault_rules_off: [invoice]\n{body}")
    assert "invoice" in _ids(a) and "invoice" not in _ids(b)
    assert rules.compose(SCHEMA, f"version: 1\n{body}") is a  # cached


def test_the_fallback_rules_are_the_fixed_ones_and_otherwise() -> None:
    got = rules.fallback_rules(SCHEMA)
    assert _ids(got) == [*FIXED, "otherwise"]
    assert _decide(got, BASE | {"category": "marketing"}) == "otherwise"
    assert _decide(got, BASE | {"fraud_risk": "high"}) == "fraud_guard"


def test_strip_defaults_only_when_nothing_changes() -> None:
    raw = json.loads(
        json.dumps(
            load_yaml((ROOT / "src/ecf_server/data/starter_rules.yaml").read_text(), source="s")
        )
    )
    custom = {"id": "mine", "when": {"field": "priority", "eq": "urgent"}, "then": ["flag"]}
    # layered: my rule, then identical default copies (no catch-all): every copy goes
    layered = {"version": 1, "rules": [custom, *raw["rules"][:3]]}
    doc, gone = rules.strip_defaults(layered, SCHEMA)
    assert gone == tuple(r["id"] for r in raw["rules"][:3]) and doc == {
        "version": 1,
        "rules": [custom],
    }
    # the replace case with every default in order: all stripped, the default rules apply
    full = {"version": 1, "rules": [custom, *raw["rules"]]}
    doc, gone = rules.strip_defaults(full, SCHEMA)
    assert doc == {"version": 1, "rules": [custom]} and len(gone) == len(DEFAULTS)
    # a replace-case file that dropped a default: stripping would bring it back, so nothing goes
    dropped = {
        "version": 1,
        "rules": [custom, *(r for r in raw["rules"] if r["id"] != "marketing")],
    }
    assert rules.strip_defaults(dropped, SCHEMA) == (dropped, ())
    # a changed copy isn't a copy
    changed = {"version": 1, "rules": [custom, raw["rules"][0] | {"then": ["flag"]}]}
    assert rules.strip_defaults(changed, SCHEMA) == (changed, ())
    # only defaults left, all identical: nothing left (`rules: default`)
    assert rules.strip_defaults({"version": 1, "rules": raw["rules"]}, SCHEMA)[0] is None


# M0b, M0c (§7): a user file identical to the starter, or naming an unknown default field,
# decides as before.


def _cases() -> list[ruletest.Case]:
    cases, _missing = ruletest.load_cases(SYNTHETIC)
    assert len(cases) >= 300
    return cases


def test_m0b_a_starter_identical_user_file_decides_as_the_default() -> None:
    """A user file identical to `starter_rules.yaml` (the replace case), and one with every
    starter rule but `otherwise` (each a same-id replacement, spliced), decide every card as
    `rules: default` does."""
    starter_text = (ROOT / "src/ecf_server/data/starter_rules.yaml").read_text()
    raw = load_yaml(starter_text, source="s")
    no_catch = json.dumps(
        {"version": 1, "rules": [r for r in raw["rules"] if r["id"] != "otherwise"]}
    )
    default = rules.compose(SCHEMA)
    same = rules.compose(SCHEMA, starter_text, source="m0b same")
    spliced = rules.compose(SCHEMA, no_catch, source="m0b spliced")
    for case in _cases():
        for cls in (case.labels, BASE | case.labels):
            inp = RuleInput(cls, case.facts)
            want = default.evaluate(inp)
            assert same.evaluate(inp) == want, case.id
            assert spliced.evaluate(inp) == want, case.id


def test_m0c_an_unknown_default_field_decides_as_before_and_not_never_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `category` or `sender_type` deleted or set to a value it doesn't have, the starter
    rules decide each card as they did before S4 (a missing field compared false; no starter
    rule has `not` over a field), and a rule `not: {field: category, eq: x}` never matches."""
    starter = rules.compose(SCHEMA)
    probe = rules.compose(
        SCHEMA,
        "version: 1\nrules:\n  - {id: probe, when: {not: {field:"
        " category, eq: invoice}}, then: [archive]}\n",
        source="m0c",
    )
    variants: list[tuple[str, str, dict[str, Any]]] = []
    for case in _cases():
        full = BASE | case.labels
        for name in ("category", "sender_type"):
            gone = {k: v for k, v in full.items() if k != name}
            variants += [(case.id, name, gone), (case.id, name, full | {name: "no_such_value"})]
    now = [(cid, starter.evaluate(RuleInput(cls, FACTS)).rule_id) for cid, _n, cls in variants]
    for cid, name, cls in variants:
        if name == "category":  # the probe's field unknown: never a match
            assert probe.evaluate(RuleInput(cls, FACTS)).rule_id != "probe", cid

    # before S4: the field's comparisons proceeded on the raw value (absent: never equal)
    def known(*_a: Any) -> bool:
        return True

    monkeypatch.setattr(rules, "_in_spec", known)
    rules._CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    old_starter = rules.compose(SCHEMA)
    then = [(cid, old_starter.evaluate(RuleInput(cls, FACTS)).rule_id) for cid, _n, cls in variants]
    rules._CACHE.clear()  # pyright: ignore[reportPrivateUsage]
    assert now == then


# R5: RAW's "Soccer" example, end to end.


SOCCER_CONFIG = """version: 1
schema:
  fields:
    soccer_team:
      type: enum
      description: Which of the children's soccer teams the email is about.
      values:
        under_10: The under-10 team.
        under_12: The under-12 team.
        other: Neither team, or not about soccer.
  category_values:
    soccer: "About the children's soccer club: games, practice, kit, the coach."
rules:
  version: 1
  rules:
    - id: soccer_club
      when: {field: category, eq: soccer}
      then:
        - {label: soccer}
        - {flag: true, if: {field: soccer_team, eq: under_10}}
"""


@pytest.mark.usefixtures("extensions_on")
def test_the_soccer_example_end_to_end(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """RAW:51 (R5): a C install adds a `soccer` category value and a team field, and a rule that
    labels and flags; the dry run shows the budget, the privacy line and how the rules layer;
    once stored, the composed set decides soccer mail by the user rule and other mail as before,
    and the fixed rules still run first."""
    r = config.apply(conn, clock, FakeNotifier(), SOCCER_CONFIG, dry_run=True, nonce=None)
    assert r.changed and not r.applied and r.schema is not None
    assert "bytes of schema per classify read" in r.schema["budget"]
    assert config.PRIVACY_LINE in r.notes
    assert "rules: your rules run after ecf's fixed rules and before ecf's default rules" in r.notes
    doc = config.parse(conn, SOCCER_CONFIG)
    now = to_ts(clock.now())
    with write_tx(conn):
        for s in ("schema", "rules"):
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?,"
                " ?, 'test')",
                (config.KEY[s], config.ordered_json(doc[s]), now),
            )
    schema = config.current_schema(conn)
    assert "soccer" in schema.fields["category"].values and "soccer_team" in schema.fields
    compiled = config.current_rules(conn)
    assert _ids(compiled)[: len(FIXED) + 1] == [*FIXED, "soccer_club"]
    soccer = BASE | {"category": "soccer", "soccer_team": "under_10"}
    d = compiled.evaluate(RuleInput(soccer, FACTS))
    assert d.rule_id == "soccer_club" and [(a.name, a.target) for a in d.actions] == [
        ("label", "soccer"),
        ("flag", None),
    ]
    assert "soccer" in policy.labels(schema, compiled)
    assert _decide(compiled, soccer | {"fraud_risk": "high"}) == "fraud_guard"
    assert _decide(compiled, BASE | {"category": "invoice", "soccer_team": "other"}) == "invoice"
    # mail classified before the field (or by the local model): the team is unknown, no flag
    old = compiled.evaluate(RuleInput(BASE | {"category": "soccer"}, FACTS))
    assert old.rule_id == "soccer_club" and [a.name for a in old.actions] == ["label"]


# C13, the S4b premise: no user schema changes what the fixed rules read, and the kept sources
# and priority survive any accepted extension.


@pytest.mark.parametrize(
    "ext",
    [
        {"fields": {"priority": {"type": "boolean", "description": "Mine."}}},
        {"fields": {"sender_type": {"type": "boolean", "description": "Mine."}}},
        {"fields": {"category": {"type": "boolean", "description": "Mine."}}},
        {"fields": {"phishing": {"type": "boolean", "description": "Mine."}}},
        {
            "fields": {
                "fraud_risk": {
                    "type": "ordinal",
                    "description": "Mine.",
                    "levels": {"none": "No.", "high": "Yes."},
                }
            }
        },
        {"category_values": {"regulator": "A regulator."}},
        {"category_values": {"person": "A person."}},
        {"category_values": {"friend": "A friend."}},
        {"category_values": {"family": "Family."}},
        {"category_values": {"team": "Removed in v3."}},
        {"category_values": {"vendor_change_request": "Removed in v3."}},
        {
            "fields": {
                "x": {
                    "type": "enum",
                    "description": "X.",
                    "values": {"regulator": "R.", "other": "E."},
                }
            }
        },
        {
            "fields": {
                "x": {
                    "type": "enum",
                    "description": "X.",
                    "values": {"person": "P.", "other": "E."},
                }
            }
        },
        {
            "fields": {
                "x": {
                    "type": "enum",
                    "description": "X.",
                    "values": {"spam_or_phishing": "S.", "other": "E."},
                }
            }
        },
    ],
)
def test_no_user_schema_changes_what_the_fixed_rules_read(ext: dict[str, Any]) -> None:
    """S4 adds only: `priority`, `sender_type` and the values the fixed rules and `KEPT_SOURCES`
    read can't be removed, redefined or repeated (S4b must keep this when it allows replacing
    `sender_type`, plan C13)."""
    assert check_extension(SCHEMA, ext), ext
    with pytest.raises(InvalidInputError):
        extend_schema(SCHEMA, ext)


def test_the_kept_sources_and_priority_survive_any_accepted_extension() -> None:
    s = extend_schema(SCHEMA, USER)
    assert s.fields["priority"] == SCHEMA.fields["priority"]
    assert s.fields["sender_type"] == SCHEMA.fields["sender_type"]
    for sources in KEPT_SOURCES.values():
        for field, vals in sources:
            assert vals <= set(s.fields[field].values)
    assert _ids(rules.load_fixed_rules(s)) == FIXED


# Review R3: a rule with no `when` is allowed only last, and never with a default's id.


@pytest.mark.parametrize("rules_yaml", [
    "  - {id: x, then: [archive]}\n  - {id: b, when: {trigger: fraud}, then: [flag]}\n",
    "  - {id: invoice, then: [archive]}\n  - {id: b, when: {trigger: fraud}, then: [flag]}\n",
    "  - {id: b, when: {trigger: fraud}, then: [flag]}\n  - {id: invoice, then: [leave]}\n",
])  # fmt: skip
def test_r3_a_bare_rule_not_last_or_with_a_default_id_is_refused(rules_yaml: str) -> None:
    text = "version: 1\nrules:\n" + rules_yaml
    with pytest.raises(InvalidInputError, match="has no `when`"):
        rules.check_layers(text, SCHEMA)
    with pytest.raises(InvalidInputError, match="has no `when`"):
        rules.compose(SCHEMA, text)


def test_r3_a_bare_last_rule_of_another_id_or_otherwise_is_the_replace_case() -> None:
    for last in ("{id: rest, then: [leave]}", "{id: otherwise, actor: continue}"):
        first = "  - {id: b, when: {trigger: fraud}, then: [flag]}\n"
        text = f"version: 1\nrules:\n{first}  - {last}\n"
        got = rules.compose(SCHEMA, text)
        assert [r.id for r in got.rules][5:] == ["b", last.split(",")[0].removeprefix("{id: ")]
