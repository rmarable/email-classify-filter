"""Schema compiler (SPEC §7.4): classification schema YAML -> CompiledSchema.

Enum and ordinal fields become `Literal` types, so the JSON schema is inline (no `$ref`), which
both Ollama's `format` and `record_classification` need. Ordinal fields keep their level order.
`prompt_block` is a stable text rendering used as an unchanging prompt prefix.

The shipped schema is v2 (`load_schema`; SPEC §7.1, OD-475): one schema for work and personal
mail. v1 stays loadable for results and labels made before it (`load_schema_v1`). The safety
core (`SAFETY_FIELDS`, `SAFETY_VALUES`) is what policy, the fraud checks and the starter rules
rely on; the compiler refuses a shipped schema without it.

An install may extend the shipped schema through `ecf config apply` (`schema` section; SPEC
§7.1, §9.7, OD-478): `extend_schema` appends the extension's fields after the shipped ones and
its values to `category`; the extension's prompt text follows the shipped text, so the prompt
prefix doesn't change. `check_extension` lists every violation of the caps and content rules.

Extensions are built but off in v2.0.0 (OD-481; planned for v2.1.0): while `EXTENSIONS_ENABLED`
is False the service refuses a `schema` section other than `default` and ignores a stored
extension (the effective schema is the shipped one). The machinery here doesn't read the switch.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, create_model

from ecf.errors import InvalidInputError, SchemaLimitError
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


# What a schema v1 value means in v2 (OD-475): only `sender_type: staff` was renamed; every
# other v1 value is a v2 value. Used for v1 rules files (`ecf_server.rules`), v1 eval results
# (`ecf eval rescore --synthetic`) and v1-era corpus label files.
V1_VALUE_ALIASES: dict[str, dict[str, str]] = {"sender_type": {"staff": "team"}}


def v1_values(values: dict[str, Any], schema: CompiledSchema) -> dict[str, Any]:
    """`values` with each schema v1 value `schema` lacks read as its v2 value."""
    out = dict(values)
    for name, renames in V1_VALUE_ALIASES.items():
        v = out.get(name)
        spec = schema.fields.get(name)
        if isinstance(v, str) and v in renames and spec is not None and v not in spec.values:
            out[name] = renames[v]
    return out


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
    extension: dict[str, Any] | None = None  # the install's extension (OD-478), as applied
    # set when a stored extension no longer compiles: the shipped schema keyed apart (fail closed)
    fallback_for: str | None = None

    @property
    def extension_fields(self) -> tuple[str, ...]:
        return tuple(cast(dict[str, Any], (self.extension or {}).get("fields") or {}))

    def json_schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()

    def validate(self, data: Any) -> BaseModel:
        return self.model.model_validate(data)

    @property
    def digest(self) -> str:
        """A short hash of what the models are asked: the version, then each field's name, kind,
        description, values and value descriptions in order (the gate key, SPEC §9.3)."""
        doc: list[Any] = [
            self.version,
            [
                [f.name, f.kind.value, f.description, list(f.values), list(f.value_descriptions)]
                for f in self.fields.values()
            ],
        ]
        if self.fallback_for is not None:
            doc.append(["fallback", self.fallback_for])
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
    return CompiledSchema(version, fields, _model(version, fields), _prompt_block(version, fields))


def _model(version: int, fields: dict[str, FieldSpec]) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    for f in fields.values():
        annotation: Any = bool if f.kind is FieldKind.BOOLEAN else Literal[f.values]
        definitions[f.name] = (annotation, ...)
    return create_model(
        f"ClassificationV{version}",
        __config__=ConfigDict(extra="forbid", strict=True),
        **definitions,
    )


def _prompt_block(version: int, fields: dict[str, FieldSpec]) -> str:
    lines = [f"Classification schema v{version}. Answer every field.", ""]
    lines += _field_lines(fields.values())
    return "\n".join(lines) + "\n"


def _field_lines(fields: Any) -> list[str]:
    lines: list[str] = []
    for f in cast(list[FieldSpec], list(fields)):
        if f.kind is FieldKind.ENUM:
            lines.append(f"- {f.name} (one of): {f.description}")
            lines += [f"    {v}: {d}" for v, d in zip(f.values, f.value_descriptions, strict=True)]
        elif f.kind is FieldKind.ORDINAL:
            lines.append(f"- {f.name} ({' < '.join(f.values)}): {f.description}")
        else:
            lines.append(f"- {f.name} (true/false): {f.description}")
    return lines


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


# ---------------------------------------------------------------------------- extensions (OD-478)

# The one switch (OD-481): off in v2.0.0, on from v2.1.0 once the extension check passes.
EXTENSIONS_ENABLED = False
EXTENSIONS_OFF = "schema extensions arrive in v2.1.0 (SPEC §7.1); remove the `schema` section"


def extensions_enabled() -> bool:
    """Whether an install's extension is applied and used (read at each call, so tests can turn
    it on)."""
    return EXTENSIONS_ENABLED


# Field names an extension can't take: eval results keep these beside the schema's fields
RESERVED_FIELDS = frozenset({"rule", "safety"})
MAX_EXT_FIELDS = 8
MAX_EXT_VALUES = 16  # values or levels per extension field
MAX_EXT_CATEGORY_VALUES = 4
MAX_EXT_DESCRIPTION = 180  # characters, per description
MAX_EXT_TEXT = 4000  # characters of prompt text the extension adds
NEAR = 0.8  # "near the limit" from 80% of any cap
EXT_HEADING = "Added by this install (answer these too):"
_EXT_KEYS = frozenset({"fields", "category_values"})


def _ext_text(fields: list[FieldSpec], category: list[tuple[str, str]]) -> str:
    """The prompt text an extension adds after the shipped block ("" for none)."""
    if not fields and not category:
        return ""
    lines = ["", EXT_HEADING]
    if category:
        lines.append("- category (also one of):")
        lines += [f"    {v}: {d}" for v, d in category]
    lines += _field_lines(fields)
    return "\n".join(lines) + "\n"


def _check_text(text: Any, where: str, out: _Parsed) -> bool:
    """A description: one printable line, not starting with - or :, within the cap."""
    if not isinstance(text, str) or not text.strip():
        out.problems.append(f"{where}: a description is required")
        return False
    t = text.strip()
    if not t.isprintable():
        out.problems.append(f"{where}: one line of printable text (no line breaks, control or"
                            " format characters)")  # fmt: skip
        return False
    if t[0] in "-:":
        out.problems.append(f"{where}: can't start with - or :")
        return False
    if len(t) > MAX_EXT_DESCRIPTION:
        out.limits.append(f"{where}: {len(t):,} characters, limit {MAX_EXT_DESCRIPTION}")
        return False
    return True


@dataclass
class _Parsed:
    fields: list[FieldSpec]
    category: list[tuple[str, str]]
    problems: list[str]  # content rules
    limits: list[str]  # caps


def _parse_ext(base: CompiledSchema, ext: Any, reserved: frozenset[str]) -> _Parsed:
    """One pass over an extension that records every problem, not just the first."""
    out = _Parsed([], [], [], [])
    if not isinstance(ext, dict):
        out.problems.append("schema: expected a mapping with fields and/or category_values")
        return out
    doc = cast(dict[str, Any], ext)
    for k in sorted(set(doc) - _EXT_KEYS, key=str):
        out.problems.append(f"schema: unknown key {str(k)[:40]!r}; keys are fields and"
                            " category_values")  # fmt: skip
    rf = _mapping(doc.get("fields"), "schema.fields: expected a mapping of field names", out)
    rc = _mapping(doc.get("category_values"), "schema.category_values: expected a mapping of"
                  " value: description", out)  # fmt: skip
    if not rf and not rc:
        out.problems.append("schema: add fields or category_values (or `schema: default` to"
                            " remove the extension)")  # fmt: skip
    if len(rf) > MAX_EXT_FIELDS:
        out.limits.append(f"schema: {len(rf)} fields, limit {MAX_EXT_FIELDS}")
    if len(rc) > MAX_EXT_CATEGORY_VALUES:
        out.limits.append(f"schema: {len(rc)} category values, limit {MAX_EXT_CATEGORY_VALUES}")
    added: list[tuple[str, str]] = []  # (where, value) for the clash check
    for name, raw in rf.items():
        _ext_field(base, name, raw, out, added)
    for v, d in rc.items():
        where = f"schema.category_values.{str(v)[:40]}"
        if not isinstance(v, str) or not re.fullmatch(VALUE_NAME, v):
            out.problems.append(f"{where}: a value is lowercase letters, digits and _")
        elif _check_text(d, where, out):
            added.append((where, v))
            out.category.append((v, str(d).strip()))
    _clashes(base, added, reserved, out)
    text = _ext_text(out.fields, out.category)
    if len(text) > MAX_EXT_TEXT:
        out.limits.append(f"schema: extension text {len(text):,} characters, limit"
                          f" {MAX_EXT_TEXT:,}; shorten descriptions or remove a field")  # fmt: skip
    return out


def _mapping(v: Any, problem: str, out: _Parsed) -> dict[Any, Any]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        out.problems.append(problem)
        return {}
    return cast(dict[Any, Any], v)


def _ext_field(base: CompiledSchema, name: Any, raw: Any, out: _Parsed,
               added: list[tuple[str, str]]) -> None:  # fmt: skip
    where = f"schema.fields.{str(name)[:40]}"
    if not isinstance(name, str) or not re.fullmatch(FIELD_NAME, name):
        out.problems.append(f"{where}: a field name is lowercase letters, digits and _ (at most"
                            " 40, starting with a letter)")  # fmt: skip
        return
    if name in base.fields:
        out.problems.append(f"{where}: already a field of the shipped schema; an extension adds"
                            " fields, it can't change one")  # fmt: skip
        return
    if name in RESERVED_FIELDS:
        out.problems.append(f"{where}: {name!r} is reserved (eval results use it)")
        return
    spec = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    ok = _check_text(spec.get("description"), f"{where}.description", out)
    kind = spec.get("type")
    options = spec.get("values") if kind == "enum" else spec.get("levels")
    count = 0
    if kind == "enum" and isinstance(options, dict):
        vals = cast(dict[Any, Any], options)
        count = len(vals)
        for v, d in vals.items():
            ok = _check_text(d, f"{where}.values.{str(v)[:40]}", out) and ok
            if isinstance(v, str):
                added.append((f"{where}.values.{v}", v))
    elif kind == "ordinal" and isinstance(options, list):
        count = len(cast(list[Any], options))
    if count > MAX_EXT_VALUES:
        unit = "values" if kind == "enum" else "levels"
        out.limits.append(f"{where}: {count} {unit}, limit {MAX_EXT_VALUES}")
    if _bad_names(kind, options, where, out):
        return
    try:
        f = _field(name, raw)
    except InvalidInputError as exc:
        out.problems.append(f"{where}: {exc.detail.removeprefix(f'field {name}: ')}")
        return
    if ok and count <= MAX_EXT_VALUES:
        out.fields.append(f)


def _bad_names(kind: Any, options: Any, where: str, out: _Parsed) -> bool:
    """Every value or level that isn't a lowercase name, and every repeated level, listed."""
    names: list[Any] = []
    if kind == "enum" and isinstance(options, dict):
        names = list(cast(dict[Any, Any], options))
    elif kind == "ordinal" and isinstance(options, list):
        names = cast(list[Any], options)
    bad = [n for n in names if not isinstance(n, str) or not re.fullmatch(VALUE_NAME, n)]
    for n in bad:
        out.problems.append(f"{where}: {str(n)[:40]!r} isn't a lowercase name (letters, digits"
                            " and _, starting with a letter)")  # fmt: skip
    seen: set[Any] = set()
    dupes = 0
    for n in names:
        if isinstance(n, str) and n in seen:
            out.problems.append(f"{where}: levels must be unique; {n!r} is repeated")
            dupes += 1
        if isinstance(n, str):
            seen.add(n)
    return bool(bad) or bool(dupes)


def _clashes(base: CompiledSchema, added: list[tuple[str, str]], reserved: frozenset[str],
             out: _Parsed) -> None:  # fmt: skip
    """An added value can't be any enum value of any field, a built-in label or a rule id: it
    may become a label (S8). Ordinal levels never become labels, so `none` or `high` may repeat
    one."""
    taken: dict[str, str] = {}
    for f in base.fields.values():
        if f.kind is FieldKind.ENUM:
            for v in f.values:
                taken.setdefault(v, f"a value of {f.name}")
    for v in reserved:
        taken.setdefault(v, "a built-in label or a rule id")
    for where, v in added:
        if v in taken:
            out.problems.append(f"{where}: {v!r} is already {taken[v]}; added values need new"
                                " names")  # fmt: skip
        taken[v] = f"a value at {where.removeprefix('schema.')}"


def check_extension(base: CompiledSchema, ext: Any,
                    reserved: frozenset[str] = frozenset()) -> list[str]:  # fmt: skip
    """Every violation of the caps and content rules, as short messages ([] when it is fine).
    `reserved` holds names an added value can't take: the server passes its built-in labels and
    the applied rule ids (`ecf` can't import them)."""
    p = _parse_ext(base, ext, reserved)
    return p.limits + p.problems


def extend_schema(base: CompiledSchema, ext: dict[str, Any] | None,
                  reserved: frozenset[str] = frozenset()) -> CompiledSchema:  # fmt: skip
    """The shipped schema with the install's extension: its fields after the shipped ones, its
    category values at the end of `category`. Over a cap raises `SchemaLimitError`, any other
    problem `InvalidInputError`; both list every violation. Only None means no extension: an
    empty or wrong-typed value is checked, and refused."""
    if ext is None:
        return base
    p = _parse_ext(base, ext, reserved)
    if p.limits or p.problems:
        violations = p.limits + p.problems
        cls = SchemaLimitError if p.limits else InvalidInputError
        raise cls("; ".join(violations), violations=violations)
    fields = dict(base.fields)
    if p.category:
        cat = fields["category"]
        fields["category"] = FieldSpec(
            cat.name, cat.kind, cat.description,
            cat.values + tuple(v for v, _ in p.category),
            cat.value_descriptions + tuple(d for _, d in p.category),
        )  # fmt: skip
    fields |= {f.name: f for f in p.fields}
    prompt = base.prompt_block + _ext_text(p.fields, p.category)
    return CompiledSchema(base.version, fields, _model(base.version, fields), prompt, dict(ext))


def shipped_fallback(ext_text: str) -> CompiledSchema:
    """The shipped schema, keyed apart from it, for a stored extension that no longer compiles
    (a later release added a name it uses): classification goes on without the extension, and
    no gate passed on the shipped schema or the old extension carries over (fail closed)."""
    tag = hashlib.sha256(ext_text.encode("utf-8")).hexdigest()[:16]
    return dataclasses.replace(load_schema(), fallback_for=tag)


def extension_text(schema: CompiledSchema) -> str:
    """The prompt lines the install's extension adds ("" without one)."""
    i = schema.prompt_block.find("\n" + EXT_HEADING)
    return schema.prompt_block[i:] if schema.extension and i >= 0 else ""


def extension_budget(base: CompiledSchema, ext: Any) -> dict[str, Any]:
    """Usage against each cap and the budget line `ecf config apply` and `ecf doctor` print."""
    p = _parse_ext(base, ext or {}, frozenset()) if ext else _Parsed([], [], [], [])
    doc = cast(dict[str, Any], ext) if isinstance(ext, dict) else {}
    rf: Any = doc.get("fields") or {}
    rc: Any = doc.get("category_values") or {}
    n_fields = len(cast(dict[Any, Any], rf)) if isinstance(rf, dict) else 0
    n_cats = len(cast(dict[Any, Any], rc)) if isinstance(rc, dict) else 0
    most = max((len(f.values) for f in p.fields), default=0)
    chars = len(_ext_text(p.fields, p.category))
    use = {"fields": (n_fields, MAX_EXT_FIELDS),
           "category_values": (n_cats, MAX_EXT_CATEGORY_VALUES),
           "values_per_field": (most, MAX_EXT_VALUES), "text": (chars, MAX_EXT_TEXT)}  # fmt: skip
    near = sorted(k for k, (n, cap) in use.items() if n >= NEAR * cap)
    line = (f"schema: {n_fields} of {MAX_EXT_FIELDS} fields, {n_cats} of"
            f" {MAX_EXT_CATEGORY_VALUES} category values, {chars:,} of {MAX_EXT_TEXT:,}"
            " characters")  # fmt: skip
    if near:
        line += " (near the limit)"
    return {k: {"used": n, "limit": cap} for k, (n, cap) in use.items()} | {
        "near": near, "line": line}  # fmt: skip
