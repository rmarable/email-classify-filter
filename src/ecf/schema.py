"""Schema compiler (SPEC §7.4): classification schema YAML -> CompiledSchema.

Enum and ordinal fields become `Literal` types, so the JSON schema is inline (no `$ref`), which
both Ollama's `format` and `record_classification` need. Ordinal fields keep their level order.
`prompt_block` is a stable text rendering used as an unchanging prompt prefix.

The shipped schema is v3 (`load_schema`; SPEC §7.1, OD-498, OD-500): v2's fields with five yes/no
fixed safety items (`FIXED_ITEMS`) after them and `priority` level texts (v3.0.0 step 1c, stage
S2); from stage S3b the four v2 values the items replace are gone (`REMOVED_VALUES`: `category`
`vendor_change_request`, `regulatory`, `spam_or_phishing`; `sender_type` `team`) and `category`,
`account_security`, `marketing` and `unknown` are reworded. The fixed rules and policy read the
items, each through `fixed_item` (the item, or a kept v2 value that says the same: sender_type
`regulator`, `person`, `friend`, `family`). v2 stays loadable (`load_schema_v2`): the local
model (presets A and B) is still asked v2 (`local_schema`) and its answer brought to v3 by
`from_v2` (the fixed items derived from it by a fixed table, `with_fixed`, P6; then a removed
value read as `other` or `unknown`); results, labels and stored classifications made before v3
are read through the same table. v1 stays loadable for results and labels made before v2
(`load_schema_v1`). The safety core (`SAFETY_FIELDS`) is what policy, the fraud checks and the
fixed rules rely on; the compiler refuses a shipped schema without it, or with a removed value.

An install on preset C may add its own fields (the user layer, v3.0.0 S4, OD-502; SPEC §7.1,
§9.7) through `ecf config apply` (`schema` section): `extend_schema` appends them after the
shipped ones and their values to `category`; their prompt text follows the shipped text, so the
prompt prefix doesn't change. `check_extension` lists every violation of the caps and content
rules: a description on every field, value and level (an ordinal's levels are a mapping of level
to text), no fixed field's name in a description (`FIXED_FIELDS`), new names for added values
except the catch-alls (`CATCH_ALLS`, which a user field may reuse); `extension_warnings` lists
what is allowed but likely wrong (an enum with no catch-all, a description naming a default or
another user field). The fixed fields (`FIXED_FIELDS`: the safety set, `priority` and the workflow
fields) and the default fields (`category`, `sender_type`) can't be changed; S4 only adds. The
local model (presets A and B) is never asked user fields (`local_schema` is plain v2; the
service refuses user fields while any A or B address exists, D8), and its answer is checked
against `without_extension`.

The layer is behind one switch, `EXTENSIONS_ENABLED` (OD-481, kept by OD-502 until the S4
measurement M2 passes): while it is False the service refuses a `schema` section other than
`default` and ignores a stored extension (the effective schema is the shipped one). The
machinery here doesn't read the switch.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, create_model

from ecf.errors import InvalidInputError, SchemaLimitError
from ecf.yamlio import load_yaml

FIELD_NAME = r"^[a-z][a-z0-9_]{0,39}$"
VALUE_NAME = r"^[a-z][a-z0-9_]{0,39}$"
CURRENT_VERSION = 3
LOCAL_VERSION = 2  # the schema the local model is asked (`local_schema`; P6)
# The fixed safety items schema v3 adds (OD-498; schema doc §1): yes/no, after v2's fields
FIXED_ITEMS = ("bank_detail_change", "regulator_claim", "phishing", "claims_internal",
               "private_individual")  # fmt: skip
# What policy, the fraud and regulator checks and the fixed rules read (SPEC §7.1, OD-475), and
# the fixed items (OD-498, OD-500).
SAFETY_FIELDS = {
    "fraud_risk": ("ordinal", ("none", "low", "medium", "high")),
    "payment_related": ("boolean", ()),
    "priority": ("ordinal", ("low", "medium", "high", "urgent")),
    "requires_action": ("boolean", ()),
    "requires_reply": ("boolean", ()),
    "deadline_mentioned": ("boolean", ()),
} | {name: ("boolean", ()) for name in FIXED_ITEMS}
# ecf's fixed fields (schema doc §1; S4, OD-502): the safety set above, `priority` among them
# (fixed for good, D12). A rule reads an absent one as false (`ecf_server.rules`); any other field
# absent, or holding a value it doesn't have, is unknown (P14). A user description can't name one.
FIXED_FIELDS = tuple(SAFETY_FIELDS)
# The default fields: shipped and replaceable later (S4b), only added to in S4
DEFAULT_FIELDS = ("category", "sender_type")
# The catch-all values a user enum field should have one of, and may reuse although another field
# has them (D15, P12)
CATCH_ALLS = frozenset({"other", "unknown", "none"})


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


# Schema doc §9 (P13): what a v2 answer says about each fixed item. An item is true when any of
# its sources holds one of its values. `staff` is v1's name for `team` (OD-475).
FIXED_FROM_V2: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
    "bank_detail_change": (("category", frozenset({"vendor_change_request"})),),
    "regulator_claim": (("category", frozenset({"regulatory"})),
                        ("sender_type", frozenset({"regulator"}))),
    "phishing": (("category", frozenset({"spam_or_phishing"})),),
    "claims_internal": (("sender_type", frozenset({"team", "staff"})),),
    "private_individual": (("sender_type", frozenset({"person", "friend", "family"})),),
}  # fmt: skip
# Eval labels never derived (schema doc §10, P8): plain spam isn't phishing, so the operator
# splits `spam_or_phishing` by hand
NOT_DERIVED_LABELS = frozenset({"phishing"})
# A label value that says nothing (schema doc §10): the item stays unlabelled
UNLABELLED = frozenset({"unknown"})


def derive_fixed(values: Mapping[str, Any]) -> dict[str, bool]:
    """Each fixed item from a v2 classification by the §9 table; an absent source is false. The
    one table preset A (`with_fixed`), migration 0036 (the same rows, in SQL) and the eval
    labels (`derive_labels`, more strictly) use."""
    return {item: any(values.get(field) in vals for field, vals in sources)
            for item, sources in FIXED_FROM_V2.items()}  # fmt: skip


# The v2 values schema v3 removed (v3.0.0 S3b, OD-500), and what a rules file uses instead. A
# rules file can't test them; the shipped schema can't hold them (`check_safety_core`) and an
# extension can't add them back (`_clashes`). `staff` is v1's name for `team`.
REMOVED_VALUES: dict[tuple[str, str], str] = {
    ("category", "vendor_change_request"): "bank_detail_change: true",
    ("category", "regulatory"): "regulator_claim: true",
    ("category", "spam_or_phishing"): "phishing: true",
    ("sender_type", "team"): "claims_internal: true",
}
# Each field's removed values (v1's `staff` with `team`), and the value `from_v2` reads them as:
# the least informative one (`evalrun.null_classification`'s)
REMOVED_BY_FIELD: dict[str, frozenset[str]] = {
    "category": frozenset({"vendor_change_request", "regulatory", "spam_or_phishing"}),
    "sender_type": frozenset({"team", "staff"}),
}
REMOVED_READ_AS = {"category": "other", "sender_type": "unknown"}
# The kept sources (S3b, P2): `FIXED_FROM_V2` without the removed values. What `fixed_item`
# reads beside the item: values schema v3 still has, which the fixed rules read too.
KEPT_SOURCES: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
    item: tuple((f, vals - REMOVED_BY_FIELD.get(f, frozenset())) for f, vals in sources
                if vals - REMOVED_BY_FIELD.get(f, frozenset()))
    for item, sources in FIXED_FROM_V2.items()}  # fmt: skip


def fixed_item(values: Mapping[str, Any], item: str) -> bool:
    """Whether a classification says fixed item `item` holds: the item itself is true, or one of
    its kept v2 sources holds (`KEPT_SOURCES`: sender_type `regulator` for `regulator_claim`,
    `person`/`friend`/`family` for `private_individual`). The one test every Python reader of the
    items uses (v3.0.0 S3, OD-500), so readers agree with the fixed rules, which read the kept
    sources too (P2); it only ever adds risk. The removed values (S3a's bridge) aren't read: no
    classification holds them once `from_v2` and migration 0037 have run."""
    if values.get(item) is True:
        return True
    return any(values.get(field) in vals for field, vals in KEPT_SOURCES[item])


def with_fixed(values: Mapping[str, Any]) -> dict[str, Any]:
    """A v2 classification completed with the fixed items: each item it lacks, derived
    (`derive_fixed`). Items it has are kept. Its values are left as they are (`from_v2` reads
    the removed ones)."""
    return dict(values) | {k: v for k, v in derive_fixed(values).items() if k not in values}


def from_v2(values: Mapping[str, Any]) -> dict[str, Any]:
    """A v2 classification (or a stored or recorded one from before S3b) brought to schema v3
    (S3b, OD-500, D3): first the fixed items it lacks, derived from its values (`with_fixed`),
    then each removed value read as its field's least informative value (`category` `other`,
    `sender_type` `unknown`; `REMOVED_READ_AS`). The order matters: the items carry what the
    removed values said. Used by the local classifier, the decision models, preset B's Gemma
    source, `ecf eval rescore` and, in SQL, migration 0037."""
    out = with_fixed(values)
    for name, gone in REMOVED_BY_FIELD.items():
        if out.get(name) in gone:
            out[name] = REMOVED_READ_AS[name]
    return out


def derive_labels(labels: Mapping[str, Any]) -> dict[str, bool]:
    """The fixed-item labels a card's (or a corpus message's) v2 labels imply (schema doc §10,
    P8), for each item it doesn't label itself: true when any labelled source holds a mapped
    value; false only when every source is labelled (not `unknown`) and none does; otherwise
    left out (unlabelled, never false). `phishing` is never derived."""
    out: dict[str, bool] = {}
    for item, sources in FIXED_FROM_V2.items():
        if item in labels or item in NOT_DERIVED_LABELS:
            continue
        known = [(labels[f], vals) for f, vals in sources
                 if f in labels and labels[f] not in UNLABELLED]  # fmt: skip
        if any(v in vals for v, vals in known):
            out[item] = True
        elif len(known) == len(sources):
            out[item] = False
    return out


# The label map (S3b, P10, OD-500): how eval labels, corpus labels and old results' expected
# values are read. 2: a removed value is unlabelled (never `other`), after the fixed items it
# implies are derived; 1 (results without the key): read as labelled. Results record it, and
# `ecf eval compare` refuses two results with different maps.
LABEL_MAP = 2


def unlabel_removed(labels: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Labels through the label map: the fixed items they imply derived first (`derive_labels`,
    for items not labelled), then each field holding a removed value dropped (unlabelled). Also
    the fields dropped. Labels with no removed value come back unchanged (nothing derived)."""
    gone = tuple(f for f, vals in REMOVED_BY_FIELD.items() if labels.get(f) in vals)
    if not gone:
        return dict(labels), ()
    full = dict(labels) | derive_labels(labels)
    return {k: v for k, v in full.items() if k not in gone}, gone


def local_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    """A classification as the local model knows it (schema v2): without the fixed items, so
    the local actor is asked as before v3 (P6)."""
    return {k: v for k, v in values.items() if k not in FIXED_ITEMS}


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
    value_descriptions: tuple[str, ...] = ()  # each value's text; an ordinal's may be empty

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

    @functools.cached_property
    def digest(self) -> str:
        """A short hash of what the models are asked: the version, then each field's name, kind,
        description, values and value descriptions in order (the gate key, SPEC §9.3). Computed
        once per schema (A10; the dataclass is frozen and has no slots, so the cache lands in the
        instance's own dict)."""
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
        return FieldSpec(name, kind, desc, *_levels(name, spec.get("levels")))
    return FieldSpec(name, kind, desc)


def _levels(name: str, levels: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """An ordinal's levels, low to high, and their texts: `levels` is a list, or a mapping of each
    level to its text (classifier plan item 2)."""
    texts: tuple[str, ...] = ()
    if isinstance(levels, dict):
        texts = tuple(str(v).strip() for v in cast(dict[Any, Any], levels).values())
        levels = list(cast(dict[Any, Any], levels))
    if not isinstance(levels, list) or len(cast(list[Any], levels)) < 2:
        raise InvalidInputError(f"field {name}: ordinal needs at least two levels")
    lv = cast(list[Any], levels)
    if any(not isinstance(v, str) or not re.fullmatch(VALUE_NAME, v) for v in lv):
        raise InvalidInputError(f"field {name}: each level must be a lowercase name")
    if len(set(lv)) != len(lv):
        raise InvalidInputError(f"field {name}: levels must be unique")
    if any(not t for t in texts):
        raise InvalidInputError(f"field {name}: a level's text can't be empty")
    return tuple(cast(list[str], lv)), texts


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
            lines += [f"    {v}: {d}" for v, d in zip(f.values, f.value_descriptions, strict=False)]
        else:
            lines.append(f"- {f.name} (true/false): {f.description}")
    return lines


def check_safety_core(schema: CompiledSchema) -> None:
    """Refuse a schema without the fields ecf's safety logic reads (OD-475, OD-498), or with a
    value schema v3 removed (S3b, OD-500: the fixed items say it instead)."""
    for name, (kind, levels) in SAFETY_FIELDS.items():
        f = schema.fields.get(name)
        if f is None or f.kind.value != kind or (levels and f.values != levels):
            raise InvalidInputError(f"schema: {name} must be the {kind} field ecf ships")
    for name, gone in REMOVED_BY_FIELD.items():
        f = schema.fields.get(name)
        held = sorted(v for v in (f.values if f is not None else ()) if v in gone)
        if held:
            raise InvalidInputError(f"schema: {name} can't hold {', '.join(held)} (removed in"
                                    " v3; the fixed items say it)")  # fmt: skip


def _load(version: int) -> CompiledSchema:
    name = f"schema_v{version}.yaml"
    text = resources.files("ecf.data").joinpath(name).read_text(encoding="utf-8")
    return compile_schema(text, source=name)


@functools.cache
def load_schema() -> CompiledSchema:
    """The shipped schema (v3)."""
    schema = _load(CURRENT_VERSION)
    check_safety_core(schema)
    return schema


@functools.cache
def load_schema_v2() -> CompiledSchema:
    """Schema v2: what the local model is asked (`local_schema`), and for results, labels and
    rules made before v3 (OD-498)."""
    return _load(LOCAL_VERSION)


def local_schema(schema: CompiledSchema) -> CompiledSchema:
    """The schema the local model is asked for `schema` (P6): for the shipped v3, plain schema v2
    (with the same fallback key), so preset A's prompt, `num_predict`, prompt digest and gate key
    don't change with v3; its answer is completed with `with_fixed`. Never the install's user
    fields (S4, D8: the local model isn't measured with them, SPEC §1.5a item 2; the service
    refuses them while any A or B address exists), so A's and B's keys don't move with them. Any
    other version is asked as it is (v1, v2, the frozen v2.0.0 text)."""
    if schema.version != CURRENT_VERSION:
        return schema
    base = load_schema_v2()
    if schema.fallback_for is not None:
        base = dataclasses.replace(base, fallback_for=schema.fallback_for)
    return base


def without_extension(schema: CompiledSchema) -> CompiledSchema:
    """What a local answer (asked `local_schema`, so without user fields) is checked against once
    brought to v3: `schema` without the install's user fields and values (the shipped schema; P1).
    A rule reads the absent user fields as unknown (P14). `schema` itself when it has none."""
    if schema.extension is None:
        return schema
    return load_schema()


@functools.cache
def load_schema_v1() -> CompiledSchema:
    """Schema v1, for results and labels made before v2 (OD-475)."""
    return _load(1)


@functools.cache
def load_schema_v2_0_0() -> CompiledSchema:
    """The v2.0.0 schema text, frozen byte for byte (its header comment included) for eval runs
    with the new wording off (`ecf eval run --no-new-wording`, classifier plan 0.3); never used
    to classify mail."""
    name = "schema_v2_0_0.yaml"
    text = resources.files("ecf.data").joinpath(name).read_text(encoding="utf-8")
    return compile_schema(text, source=name)


# ---------------------------------------------------------------------------- extensions (OD-478)

# The one switch (OD-481; kept by OD-502, S4 C1): on only once the user layer's measurement (M2,
# SPEC §1.5a item 2b) passes; off, a `schema` section other than `default` is refused.
EXTENSIONS_ENABLED = False
EXTENSIONS_OFF = ("your fields are off in this release (SPEC §7.1, §1.5a); remove the `schema`"
                  " section")  # fmt: skip


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
# The heading and its rule (classifier plan item 7): added fields never move the shipped ones
EXT_HEADING = (
    "Added by this install (answer these too):\n"
    "Added fields and values never change how you answer the fields above. Choose an added"
    " category only when no built-in category fits."
)
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
    """A description: one printable ASCII line, not starting with - or :, within the cap."""
    if not isinstance(text, str) or not text.strip():
        out.problems.append(f"{where}: a description is required")
        return False
    t = text.strip()
    if not t.isprintable():
        out.problems.append(f"{where}: one line of printable text (no line breaks, control or"
                            " format characters)")  # fmt: skip
        return False
    if not t.isascii():  # review R2: look-alike letters (a Cyrillic i) and full-width marks
        out.problems.append(f"{where}: plain ASCII text only (letters a-z, digits, ordinary"
                            " punctuation)")  # fmt: skip
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
    warnings: list[str] = dataclasses.field(default_factory=list[str])  # allowed, likely wrong
    texts: list[tuple[str, str]] = dataclasses.field(default_factory=list[tuple[str, str]])


def _names_in(text: str, names: Any) -> list[str]:
    """The names `text` mentions as whole words (case ignored): `fraud_risk`, not `fraud`."""
    low = text.lower()
    return [n for n in names if re.search(rf"(?<![a-z0-9_]){re.escape(n)}(?![a-z0-9_])", low)]


_WORD = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"
_WRAP = r"[\"'*_`()\[\]]"  # quotes, markdown and brackets around a name (review R2)
_SEP = r"(?::|=|->|=>|\u2192|\uff1a)"  # `:`, `=`, arrows, a full-width colon


def fixed_as_field(text: str, name: str) -> bool:
    """Whether `text` writes fixed field `name` as a field name (P10, operator decision
    2026-10-14), not as plain English (case ignored, so `Priority: low` counts). A name with an
    underscore (`fraud_risk`, `bank_detail_change`) is a field name wherever it stands as
    a word. A one-word name (`priority`, `phishing`) is one only as an identifier: in backticks;
    maybe quoted, marked up or bracketed (`"phishing"`, `'priority'`, `**priority**`) and
    followed by `:`, `=`, `->`, `=>`, an arrow or a full-width colon; after `set`; or followed
    (after any of those, maybe bracketed) by one of the field's values (`true`, `false`, `yes`,
    `no` for a yes/no field; a level for an ordinal: `priority high`, `priority (high)`).
    Anything else ("a high priority customer", "phishing attempts") is plain
    English, which warns. Descriptions are printable ASCII (`_check_text`; review R2), so a
    full-width colon or a look-alike letter can't slip past."""
    word = _WORD.format(re.escape(name))
    if "_" in name:
        return re.search(word, text, re.IGNORECASE) is not None
    kind, levels = SAFETY_FIELDS[name]
    values = levels if kind == "ordinal" else ("true", "false", "yes", "no")
    alts = "|".join(re.escape(v) for v in values)
    # the name, maybe quoted or marked up (`"phishing"`, `'priority'`, `**priority**`, `(x)`);
    # not inside a longer name (`x_priority`)
    wrapped = (rf"(?<![A-Za-z0-9])(?<![A-Za-z0-9]_){_WRAP}*{re.escape(name)}{_WRAP}*"
               r"(?![A-Za-z0-9])")  # fmt: skip
    forms = (rf"`{re.escape(name)}`", rf"{wrapped}\s*{_SEP}", rf"(?<![A-Za-z0-9_])set\s+{word}",
             rf"{wrapped}\s*(?:{_SEP}\s*)?{_WRAP}*(?:{alts})(?![A-Za-z0-9_])")  # fmt: skip
    return any(re.search(f, text, re.IGNORECASE) is not None for f in forms)


def _check_names(out: _Parsed, user_fields: list[str]) -> None:
    """P10, D9b (as narrowed by the operator 2026-10-14): a description writing one of ecf's
    fixed fields as a field name (`fixed_as_field`) is refused, since the classifier would read it
    as an instruction about a field ecf relies on; one using a fixed field's name as plain
    English (any case), or naming a default field or another user field, warns. Only literal
    names are caught; values aren't checked (common words)."""
    for where, text in out.texts:
        fixed = [n for n in FIXED_FIELDS if fixed_as_field(text, n)]
        if fixed:
            out.problems.append(f"{where}: names ecf's fixed field{'s' if len(fixed) > 1 else ''}"
                                f" {', '.join(fixed)}; a description of your own field or value"
                                " can't refer to ecf's fixed fields (SPEC §12.4)")  # fmt: skip
        own = ("category" if where.startswith("schema.category_values.")
               else where.removeprefix("schema.fields.").split(".", 1)[0])  # fmt: skip
        plain = [n for n in _names_in(text, FIXED_FIELDS) if n not in fixed]
        other = plain + _names_in(text, [f for f in (*DEFAULT_FIELDS, *user_fields) if f != own])
        if other:
            out.warnings.append(f"{where}: mentions the field{'s' if len(other) > 1 else ''}"
                                f" {', '.join(other)}; the classifier reads descriptions as"
                                " instructions, so check it doesn't change how that field is"
                                " answered")  # fmt: skip


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
            out.texts.append((where, str(d).strip()))
    user_fields = [str(n) for n in rf if isinstance(n, str)]
    _clashes(base, added, reserved, out, user_fields)
    _check_names(out, user_fields)
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
    if ok:
        out.texts.append((f"{where}.description", str(spec["description"]).strip()))
    kind = spec.get("type")
    options = spec.get("values") if kind == "enum" else spec.get("levels")
    count, told = _options(kind, options, where, out, added)
    ok = told and ok
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


def _options(kind: Any, options: Any, where: str, out: _Parsed,
             added: list[tuple[str, str]]) -> tuple[int, bool]:  # fmt: skip
    """A user field's values or levels: how many, and whether every text passes. Each needs its
    text (R10: an ordinal's levels a mapping, not a list); an enum with no catch-all warns."""
    count, ok = 0, True
    if kind in ("enum", "ordinal") and isinstance(options, dict):  # level texts: plan item 7
        vals = cast(dict[Any, Any], options)
        count = len(vals)
        key = "values" if kind == "enum" else "levels"
        for v, d in vals.items():
            told = _check_text(d, f"{where}.{key}.{str(v)[:40]}", out)
            if told:
                out.texts.append((f"{where}.{key}.{str(v)[:40]}", str(d).strip()))
            ok = told and ok
            if kind == "enum" and isinstance(v, str):  # levels never become labels
                added.append((f"{where}.values.{v}", v))
        if kind == "enum" and not CATCH_ALLS & {str(v) for v in vals}:
            out.warnings.append(f"{where}: no catch-all value ({', '.join(sorted(CATCH_ALLS))});"
                                " without one the classifier must pick a value that may not"
                                " fit")  # fmt: skip
    elif kind == "ordinal" and isinstance(options, list):
        count = len(cast(list[Any], options))
        out.problems.append(f"{where}.levels: give each level its text (a mapping of level to"
                            " description, low to high), not a list")  # fmt: skip
    return count, ok


def _bad_names(kind: Any, options: Any, where: str, out: _Parsed) -> bool:
    """Every value or level that isn't a lowercase name, and every repeated level, listed."""
    names: list[Any] = []
    if kind in ("enum", "ordinal") and isinstance(options, dict):
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
             out: _Parsed, user_fields: Sequence[str] = ()) -> None:  # fmt: skip
    """An added value can't be any enum value of any field, a value schema v3 removed (S3b: the
    local model is still asked v2, which has them; an extension can't bring one back), a
    built-in label or a rule id: it may become a label (S8). Ordinal levels never become labels,
    so `none` or `high` may repeat one. A user field's catch-all (`CATCH_ALLS`) may repeat
    another field's (D15, P12): a rule that labels from such a field is refused instead
    (`ecf_server.config`); an added `category` value still needs a new name. Nor can an added
    value take a field's name, ecf's 13 or the user's own (review R7)."""
    taken: dict[str, str] = {}
    for f in base.fields.values():
        if f.kind is FieldKind.ENUM:
            for v in f.values:
                taken.setdefault(v, f"a value of {f.name}")
    for name, gone in REMOVED_BY_FIELD.items():
        for v in gone:
            taken.setdefault(v, f"a value of {name} removed in v3")
    for v in reserved:
        taken.setdefault(v, "a built-in label or a rule id")
    # review R7: nor a field's name, so `fraud_risk: ...` never reads as one in the prompt
    for name in (*FIXED_FIELDS, *DEFAULT_FIELDS):
        taken.setdefault(name, f"the name of ecf's field {name}")
    for name in user_fields:
        taken.setdefault(name, f"the name of your field {name}")
    for where, v in added:
        if v in CATCH_ALLS and where.startswith("schema.fields."):
            continue  # a user field may reuse a catch-all (D15, P12); never as a label (config)
        if v in taken:
            out.problems.append(f"{where}: {v!r} is already {taken[v]}; added values need new"
                                " names")  # fmt: skip
        taken[v] = f"a value at {where.removeprefix('schema.')}"


def check_extension(base: CompiledSchema, ext: Any,
                    reserved: frozenset[str] = frozenset()) -> list[str]:  # fmt: skip
    """Every violation of the caps and content rules, as short messages ([] when it is fine).
    `reserved` holds names an added value can't take: the server passes its built-in labels and
    the fixed, default and applied rule ids (`ecf` can't import them)."""
    p = _parse_ext(base, ext, reserved)
    return p.limits + p.problems


def extension_warnings(base: CompiledSchema, ext: Any) -> list[str]:
    """What an extension may do but likely shouldn't (shown by the dry run and apply, never
    refused): an enum field with no catch-all (D15), a description naming a default field or
    another user field (D9b)."""
    return _parse_ext(base, ext, frozenset()).warnings if ext else []


def read_bytes(schema: CompiledSchema) -> int:
    """What one Claude classify read carries besides the email (P16): the schema text and the
    JSON schema, in bytes (`claude_review.schema_fields`)."""
    return (len(schema.prompt_block.encode("utf-8"))
            + len(json.dumps(schema.json_schema()).encode("utf-8")))  # fmt: skip


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
    try:  # per Claude classify read, with the extension (P16); the shipped schema's if refused
        per_read = read_bytes(extend_schema(base, cast(dict[str, Any], ext)) if ext else base)
    except (InvalidInputError, SchemaLimitError):
        per_read = read_bytes(base)
    line = (f"schema: {n_fields} of {MAX_EXT_FIELDS} fields, {n_cats} of"
            f" {MAX_EXT_CATEGORY_VALUES} category values, {chars:,} of {MAX_EXT_TEXT:,}"
            f" characters; {per_read:,} bytes of schema per classify read (shipped"
            f" {read_bytes(base):,})")  # fmt: skip
    if near:
        line += " (near the limit)"
    return {k: {"used": n, "limit": cap} for k, (n, cap) in use.items()} | {
        "near": near, "line": line, "read_bytes": per_read}  # fmt: skip
