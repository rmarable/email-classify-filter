import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecf.errors import InvalidInputError
from ecf.schema import FieldKind, check_safety_core, compile_schema, load_schema, load_schema_v1
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
