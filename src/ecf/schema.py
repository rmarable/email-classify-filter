"""Schema compiler (SPEC §7.4): classification schema YAML -> CompiledSchema.

Enum and ordinal fields become `Literal` types, so the JSON schema is inline (no `$ref`), which
both Ollama's `format` and `record_classification` need. Ordinal fields keep their level order.
`prompt_block` is a stable text rendering used as an unchanging prompt prefix.
"""

from __future__ import annotations

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
        return FieldSpec(name, kind, desc, tuple(str(v) for v in cast(list[Any], levels)))
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


def load_schema_v1() -> CompiledSchema:
    text = resources.files("ecf.data").joinpath("schema_v1.yaml").read_text(encoding="utf-8")
    return compile_schema(text, source="schema_v1.yaml")
