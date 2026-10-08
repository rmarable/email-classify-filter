import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ecf.errors import InvalidInputError, SchemaLimitError
from ecf.schema import (
    MAX_EXT_TEXT,
    SAFETY_FIELDS,
    SAFETY_VALUES,
    V1_VALUE_ALIASES,
    FieldKind,
    check_extension,
    check_safety_core,
    compile_schema,
    extend_schema,
    extension_budget,
    extension_text,
    load_schema,
    load_schema_v1,
    shipped_fallback,
    v1_values,
)
from ecf.yamlio import load_yaml

ROOT = Path(__file__).resolve().parents[1]
GOOD = {
    "category": "invoice",
    "priority": "high",
    "requires_action": True,
    "requires_reply": False,
    "payment_related": True,
    "deadline_mentioned": False,
    "sender_type": "vendor",
    "fraud_risk": "none",
}


def test_shipped_schemas_match_spec() -> None:
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    section = spec[spec.index("### 7.1 Schema v2") : spec.index("### 7.2")]
    blocks = re.findall(r"```yaml\n(.*?)```", section, re.S)
    assert len(blocks) == 2  # v2, then v1
    for version, block in zip((2, 1), blocks, strict=True):
        shipped = (ROOT / f"src/ecf/data/schema_v{version}.yaml").read_text(encoding="utf-8")
        assert load_yaml(shipped) == load_yaml(block)


def test_v2_keeps_the_safety_core_and_is_what_ships() -> None:
    v2 = load_schema()
    assert v2.version == 2 and v2 is load_schema()  # cached
    check_safety_core(v2)
    assert "team" in v2.fields["sender_type"].values
    assert "staff" not in v2.fields["sender_type"].values
    assert len(v2.fields["category"].values) == 22
    assert v2.fields["fraud_risk"].values == ("none", "low", "medium", "high")
    with pytest.raises(InvalidInputError, match="must keep team"):
        check_safety_core(load_schema_v1())  # v1 says staff


def test_digest_tracks_what_the_model_is_asked() -> None:
    a, b = load_schema(), load_schema_v1()
    assert a.digest.startswith("schema-") and a.digest != b.digest
    text = (ROOT / "src/ecf/data/schema_v2.yaml").read_text(encoding="utf-8")
    assert compile_schema(text).digest == a.digest
    assert compile_schema(text.replace("A relative.", "A relative or in-law.")).digest != a.digest


def test_v2_json_schema_snapshot() -> None:
    text = json.dumps(load_schema().json_schema(), indent=2, sort_keys=True) + "\n"
    assert "$ref" not in text and "$defs" not in text
    assert text == (ROOT / "tests/snapshots/schema_v2.json").read_text(encoding="utf-8")


def test_json_schema_snapshot_and_no_refs() -> None:
    js = load_schema_v1().json_schema()
    text = json.dumps(js, indent=2, sort_keys=True) + "\n"
    assert "$ref" not in text and "$defs" not in text
    assert text == (ROOT / "tests/snapshots/schema_v1.json").read_text(encoding="utf-8")


def test_validation_is_strict() -> None:
    s = load_schema_v1()
    s.validate(GOOD)
    for bad in (
        {**GOOD, "category": "tax"},
        {**GOOD, "extra": 1},
        {**GOOD, "requires_reply": "yes"},
        {k: v for k, v in GOOD.items() if k != "priority"},
    ):
        with pytest.raises(ValidationError):
            s.validate(bad)


def test_ordinals_keep_order() -> None:
    f = load_schema_v1().fields["priority"]
    assert f.kind is FieldKind.ORDINAL
    assert f.rank("low") < f.rank("medium") < f.rank("high") < f.rank("urgent")
    with pytest.raises(InvalidInputError):
        load_schema_v1().fields["category"].rank("invoice")


def test_prompt_block_is_stable_and_complete() -> None:
    a, b = load_schema_v1().prompt_block, load_schema_v1().prompt_block
    assert a == b
    for name in GOOD:
        assert f"- {name} " in a
    assert "low < medium < high < urgent" in a


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\nfields: {}\n",
        "version: 0\nfields: {a: {type: boolean, description: x}}\n",
        "version: 1\nfields: {a: {type: text, description: x}}\n",
        "version: 1\nfields: {a: {type: boolean}}\n",
        "version: 1\nfields: {a: {type: ordinal, levels: [one], description: x}}\n",
        "version: 1\nfields: {Bad: {type: boolean, description: x}}\n",
        "version: 1\nfields: {a: {type: ordinal, levels: [lo, lo], description: x}}\n",
        "version: 1\nfields: {a: {type: ordinal, levels: [lo, Hi Gh], description: x}}\n",
    ],
)
def test_bad_schemas_rejected(text: str) -> None:
    with pytest.raises(InvalidInputError):
        compile_schema(text)


def test_yaml_rules() -> None:
    assert load_yaml("a: no\nb: 010\n") == {"a": "no", "b": 10}
    with pytest.raises(InvalidInputError, match="invalid YAML"):
        load_yaml("a: 1\na: 2\n")


@pytest.mark.parametrize(
    "text",
    [
        "version: true\nfields: {a: {type: boolean, description: x}}\n",
        "version: 1\nfields: {a: {type: enum, description: x, values: {1: one}}}\n",
        "version: 1\nfields: {a: {type: enum, description: x, values: {Bad Value: one}}}\n",
    ],
)
def test_more_bad_schemas_rejected(text: str) -> None:
    with pytest.raises(InvalidInputError):
        compile_schema(text)


def test_rank_of_unknown_level_is_a_clear_error() -> None:
    with pytest.raises(InvalidInputError, match="not a level"):
        load_schema_v1().fields["priority"].rank("critical")


# ---- extensions (OD-478) -------------------------------------------------------------------------

EXT: dict[str, Any] = {
    "fields": {
        "contract_stage": {
            "type": "enum",
            "description": "Where a contract discussed in the email stands.",
            "values": {"none": "No contract discussed.", "draft": "A draft is being exchanged.",
                       "signature": "Waiting for signature."},
        },
        "legal_risk": {"type": "ordinal", "levels": ["none", "low", "high"],
                       "description": "How much legal exposure the email suggests."},
        "mentions_nda": {"type": "boolean", "description": "Mentions a non-disclosure agreement."},
    },
    "category_values": {"legal_notice": "Letter from a lawyer or court about us."},
}  # fmt: skip


def _enum(n: int, prefix: str = "v") -> dict[str, Any]:
    return {"type": "enum", "description": "An added field.",
            "values": {f"{prefix}{i}": f"Value {i}." for i in range(n)}}  # fmt: skip


def test_an_extension_appends_fields_and_category_values() -> None:
    base = load_schema()
    s = extend_schema(base, EXT)
    assert list(s.fields)[: len(base.fields)] == list(base.fields)  # shipped fields first
    assert list(s.fields)[len(base.fields) :] == ["contract_stage", "legal_risk", "mentions_nda"]
    assert s.fields["category"].values == (*base.fields["category"].values, "legal_notice")
    assert s.extension_fields == ("contract_stage", "legal_risk", "mentions_nda")
    assert s.fields["legal_risk"].rank("high") > s.fields["legal_risk"].rank("low")
    assert s.prompt_block.startswith(base.prompt_block)  # the prompt prefix doesn't change
    added = extension_text(s)
    assert "legal_notice: Letter from a lawyer" in added and "- mentions_nda (true/false)" in added
    assert extension_text(base) == ""
    good = {**GOOD, "category": "legal_notice", "contract_stage": "draft", "legal_risk": "low",
            "mentions_nda": False}  # fmt: skip
    s.validate(good)
    with pytest.raises(ValidationError):
        s.validate(GOOD)  # the added fields are required
    js = json.dumps(s.json_schema())
    assert "$ref" not in js and "legal_notice" in js
    check_safety_core(s)
    assert extend_schema(base, None) is base  # only None is "no extension"


@pytest.mark.parametrize("empty", [{}, [], "", 0, False])
def test_an_empty_or_wrong_typed_extension_is_checked_and_refused(empty: Any) -> None:
    with pytest.raises(InvalidInputError):
        extend_schema(load_schema(), empty)
    assert check_extension(load_schema(), empty)


def test_rule_and_safety_are_reserved_field_names() -> None:
    for name in ("rule", "safety"):
        ext = {"fields": {name: {"type": "boolean", "description": "Reserved."}}}
        assert any("reserved" in p for p in check_extension(load_schema(), ext))


def test_every_bad_value_name_and_repeated_level_is_listed() -> None:
    ext = {"fields": {
        "stage": {"type": "enum", "description": "A stage.",
                  "values": {"Bad": "One.", "also-bad": "Two.", "ok": "Three."}},
        "size": {"type": "ordinal", "description": "A size.",
                 "levels": ["small", "Big", "small", "9x"]},
    }}  # fmt: skip
    problems = check_extension(load_schema(), ext)
    for bad in ("'Bad'", "'also-bad'", "'Big'", "'9x'", "'small' is repeated"):
        assert any(bad in p for p in problems), bad


def test_the_extension_keeps_the_operators_order_and_a_stable_digest() -> None:
    a = {"fields": {"zeta": {"type": "boolean", "description": "Zeta."},
                    "alpha": {"type": "boolean", "description": "Alpha."}}}  # fmt: skip
    b = {"fields": dict(reversed(list(a["fields"].items())))}
    sa, sb = extend_schema(load_schema(), a), extend_schema(load_schema(), b)
    assert sa.extension_fields == ("zeta", "alpha")
    assert extension_text(sa).index("zeta") < extension_text(sa).index("alpha")
    assert sa.digest == extend_schema(load_schema(), a).digest != sb.digest


def test_a_fallback_schema_is_the_shipped_one_keyed_apart() -> None:
    fb = shipped_fallback('{"fields":{}}')
    assert fb.fields == load_schema().fields and fb.digest != load_schema().digest
    assert fb.digest == shipped_fallback('{"fields":{}}').digest


def test_the_digest_changes_with_the_extension() -> None:
    base = load_schema()
    a = extend_schema(base, EXT)
    assert a.digest != base.digest
    assert extend_schema(base, EXT).digest == a.digest  # stable
    other = json.loads(json.dumps(EXT))
    other["fields"]["contract_stage"]["values"]["draft"] = "A draft is being negotiated."
    assert extend_schema(base, other).digest != a.digest


def test_every_cap_violation_is_listed() -> None:
    base = load_schema()
    fields = {f"f{i}": _enum(2, f"f{i}v") for i in range(9)}
    fields["f0"] = _enum(18, "big")
    cats = {f"c{i}": "An added category." for i in range(5)}
    ext = {"fields": fields, "category_values": cats}
    got = check_extension(base, ext)
    assert "schema: 9 fields, limit 8" in got
    assert "schema: 5 category values, limit 4" in got
    assert "schema.fields.f0: 18 values, limit 16" in got
    with pytest.raises(SchemaLimitError) as ei:
        extend_schema(base, ext)
    assert ei.value.extra["violations"] == got and "9 fields, limit 8" in ei.value.detail


def test_long_text_and_descriptions_are_over_the_cap() -> None:
    base = load_schema()
    long = "x" * 181
    got = check_extension(base, {"fields": {"a": {"type": "boolean", "description": long}}})
    assert got == ["schema.fields.a.description: 181 characters, limit 180"]
    many = {f"f{i}": {"type": "enum", "description": "d" * 170,
                      "values": {f"f{i}v{j}": "e" * 170 for j in range(3)}}
            for i in range(7)}  # fmt: skip
    got = check_extension(base, {"fields": many})
    assert len(got) == 1 and got[0].startswith("schema: extension text ")
    assert got[0].endswith(f"limit {MAX_EXT_TEXT:,}; shorten descriptions or remove a field")


@pytest.mark.parametrize(
    ("ext", "why"),
    [
        ({"fields": {"priority": {"type": "boolean", "description": "x"}}}, "can't change one"),
        ({"fields": {"fraud_risk": {"type": "boolean", "description": "x"}}}, "can't change one"),
        ({"fields": {"Bad": {"type": "boolean", "description": "x"}}}, "field name"),
        ({"fields": {"a": {"type": "text", "description": "x"}}}, "type must be"),
        ({"fields": {"a": {"type": "boolean", "description": "two\nlines"}}}, "one line"),
        ({"fields": {"a": {"type": "boolean", "description": "hidden\u200bchar"}}}, "one line"),
        ({"fields": {"a": {"type": "boolean", "description": "- a list"}}}, "start with"),
        ({"fields": {"a": {"type": "boolean", "description": ": key"}}}, "start with"),
        ({"fields": {"a": {"type": "boolean"}}}, "description is required"),
        (
            {"fields": {"a": {"type": "ordinal", "levels": ["lo", "lo"], "description": "x"}}},
            "unique",
        ),
        ({"category_values": {"invoice": "Again."}}, "already a value of category"),
        ({"category_values": {"suspicious": "A label."}}, "built-in label or a rule id"),
        ({"category_values": {"Bad Name": "x"}}, "lowercase"),
        ({"fields": {"a": _enum(2, "same"), "b": _enum(2, "same")}}, "already a value at"),
        (
            {"fields": {"a": {"type": "enum", "description": "x", "values": {"team": "y"}}}},
            "already a value of sender_type",
        ),
        ({"other": 1}, "unknown key"),
        ({"fields": {}}, "add fields or category_values"),
        ("text", "expected a mapping"),
    ],
)
def test_content_rules(ext: Any, why: str) -> None:
    got = check_extension(load_schema(), ext, frozenset({"suspicious"}))
    assert any(why in g for g in got), got
    with pytest.raises(InvalidInputError, match=why.replace("(", ".")):
        extend_schema(load_schema(), ext, frozenset({"suspicious"}))


def test_problems_are_all_listed_not_just_the_first() -> None:
    ext = {"fields": {"priority": {}, "a": {"type": "boolean", "description": "- x"}},
           "category_values": {"invoice": "Again."}}  # fmt: skip
    assert len(check_extension(load_schema(), ext)) == 3


def test_the_safety_core_cant_change() -> None:
    base = load_schema()
    for name in [*SAFETY_FIELDS, *SAFETY_VALUES]:
        got = check_extension(base, {"fields": {name: {"type": "boolean", "description": "x"}}})
        assert got and "can't change one" in got[0]
    s = extend_schema(base, EXT)
    for name in SAFETY_FIELDS:
        assert s.fields[name] == base.fields[name]
    assert s.fields["sender_type"] == base.fields["sender_type"]


def test_ordinal_levels_may_repeat_other_fields_levels() -> None:
    """Levels never become labels, so `none` or `high` may repeat fraud_risk's (OD-478)."""
    ext = {"fields": {"legal_risk": EXT["fields"]["legal_risk"]}}
    assert check_extension(load_schema(), ext) == []


def test_the_budget_line_and_near_the_limit() -> None:
    base = load_schema()
    b = extension_budget(base, EXT)
    assert b["line"].startswith("schema: 3 of 8 fields, 1 of 4 category values, ")
    assert b["line"].endswith(" of 4,000 characters") and b["near"] == []
    assert b["text"]["used"] == len(extension_text(extend_schema(base, EXT)))
    near = extension_budget(base, {"fields": {f"f{i}": _enum(2, f"f{i}v") for i in range(7)}})
    assert near["near"] == ["fields"] and near["line"].endswith(" (near the limit)")
    cats = extension_budget(base, {"category_values": {f"c{i}": "x" for i in range(4)}})
    assert cats["near"] == ["category_values"]
    empty = extension_budget(base, None)
    assert empty["line"] == "schema: 0 of 8 fields, 0 of 4 category values, 0 of 4,000 characters"


def test_v1_value_aliases_map_each_renamed_v1_value_to_a_v2_value() -> None:
    v1, v2 = load_schema_v1(), load_schema()
    for name, renames in V1_VALUE_ALIASES.items():
        for old, new in renames.items():
            assert old in v1.fields[name].values and old not in v2.fields[name].values
            assert new in v2.fields[name].values
    assert v1_values({"sender_type": "staff", "priority": "high"}, v2) == {
        "sender_type": "team", "priority": "high"}  # fmt: skip
    assert v1_values({"sender_type": "staff"}, v1) == {"sender_type": "staff"}  # still a value
