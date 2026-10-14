"""Rules (SPEC §8.6): structured YAML compiled to predicates. No eval.

A rule set is an ordered list; the first rule whose `when` matches decides. A condition is a
Pydantic discriminated union: a comparison on an operand (`field`, `fact`, `trigger`, `address`)
with `eq`, `in`, `gte` or `lte` (ordinals by level order), or `and` / `or` / `not`.

A comparison on a field ecf doesn't fix (`ecf.schema.FIXED_FIELDS`: `category`, `sender_type`
and the install's own fields) is unknown (None) when the classification lacks the field, holds a
value the field doesn't have, or a non-boolean for a boolean (v3.0.0 S4, P14; R17); a fixed
field compares as it is (an absent fixed item reads false, step 1c P13). An ordinal level the
field no longer has is unknown too. Unknown propagates (Kleene logic: `not` unknown is unknown;
`and` is false if any part is false, `or` true if any part is true), and a condition matches only
when it is true, so `not` over a missing or out-of-spec field can't match.
Rules may only emit non-sending actions; sends and drafts come from actor proposals (OD-170).

**Three layers (v3.0.0 S3, OD-500; S4, OD-502; SPEC §8.6).** Every rule set in force is composed
by `compose`: (1) ecf's fixed rules (`fixed_rules.yaml`: fraud_guard, regulatory, fraud_weak,
unverified_payment_sender, phishing), always first; (2) the applied rules file's rules
with new ids, in file order; (3) the default rules (`starter_rules.yaml`) in their own order,
with each default the file replaces (a rule with the same id) taking that default's position
(spliced, P4) and each id in the file's `default_rules_off` left out; then `otherwise` unless
something before it matches everything. A file whose last rule has no `when` is a catch-all: the
defaults can't run, so they aren't added (D2, the replace case). The file's layer compiles
without an appended `otherwise` (P3). A rules file can't use a fixed rule's id (nor its id
before a rename, `LEGACY_FIXED_IDS`, OD-508), nor a condition
on a v2 value schema v3 removed (`REMOVED_VALUES`, refused with its replacement; S3b), and may
read the fixed items; `check_layers` refuses an off list ecf can't follow (apply), while at load
an unknown off id is skipped with a warning (a later release may rename a default). Applied sets
copied from the v2 starter rules hold the fixed ids: `convert_v2` strips each fixed-id rule
identical to the v2 starter text (`starter_rules_v2.yaml`, frozen) and keeps the rest; a
customised one is left for the caller to refuse. Compiled sets are cached on the schema and the
text (P8): rules are evaluated per item.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    StrictStr,
    Tag,
    ValidationError,
    model_validator,
)

from ecf.errors import InvalidInputError
from ecf.schema import (
    FIXED_FIELDS,
    FIXED_ITEMS,
    REMOVED_VALUES,
    V1_VALUE_ALIASES,
    CompiledSchema,
    FieldKind,
)
from ecf.yamlio import load_yaml
from ecf_server.log_bridge import log

FACTS: dict[str, tuple[str, ...] | None] = {  # None = boolean fact
    "sender_origin": ("internal", "external"),
    "auth_result": ("pass", "fail", "none"),
    "sender_seen_before": None,
    "reply_to_mismatch": None,
    "recipient_mismatch": None,
    "bulk_signal": None,
    "content_unscanned": None,
    "sender_verified": None,
    "payment_keyword": None,  # a payment keyword in the text (§8.5; OD-262)
    "gift_card_keyword": None,  # a gift-card term: money for impersonation only (§8.5; OD-457)
    "bec_opener": None,  # two different impersonation-opener phrases (§8.5; OD-479)
    "tax_form_request": None,  # employee tax data asked to be sent (§8.5; OD-482)
    "from_org_address": None,  # From is in org_addresses (§7.2; V1.6, OD-431)
    "impersonates_internal": None,  # trigger 7's org address or name clause (§8.5; OD-436)
    "self_sent": None,  # Gmail: the account's own mail to itself (§7.2; OD-446)
}
TRIGGERS = frozenset({"fraud", "fraud_weak", "regulator", "unverified_payment"})
ADDRESS_ATTRS: dict[str, tuple[str, ...]] = {"sensitivity": ("standard", "high")}
RULE_ACTIONS = frozenset(
    {"label", "flag", "escalate", "leave", "mark_read", "archive", "move", "junk"}
)
TARGET_ACTIONS = frozenset({"label", "move"})
HIDE_ACTIONS = frozenset({"mark_read", "archive", "move", "junk"})
_STRICT = ConfigDict(extra="forbid", frozen=True)


class Hide(StrEnum):
    NEVER = "never"
    CORROBORATED = "corroborated"


# Conditions: the comparison and the and/or/not forms a rule's `when` is built from.


class Compare(BaseModel):
    model_config = _STRICT
    field: str | None = None
    fact: str | None = None
    trigger: str | None = None
    address: str | None = None
    eq: StrictStr | StrictBool | None = None
    in_: list[StrictStr] | None = Field(default=None, alias="in")
    gte: StrictStr | None = None
    lte: StrictStr | None = None

    @model_validator(mode="after")
    def _shape(self) -> Compare:
        operands = [x for x in (self.field, self.fact, self.trigger, self.address) if x is not None]
        if len(operands) != 1:
            raise ValueError("a comparison needs exactly one of field, fact, trigger, address")
        ops = [x for x in (self.eq, self.in_, self.gte, self.lte) if x is not None]
        if self.trigger is not None:
            if ops:
                raise ValueError("a trigger is a true/false test and takes no operator")
        elif len(ops) != 1:
            raise ValueError("a comparison needs exactly one of eq, in, gte, lte")
        return self


class And(BaseModel):
    model_config = _STRICT
    and_: list[Condition] = Field(alias="and", min_length=1)


class Or(BaseModel):
    model_config = _STRICT
    or_: list[Condition] = Field(alias="or", min_length=1)


class Not(BaseModel):
    model_config = _STRICT
    not_: Condition = Field(alias="not")


def _tag(v: Any) -> str:
    if isinstance(v, dict):
        keys = set(cast(dict[str, Any], v))
        for k in ("and", "or", "not"):
            if k in keys:
                return k
        return "cmp"
    if isinstance(v, And):
        return "and"
    if isinstance(v, Or):
        return "or"
    return "not" if isinstance(v, Not) else "cmp"


Condition = Annotated[
    Annotated[Compare, Tag("cmp")]
    | Annotated[And, Tag("and")]
    | Annotated[Or, Tag("or")]
    | Annotated[Not, Tag("not")],
    Discriminator(_tag),
]
And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()


# Rules: a rule's actions, labels and continue condition, and the rule set as the YAML file
# holds it.


class LabelFrom(BaseModel):
    model_config = _STRICT
    field: str


class ActionSpec(BaseModel):
    model_config = _STRICT
    action: str
    target: str | LabelFrom | None = None
    if_: Condition | None = Field(default=None, alias="if")


class ContinueIf(BaseModel):
    model_config = _STRICT
    continue_if: Condition


class Rule(BaseModel):
    model_config = _STRICT
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    when: Condition | None = None  # None = always
    then: list[ActionSpec] = Field(default_factory=list[ActionSpec])
    actor: Literal["none", "continue"] | ContinueIf = "none"
    hide: Hide = Hide.CORROBORATED

    @model_validator(mode="before")
    @classmethod
    def _actions(cls, data: Any) -> Any:
        if isinstance(data, dict) and "then" in data:
            d: dict[str, Any] = dict(cast(dict[str, Any], data))
            d["then"] = [_action_item(a) for a in cast(list[Any], d["then"] or [])]
            return d
        return cast(Any, data)


def _action_item(raw: Any) -> dict[str, Any]:
    """`flag` | `{label: suspicious}` | `{label: {field: category}}` | `{flag: true, if: {...}}`."""
    if isinstance(raw, str):
        return {"action": raw}
    if isinstance(raw, dict):
        d = dict(cast(dict[str, Any], raw))
        cond = d.pop("if", None)
        if len(d) != 1:
            raise ValueError(f"an action has one name: {sorted(d)}")
        ((name, value),) = d.items()
        item: dict[str, Any] = {"action": name}
        if value is not True:
            item["target"] = value
        if cond is not None:
            item["if"] = cond
        return item
    raise ValueError(f"not an action: {raw!r}")


class RuleSet(BaseModel):
    model_config = _STRICT
    version: Literal[1]
    rules: list[Rule] = Field(min_length=1)
    # default rules (`starter_rules.yaml`) this file turns off (S4, D2); not in the fixed file
    default_rules_off: list[Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]] = Field(
        default_factory=list[str])  # fmt: skip


# Evaluation: the input a rule set runs against, the decision it gives, and how a condition is
# judged true, false or unknown.


@dataclass(frozen=True)
class RuleInput:
    classification: dict[str, Any]
    facts: dict[str, Any]
    triggers: frozenset[str] = frozenset()
    address: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Action:
    name: str
    target: str | None = None


@dataclass(frozen=True)
class Decision:
    rule_id: str
    actions: tuple[Action, ...]
    to_actor: bool
    hide: Hide


@dataclass(frozen=True)
class CompiledRules:
    rules: tuple[Rule, ...]
    schema: CompiledSchema
    aliased: tuple[str, ...] = ()  # rule ids that used a schema v1 value (V1_ALIASES)
    off: tuple[str, ...] = ()  # the file's `default_rules_off`, as written (one file only)

    def evaluate(self, inp: RuleInput) -> Decision:
        for rule in self.rules:
            if rule.when is None or _eval(rule.when, inp, self.schema) is True:
                actions = tuple(
                    Action(a.action, t)
                    for a in rule.then
                    if a.if_ is None or _eval(a.if_, inp, self.schema) is True
                    for t in [_target(a.target, inp)]
                    if t is not None or a.action not in TARGET_ACTIONS
                )
                if isinstance(rule.actor, ContinueIf):
                    to_actor = _eval(rule.actor.continue_if, inp, self.schema) is True
                else:
                    to_actor = rule.actor == "continue"
                return Decision(rule.id, actions, to_actor, rule.hide)
        raise InvalidInputError("no rule matched; the last rule must match everything")


def _target(t: str | LabelFrom | None, inp: RuleInput) -> str | None:
    """A label from a field the classification lacks (an item classified before that field
    existed) is None, and the action is dropped."""
    if isinstance(t, LabelFrom):
        value = inp.classification.get(t.field)
        return None if value is None else str(value)
    return t


def _eval(c: Compare | And | Or | Not, inp: RuleInput, schema: CompiledSchema) -> bool | None:
    """True, False or None (unknown); see the module docstring."""
    if isinstance(c, And | Or):
        parts = [_eval(x, inp, schema) for x in (c.and_ if isinstance(c, And) else c.or_)]
        decisive = isinstance(c, Or)  # or: any true decides; and: any false
        if decisive in parts:
            return decisive
        return None if None in parts else not decisive
    if isinstance(c, Not):
        inner = _eval(c.not_, inp, schema)
        return None if inner is None else not inner
    return _compare(c, inp, schema)


def _compare(c: Compare, inp: RuleInput, schema: CompiledSchema) -> bool | None:  # noqa: PLR0911
    if c.trigger is not None:
        return c.trigger in inp.triggers
    if c.field is not None:
        if c.field not in FIXED_FIELDS and not _in_spec(c.field, inp.classification, schema):
            return None  # absent or out of spec on a non-fixed field: unknown (P14)
        value = inp.classification.get(c.field)
    elif c.fact is not None:
        value = inp.facts.get(c.fact)
    else:
        value = inp.address.get(cast(str, c.address))
    if c.eq is not None:
        return value == c.eq
    if c.in_ is not None:
        return value in c.in_
    spec = schema.fields[cast(str, c.field)]
    if value is None:
        return False
    bound = c.gte if c.gte is not None else cast(str, c.lte)
    if str(value) not in spec.values or bound not in spec.values:
        return None  # a level the ordinal no longer has (its extension changed): unknown
    rank = spec.rank(str(value))
    return rank >= spec.rank(bound) if c.gte is not None else rank <= spec.rank(bound)


def _in_spec(name: str, classification: dict[str, Any], schema: CompiledSchema) -> bool:
    """Whether the classification holds a value field `name` has: present, a boolean for a
    boolean, one of the values or levels otherwise."""
    spec = schema.fields.get(name)
    if spec is None or name not in classification:
        return False
    value = classification[name]
    if spec.kind is FieldKind.BOOLEAN:
        return isinstance(value, bool)
    return isinstance(value, str) and value in spec.values


# Compiling a rules file: the shipped files, the fixed rule ids (and old ids a rename left),
# and the checks every rule and condition must pass against the schema.


# Schema v1 values a rules file may still use; read as their v2 value (OD-475). Stored and
# applied rules are never rewritten: the compiler maps them and names the rules that did.
V1_ALIASES = {(f, v): new for f, renames in V1_VALUE_ALIASES.items()
              for v, new in renames.items()}  # fmt: skip


def _alias_v1(node: Any, schema: CompiledSchema, hits: set[str], rule: str = "") -> Any:
    if isinstance(node, list):
        return [_alias_v1(x, schema, hits, rule) for x in cast(list[Any], node)]
    if not isinstance(node, dict):
        return node
    d = dict(cast(dict[str, Any], node))
    rule = str(d["id"]) if isinstance(d.get("id"), str) and "rules" not in d else rule
    field_name = d.get("field")
    for op in ("eq", "in", "gte", "lte"):
        if op not in d or not isinstance(field_name, str):
            continue
        vals = cast(list[Any], d[op] if isinstance(d[op], list) else [d[op]])
        spec = schema.fields.get(field_name)
        mapped: list[Any] = []
        for v in vals:
            new = V1_ALIASES.get((field_name, v)) if isinstance(v, str) else None
            use = new is not None and spec is not None and v not in spec.values
            if use:
                hits.add(rule)
            mapped.append(new if use else v)
        d[op] = mapped if isinstance(d[op], list) else mapped[0]
    return {k: _alias_v1(v, schema, hits, rule) if k != "id" else v for k, v in d.items()}


FIXED_FILE = "fixed_rules.yaml"
DEFAULT_FILE = "starter_rules.yaml"
V2_STARTER_FILE = "starter_rules_v2.yaml"  # v2.0.0-v2.2.3's starter rules, byte for byte (P7)
CATCH_ALL = "otherwise"


@functools.cache
def _data(name: str) -> str:
    return resources.files("ecf_server.data").joinpath(name).read_text("utf-8")


@functools.cache
def fixed_ids() -> tuple[str, ...]:
    """The fixed rules' ids, in order."""
    raw = cast(dict[str, Any], load_yaml(_data(FIXED_FILE), source=FIXED_FILE))
    return tuple(str(r["id"]) for r in cast(list[dict[str, Any]], raw["rules"]))


# A fixed rule's id before a release renamed it -> its id now (v3.0.0 step 5, OD-508). Still
# ecf's: a rules file can't use it as a rule id or turn it off, and `convert_v2` strips its v2
# starter copy. It stays a label a rules file may write.
LEGACY_FIXED_IDS: dict[str, str] = {"spam_or_phishing": "phishing"}


def is_fixed_id(rid: object) -> bool:
    """A fixed rule's id, now or before a rename (`LEGACY_FIXED_IDS`)."""
    return rid in fixed_ids() or (isinstance(rid, str) and rid in LEGACY_FIXED_IDS)


def renamed_from(rid: object) -> str | None:
    """The old id of the fixed rule a release renamed to `rid`; None for any other id."""
    return next((o for o, n in LEGACY_FIXED_IDS.items() if n == rid), None)


def rename_note(rid: object) -> str:
    """What a refusal adds, in brackets, for an id a fixed rule's rename touches: its old id, or
    the new id a rule of your own may already have ('' for any other)."""
    if isinstance(rid, str) and rid in LEGACY_FIXED_IDS:
        return f"ecf renamed its fixed rule {rid} to {LEGACY_FIXED_IDS[rid]} in v3.0.0"
    old = renamed_from(rid)
    if old:
        return (f"{old} was renamed {rid} in v3.0.0; if this rule is your own, give it another"
                " id")  # fmt: skip
    return ""


def _noted(rid: object) -> str:
    note = rename_note(rid)
    return f" ({note})" if note else ""


def compile_rules(text: str, schema: CompiledSchema, *, source: str = "rules") -> CompiledRules:
    """A rules file (applied, proposed or the default rules) on its own, checked; with
    `otherwise` (no actions, `actor: continue`) appended when its last rule has a `when`. Mail
    is decided by `compose`, which layers it (and every validator checks `compose`'s set, P2);
    this is the file alone, for tests and tools."""
    return _compile(text, schema, source=source, fixed=False)


def _compile(text: str, schema: CompiledSchema, *, source: str, fixed: bool,
             append: bool = True) -> CompiledRules:  # fmt: skip
    hits: set[str] = set()
    raw = _alias_v1(load_yaml(text, source=source), schema, hits)
    try:
        rs = RuleSet.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise InvalidInputError(f"{source}: {where}: {first['msg']}") from exc
    ids = [r.id for r in rs.rules]
    if len(set(ids)) != len(ids):
        raise InvalidInputError(f"{source}: duplicate rule ids")
    added = added_values(schema)
    for i, r in enumerate(rs.rules):
        if r.id in added:  # a rule id may name a label; an added value would too (S8)
            raise InvalidInputError(f"{source}: rule id {r.id} is a value the schema extension"
                                    " adds; rename the rule")  # fmt: skip
        if not fixed:
            _check_user_rule(r, last=i == len(rs.rules) - 1, source=source)
        _check_rule(r, schema, source)
    if fixed and rs.default_rules_off:
        raise InvalidInputError(f"{source}: default_rules_off is for a rules file")
    out = tuple(rs.rules)
    if not fixed and append and out[-1].when is not None:
        out = (*out, Rule(id=CATCH_ALL, actor="continue"))
    return CompiledRules(out, schema, tuple(sorted(hits)), tuple(rs.default_rules_off))


def _check_user_rule(r: Rule, *, last: bool, source: str) -> None:
    """A rules file can't redefine a fixed rule, can't use `otherwise` but for a last rule with
    no `when` (ecf appends one otherwise), can't have a rule with no `when` but its last, nor
    one with no `when` and a default rule's id (review R3: the defaults after it would never
    run, while the dry run said they do), and can't test a value schema v3 removed (OD-500)."""
    where = f"{source}: rule {r.id}"
    if is_fixed_id(r.id):
        raise InvalidInputError(f"{where} is one of ecf's fixed rules, run first; remove it from"
                                f" your file{_noted(r.id)}")  # fmt: skip
    if r.id == CATCH_ALL and not (last and r.when is None):
        raise InvalidInputError(f"{where}: the id {CATCH_ALL} is only for a last rule with no"
                                " `when` (ecf adds one when your file has none)")  # fmt: skip
    if r.when is None and not last:  # review R3: the rules after it could never run
        raise InvalidInputError(f"{where} has no `when`, so it matches everything; only your last"
                                " rule may (it then replaces ecf's default rules)")  # fmt: skip
    if r.when is None and r.id != CATCH_ALL and r.id in default_ids():  # review R3
        raise InvalidInputError(f"{where} has no `when` and a default rule's id: it would replace"
                                f" ecf's default {r.id} with a rule that matches everything;"
                                " give it a `when`, or another id")  # fmt: skip
    conds = [r.when, r.actor.continue_if if isinstance(r.actor, ContinueIf) else None]
    conds += [a.if_ for a in r.then]
    for cond in conds:
        for c in _compares(cond):
            for v in _wanted(c):
                use = REMOVED_VALUES.get((str(c.field), str(v))) if c.field else None
                if use is not None:
                    raise InvalidInputError(f"{where}: {c.field}: {v} is no longer a value ecf"
                                            f" asks for; use `{use}`")  # fmt: skip


def _compares(c: Compare | And | Or | Not | None) -> list[Compare]:
    if c is None:
        return []
    if isinstance(c, And | Or):
        return [x for part in (c.and_ if isinstance(c, And) else c.or_) for x in _compares(part)]
    if isinstance(c, Not):
        return _compares(c.not_)
    return [c]


def _wanted(c: Compare) -> list[str | bool]:
    return [x for x in (c.eq, c.gte, c.lte) if x is not None] + list(c.in_ or [])


def added_values(schema: CompiledSchema) -> set[str]:
    """The `category` values and extension enum values the install's extension adds."""
    ext: dict[str, Any] = schema.extension or {}
    out = set(cast(dict[str, Any], ext.get("category_values") or {}))
    for name in schema.extension_fields:
        f = schema.fields[name]
        if f.kind is FieldKind.ENUM:
            out |= set(f.values)
    return out


def _check_rule(r: Rule, schema: CompiledSchema, source: str) -> None:
    where = f"{source}: rule {r.id}"
    for cond in (r.when, r.actor.continue_if if isinstance(r.actor, ContinueIf) else None):
        if cond is not None:
            _check_cond(cond, schema, where)
    for a in r.then:
        if a.action not in RULE_ACTIONS:
            raise InvalidInputError(f"{where}: action {a.action!r} is not allowed in rules")
        if (a.target is None) == (a.action in TARGET_ACTIONS):
            raise InvalidInputError(f"{where}: {a.action} target is wrong")
        if isinstance(a.target, LabelFrom):
            f = schema.fields.get(a.target.field)
            if a.action != "label" or f is None or f.kind is not FieldKind.ENUM:
                raise InvalidInputError(f"{where}: label from field needs an enum field")
        if a.if_ is not None:
            _check_cond(a.if_, schema, where)
        if r.hide is Hide.NEVER and a.action in HIDE_ACTIONS:
            raise InvalidInputError(f"{where}: hide: never, but {a.action} hides mail")


def _check_cond(c: Compare | And | Or | Not, schema: CompiledSchema, where: str) -> None:
    if isinstance(c, And | Or):
        for x in c.and_ if isinstance(c, And) else c.or_:
            _check_cond(x, schema, where)
    elif isinstance(c, Not):
        _check_cond(c.not_, schema, where)
    elif c.trigger is not None:
        if c.trigger not in TRIGGERS:
            raise InvalidInputError(f"{where}: unknown trigger {c.trigger!r}")
    else:
        _check_values(c, _allowed_values(c, schema, where), where)


def _allowed_values(c: Compare, schema: CompiledSchema, where: str) -> tuple[str, ...] | None:
    """The values an operand can take (None = boolean). Also rejects gte/lte on non-ordinals."""
    if c.field is not None:
        f = schema.fields.get(c.field)
        if f is None and c.field in FIXED_ITEMS:  # a schema before v3 (an eval's frozen text):
            return None  # the item is absent from its answers, so false (step 1c P13)
        if f is None:
            raise InvalidInputError(f"{where}: unknown field {c.field!r}")
        if (c.gte or c.lte) and f.kind is not FieldKind.ORDINAL:
            raise InvalidInputError(f"{where}: gte/lte need an ordinal field, not {c.field}")
        return None if f.kind is FieldKind.BOOLEAN else f.values
    if c.gte or c.lte:
        raise InvalidInputError(f"{where}: gte/lte only apply to ordinal fields")
    if c.fact is not None:
        if c.fact not in FACTS:
            raise InvalidInputError(f"{where}: unknown fact {c.fact!r}")
        return FACTS[c.fact]
    attr = cast(str, c.address)
    if attr not in ADDRESS_ATTRS:
        raise InvalidInputError(f"{where}: unknown address attribute {attr!r}")
    return ADDRESS_ATTRS[attr]


def _check_values(c: Compare, values: tuple[str, ...] | None, where: str) -> None:
    wanted = [x for x in (c.eq, c.gte, c.lte) if x is not None] + list(c.in_ or [])
    for w in wanted:
        if values is None and not isinstance(w, bool):
            raise InvalidInputError(f"{where}: boolean comparison needs true/false")
        if values is not None and w not in values:
            raise InvalidInputError(f"{where}: {w!r} is not a valid value")


# Composing the rules in force: the fixed rules, the user's file and the default rules in
# layers, cached per schema, with what the dry run and apply say about the layering.


# a compiled set, or the refusal it raised (A4: a refused applied set is re-raised from here, not
# compiled again for every item)
_CACHE: dict[tuple[str, str, str | None], CompiledRules | InvalidInputError] = {}
_CACHE_MAX = 32


def _schema_key(schema: CompiledSchema) -> str:
    """What compiling against `schema` depends on: its digest (fields, values, any fallback) and
    its extension (which fields are extension fields, and the values it adds)."""
    ext = json.dumps(schema.extension, sort_keys=True) if schema.extension is not None else ""
    return f"{schema.digest}|{ext}"


def _cached(key: tuple[str, str, str | None], build: Callable[[], CompiledRules]) -> CompiledRules:
    hit = _CACHE.get(key)
    if hit is None:
        try:
            hit = build()
        except InvalidInputError as exc:
            hit = exc
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = hit
    if isinstance(hit, InvalidInputError):
        raise InvalidInputError(hit.detail, **hit.extra)
    return hit


def load_fixed_rules(schema: CompiledSchema) -> CompiledRules:
    """ecf's fixed rules, compiled against `schema` (cached per schema, P8)."""
    return _cached((_schema_key(schema), FIXED_FILE, None),
                   lambda: _compile(_data(FIXED_FILE), schema, source=FIXED_FILE,
                                    fixed=True))  # fmt: skip


def load_default_rules(schema: CompiledSchema) -> CompiledRules:
    """ecf's default rules on their own, compiled against `schema` (cached per schema, P8). The
    file ends with `otherwise`."""
    return _cached((_schema_key(schema), DEFAULT_FILE, None),
                   lambda: _compile(_data(DEFAULT_FILE), schema, source=DEFAULT_FILE,
                                    fixed=False))  # fmt: skip


@functools.cache
def default_ids() -> tuple[str, ...]:
    """The default rules' ids, in order (`otherwise` last). Part of the rules-file format: a
    file replaces a default by its id, or turns it off (SPEC §8.6; renaming one is a major
    version, OD-480)."""
    raw = cast(dict[str, Any], load_yaml(_data(DEFAULT_FILE), source=DEFAULT_FILE))
    return tuple(str(r["id"]) for r in cast(list[dict[str, Any]], raw["rules"]))


def catch_all(user: CompiledRules) -> bool:
    """Whether a file's last rule has no `when` (the replace case, D2): only a bare last rule
    counts; a condition that is always true isn't detected (P23)."""
    return bool(user.rules) and user.rules[-1].when is None


def layered(user: CompiledRules, defaults: CompiledRules,
            fixed: CompiledRules) -> tuple[Rule, ...]:  # fmt: skip
    """The three layers (module docstring): the fixed rules; the file's new-id rules; the
    defaults with the file's same-id rules spliced in and its off ids left out; `otherwise`
    unless the set already ends with a catch-all. A catch-all file is the replace case."""
    if catch_all(user):
        return (*fixed.rules, *user.rules)
    ids = {r.id for r in defaults.rules}
    mine = {r.id: r for r in user.rules if r.id in ids}
    off = set(user.off)
    new = [r for r in user.rules if r.id not in ids]
    rest = [mine.get(r.id, r) for r in defaults.rules if r.id not in off or r.id == CATCH_ALL]
    out = (*fixed.rules, *new, *rest)
    if out[-1].when is not None:
        out = (*out, Rule(id=CATCH_ALL, actor="continue"))
    return out


def compose(schema: CompiledSchema, text: str | None = None, *,
            source: str = "rules") -> CompiledRules:  # fmt: skip
    """The rules in force: the fixed rules, then the rules file `text` (an applied or proposed
    set) layered over the default rules (`layered`), or the default rules alone when it is None.
    Cached on the schema and the text, which holds the off list (P8). Raises InvalidInputError
    when `text` (or, against this schema, the default rules) is refused. An off id that isn't a
    default rule is skipped with a warning here (`check_layers` refuses it at apply)."""
    body = _data(DEFAULT_FILE) if text is None else text
    where = DEFAULT_FILE if text is None else source

    def build() -> CompiledRules:
        fixed = load_fixed_rules(schema)
        defaults = load_default_rules(schema)
        if text is None:
            return CompiledRules((*fixed.rules, *defaults.rules), schema, defaults.aliased)
        user = _compile(body, schema, source=where, fixed=False, append=False)
        unknown = layer_warnings(user)
        if unknown:
            log.warning("rules.default_off_unknown", ids=len(unknown))
        return CompiledRules(layered(user, defaults, fixed), schema, user.aliased, user.off)

    return _cached((_schema_key(schema), body, where), build)


def layer_warnings(user: CompiledRules) -> list[str]:
    """Off ids ecf skips at load: not a default rule's (a later release may rename one), or one
    ecf never turns off (`otherwise`, a fixed rule's id)."""
    out: list[str] = []
    for rid in user.off:
        if rid not in default_ids() or rid == CATCH_ALL:
            out.append(f"default_rules_off: {rid} isn't one of ecf's default rules; skipped")
    return out


def check_layers(text: str, schema: CompiledSchema, *, source: str = "rules") -> None:
    """What `ecf config apply` refuses in a file's layering (P17): an off id that isn't a default
    rule, `otherwise` or a fixed rule's id in the off list, an id both replaced and turned off,
    and an off list in a catch-all file (whose defaults never run)."""
    user = _compile(text, schema, source=source, fixed=False, append=False)
    where = f"{source}: default_rules_off"
    if len(set(user.off)) != len(user.off):
        raise InvalidInputError(f"{where}: an id is listed twice")
    for rid in user.off:
        if is_fixed_id(rid):
            raise InvalidInputError(f"{where}: {rid} is one of ecf's fixed rules, which always"
                                    " run first and can't be turned off"
                                    + _noted(rid))  # fmt: skip
        if rid == CATCH_ALL:
            raise InvalidInputError(f"{where}: {CATCH_ALL} is ecf's catch-all and can't be"
                                    " turned off; end your file with a rule with no `when`"
                                    " instead")  # fmt: skip
        if rid not in default_ids():
            named = ", ".join(i for i in default_ids() if i != CATCH_ALL)
            raise InvalidInputError(f"{where}: {rid} isn't one of ecf's default rules ({named})")
        if rid in {r.id for r in user.rules}:
            raise InvalidInputError(f"{where}: {rid} is also a rule in your file, which replaces"
                                    " ecf's default; keep one of the two")  # fmt: skip
    if user.off and catch_all(user):
        raise InvalidInputError(f"{where}: your last rule matches everything, so the default"
                                " rules don't run; remove default_rules_off or give the last"
                                " rule a `when`")  # fmt: skip


def layer_lines(text: str, schema: CompiledSchema, *, source: str = "rules") -> list[str]:
    """What the dry run and apply say about a file's layering (D14, P17): which case applies,
    each default it replaces (and where that runs), each default it turns off, and its rules
    that can hide mail."""
    user = _compile(text, schema, source=source, fixed=False, append=False)
    out: list[str] = []
    if catch_all(user):
        out.append("rules: your last rule matches everything, so ecf's default rules don't run"
                   " (your file replaces them; ecf's fixed rules still run first)")  # fmt: skip
    else:
        out.append("rules: your rules run after ecf's fixed rules and before ecf's default"
                   " rules")  # fmt: skip
        ids = default_ids()
        for r in user.rules:
            if r.id in ids:
                pos = ids.index(r.id) + 1
                out.append(f"rules: your rule {r.id} replaces ecf's default {r.id}, at its"
                           f" position ({pos} of {len(ids)} default rules); rename it to keep"
                           " both")  # fmt: skip
        out += [f"rules: ecf's default rule {rid} is off" for rid in user.off]
    hides = [r.id for r in user.rules if any(a.action in HIDE_ACTIONS for a in r.then)]
    if hides:
        out.append(f"rules: your rules that can hide mail (when ecf's checks allow it):"
                   f" {', '.join(hides)}")  # fmt: skip
    return out


def load_starter_rules(schema: CompiledSchema) -> CompiledRules:
    """The rules in force when none are applied: the fixed rules, then the default rules."""
    return compose(schema)


def fallback_rules(schema: CompiledSchema) -> CompiledRules:
    """The last resort when even the default rules don't compile against `schema` (P2, C11): the
    fixed rules and `otherwise`, so mail is still decided (fail closed: no default rule hides).
    The fixed rules compile against every schema ecf accepts (`check_safety_core`)."""
    fixed = load_fixed_rules(schema)
    return CompiledRules((*fixed.rules, Rule(id=CATCH_ALL, actor="continue")), schema)


def starter_digest() -> str:
    """SHA-256 of the shipped fixed and default rules files (`fixed_rules.yaml`, then
    `starter_rules.yaml`), recorded beside an eval result's keywords digest (pre-classifier plan
    step 7)."""
    h = hashlib.sha256()
    for name in (FIXED_FILE, DEFAULT_FILE):
        h.update(resources.files("ecf_server.data").joinpath(name).read_bytes())
    return h.hexdigest()


# Applied rules from v2: converting a stored v2 document by stripping ecf's fixed rules and
# the shipped default rules copied into it, or naming the customised ones that stop it.


@dataclass(frozen=True)
class Conversion:
    """`convert_v2`'s answer: the rules document to store (None: leave it as it is), the fixed
    rules it strips, and the customised fixed rules that stop it."""

    doc: dict[str, Any] | None
    stripped: tuple[str, ...] = ()
    customised: tuple[str, ...] = ()
    to_default: bool = False  # only fixed rules were left: the default rules apply (A7)


@functools.cache
def _v2_fixed() -> dict[str, str]:
    raw = cast(dict[str, Any], load_yaml(_data(V2_STARTER_FILE), source=V2_STARTER_FILE))
    return {str(r["id"]): _canonical(r) for r in cast(list[dict[str, Any]], raw["rules"])
            if is_fixed_id(r["id"])}  # fmt: skip


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def convert_v2(doc: Any) -> Conversion:
    """P7: an applied rules document from v2 without ecf's fixed rules. Each rule with a fixed
    id identical to the v2 starter text is stripped (the fixed rule, run first, does the same
    and more); the rest are kept in order. A rule with a fixed id that differs (customised) stops
    the conversion: the document is left as it is, and it doesn't compile (`compile_rules`
    refuses fixed ids), so the starter rules apply and a System Error names it. A fixed rule's id
    before a rename (`LEGACY_FIXED_IDS`) counts as fixed. A document of
    only unchanged fixed rules leaves nothing: `to_default` (the default rules apply). Idempotent:
    a document with no fixed id needs nothing."""
    if not isinstance(doc, dict):
        return Conversion(None)
    d = cast(dict[str, Any], doc)
    raw_rules: Any = d.get("rules")
    if not isinstance(raw_rules, list):
        return Conversion(None)
    listed = cast(list[Any], raw_rules)
    v2 = _v2_fixed()
    stripped: list[str] = []
    customised: list[str] = []
    kept: list[Any] = []
    for r in listed:
        rid = cast(dict[str, Any], r).get("id") if isinstance(r, dict) else None
        if not is_fixed_id(rid):
            kept.append(r)
        elif v2.get(str(rid)) == _canonical(r):
            stripped.append(str(rid))
        else:
            customised.append(str(rid))
    if customised:
        return Conversion(None, (), tuple(customised))
    if not stripped:  # nothing to do
        return Conversion(None)
    if not kept:  # only fixed rules: what is left is the default rules (`rules: default`)
        return Conversion(None, tuple(stripped), to_default=True)
    return Conversion(d | {"rules": kept}, tuple(stripped))


@functools.cache
def _shipped_defaults() -> dict[str, str]:
    raw = cast(dict[str, Any], load_yaml(_data(DEFAULT_FILE), source=DEFAULT_FILE))
    return {str(r["id"]): _canonical(r) for r in cast(list[dict[str, Any]], raw["rules"])}


def strip_defaults(
    doc: dict[str, Any], schema: CompiledSchema
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """P24 (S4): a converted v2 document (`convert_v2`'s, fixed copies already stripped) without
    its copies of ecf's default rules identical to the shipped text, so it doesn't pin every
    default by id. Only when the composed set is unchanged by it (a v2 file ending in its own
    catch-all is the replace case, where dropping a copy would drop the rule); else nothing is
    stripped. Returns the document (None when nothing is left: the default rules apply) and the
    stripped ids."""
    listed = cast(list[Any], doc.get("rules") or [])
    shipped = _shipped_defaults()

    def same(r: Any) -> bool:
        rid = cast(dict[str, Any], r).get("id") if isinstance(r, dict) else None
        return isinstance(rid, str) and shipped.get(rid) == _canonical(r)

    keep = [r for r in listed if not same(r)]
    gone = tuple(str(cast(dict[str, Any], r)["id"]) for r in listed if same(r))
    if not gone:
        return doc, ()
    out = doc | {"rules": keep} if keep else None
    try:
        before = compose(schema, _canonical(doc), source="applied rules").rules
        after = (compose(schema, _canonical(out), source="applied rules") if out is not None
                 else compose(schema)).rules  # fmt: skip
    except InvalidInputError:
        return doc, ()
    return (out, gone) if before == after else (doc, ())
