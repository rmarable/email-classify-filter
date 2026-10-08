"""Schema compiler (SPEC §7.4): classification schema YAML -> CompiledSchema.

Enum and ordinal fields become `Literal` types, so the JSON schema is inline (no `$ref`), which
both Ollama's `format` and `record_classification` need. Ordinal fields keep their level order.
`prompt_block` is a stable text rendering used as an unchanging prompt prefix.

The shipped schema is v2 (`load_schema`; SPEC §7.1, OD-475): one schema for work and personal
mail. v1 stays loadable for results and labels made before it (`load_schema_v1`). The safety
core (`SAFETY_FIELDS`, `SAFETY_VALUES`) is what policy, the fraud checks and the starter rules
rely on; the compiler refuses a shipped schema without it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, create_model

from ecf.errors import InvalidInputError
from ecf.yamlio import load_yaml

FIELD_NAME = r"^[a-z][a-z0-9_]{0,39}$"
VALUE_NAME = r"^[a-z][a-z0-9_]{0,39}$"
CURRENT_VERSION = 2
# What policy, the fraud and regulator checks and the starter rules read (SPEC §7.1, OD-475).
SAFETY_FIELDS = {
    "fraud_risk": ("ordinal", ("none", "low", "medium", "high")),
    "payment_related": ("boolean", ()),
    "priority": ("ordinal", ("low", "medium", "high", "urgent")),
    "requires_action": ("boolean", ()),
    "requires_reply": ("boolean", ()),
    "deadline_mentioned": ("boolean", ()),
}
SAFETY_VALUES = {
    "category": ("regulatory", "vendor_change_request", "spam_or_phishing", "notification",
                 "action_alert"),
    "sender_type": ("team",),
}  # fmt: skip


class FieldKind(StrEnum):
    ENUM = "enum"
    ORDINAL = "ordinal"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    description: str
    values: tuple[str, ...] = ()  # enum values, or ordinal levels low -> high
    value_descriptions: tuple[str, ...] = ()

    def rank(self, value: str) -> int:
        if self.kind is not FieldKind.ORDINAL:
            raise InvalidInputError(f"{self.name} is not ordinal")
        if value not in self.values:
            raise InvalidInputError(f"{value!r} is not a level of {self.name}")
        return self.values.index(value)


@dataclass(frozen=True)
class CompiledSchema:
    version: int
    fields: dict[str, FieldSpec]
    model: type[BaseModel]
    prompt_block: str

    def json_schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()

    def validate(self, data: Any) -> BaseModel:
        return self.model.model_validate(data)

    @property
    def digest(self) -> str:
        """A short hash of what the models are asked: the version, then each field's name, kind,
        description, values and value descriptions in order (the gate key, SPEC §9.3)."""
        doc = [
            self.version,
            [
                [f.name, f.kind.value, f.description, list(f.values), list(f.value_descriptions)]
                for f in self.fields.values()
            ],
        ]
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return "schema-" + hashlib.sha256(raw).hexdigest()[:16]


def _field(name: str, raw: Any) -> FieldSpec:
    if not isinstance(raw, dict):
        raise InvalidInputError(f"field {name}: must be a mapping")
    spec = cast(dict[str, Any], raw)
    kind_raw = spec.get("type")
    if kind_raw not in {k.value for k in FieldKind}:
        raise InvalidInputError(f"field {name}: type must be enum, ordinal or boolean")
    kind = FieldKind(kind_raw)
    desc = str(spec.get("description", "")).strip()
    if not desc:
        raise InvalidInputError(f"field {name}: description is required")
    if kind is FieldKind.ENUM:
        values = spec.get("values")
        if not isinstance(values, dict) or not values:
            raise InvalidInputError(f"field {name}: enum needs a values mapping")
        vals = cast(dict[str, Any], values)
        for key in cast(dict[Any, Any], values):  # YAML may give non-string keys
            if not isinstance(key, str) or not re.fullmatch(VALUE_NAME, key):
                raise InvalidInputError(f"field {name}: value {key!r} must be a lowercase name")
        return FieldSpec(
            name, kind, desc, tuple(vals), tuple(str(v).strip() for v in vals.values())
        )
    if kind is FieldKind.ORDINAL:
        levels = spec.get("levels")
        if not isinstance(levels, list) or len(cast(list[Any], levels)) < 2:
            raise InvalidInputError(f"field {name}: ordinal needs at least two levels")
        lv = cast(list[Any], levels)
        if any(not isinstance(v, str) or not re.fullmatch(VALUE_NAME, v) for v in lv):
            raise InvalidInputError(f"field {name}: each level must be a lowercase name")
        if len(set(lv)) != len(lv):
            raise InvalidInputError(f"field {name}: levels must be unique")
        return FieldSpec(name, kind, desc, tuple(cast(list[str], lv)))
    return FieldSpec(name, kind, desc)


def compile_schema(text: str, *, source: str = "schema") -> CompiledSchema:
    doc = load_yaml(text, source=source)
    if not isinstance(doc, dict):
        raise InvalidInputError(f"{source}: must be a mapping")
    d = cast(dict[str, Any], doc)
    version = d.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidInputError(f"{source}: version must be a positive integer")
    raw_fields = d.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise InvalidInputError(f"{source}: fields must be a non-empty mapping")
    fields: dict[str, FieldSpec] = {}
    for name, raw in cast(dict[str, Any], raw_fields).items():
        if not re.fullmatch(FIELD_NAME, name):
            raise InvalidInputError(f"field name {name!r} is not allowed")
        fields[name] = _field(name, raw)
    definitions: dict[str, Any] = {}
    for f in fields.values():
        annotation: Any = bool if f.kind is FieldKind.BOOLEAN else Literal[f.values]
        definitions[f.name] = (annotation, ...)
    model = create_model(
        f"ClassificationV{version}",
        __config__=ConfigDict(extra="forbid", strict=True),
        **definitions,
    )
    return CompiledSchema(version, fields, model, _prompt_block(version, fields))


def _prompt_block(version: int, fields: dict[str, FieldSpec]) -> str:
    lines = [f"Classification schema v{version}. Answer every field.", ""]
    for f in fields.values():
        if f.kind is FieldKind.ENUM:
            lines.append(f"- {f.name} (one of): {f.description}")
            lines += [f"    {v}: {d}" for v, d in zip(f.values, f.value_descriptions, strict=True)]
        elif f.kind is FieldKind.ORDINAL:
            lines.append(f"- {f.name} ({' < '.join(f.values)}): {f.description}")
        else:
            lines.append(f"- {f.name} (true/false): {f.description}")
    return "\n".join(lines) + "\n"


def check_safety_core(schema: CompiledSchema) -> None:
    """Refuse a schema without the fields and values ecf's safety logic reads (OD-475)."""
    for name, (kind, levels) in SAFETY_FIELDS.items():
        f = schema.fields.get(name)
        if f is None or f.kind.value != kind or (levels and f.values != levels):
            raise InvalidInputError(f"schema: {name} must be the {kind} field ecf ships")
    for name, values in SAFETY_VALUES.items():
        f = schema.fields.get(name)
        missing = [v for v in values if f is None or v not in f.values]
        if missing:
            raise InvalidInputError(f"schema: {name} must keep {', '.join(missing)}")


def _load(version: int) -> CompiledSchema:
    name = f"schema_v{version}.yaml"
    text = resources.files("ecf.data").joinpath(name).read_text(encoding="utf-8")
    return compile_schema(text, source=name)


@functools.cache
def load_schema() -> CompiledSchema:
    """The shipped schema (v2)."""
    schema = _load(CURRENT_VERSION)
    check_safety_core(schema)
    return schema


@functools.cache
def load_schema_v1() -> CompiledSchema:
    """Schema v1, for results and labels made before v2 (OD-475)."""
    return _load(1)
