import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecf.errors import InvalidInputError
from ecf.schema import FieldKind, compile_schema, load_schema_v1
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


def test_shipped_schema_matches_spec() -> None:
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    section = spec[spec.index("### 7.1 Schema v1") : spec.index("### 7.2")]
    block = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert block is not None
    shipped = (ROOT / "src/ecf/data/schema_v1.yaml").read_text(encoding="utf-8")
    assert load_yaml(shipped) == load_yaml(block.group(1))


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
    ],
)
def test_bad_schemas_rejected(text: str) -> None:
    with pytest.raises(InvalidInputError):
        compile_schema(text)


def test_yaml_rules() -> None:
    assert load_yaml("a: no\nb: 010\n") == {"a": "no", "b": 10}
    with pytest.raises(InvalidInputError, match="invalid YAML"):
        load_yaml("a: 1\na: 2\n")
