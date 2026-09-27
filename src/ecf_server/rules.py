"""Rules (SPEC §8.6): structured YAML compiled to predicates. No eval.

A rule set is an ordered list; the first rule whose `when` matches decides. A condition is a
Pydantic discriminated union: a comparison on an operand (`field`, `fact`, `trigger`, `address`)
with `eq`, `in`, `gte` or `lte` (ordinals by level order), or `and` / `or` / `not`.
Rules may only emit non-sending actions; sends and drafts come from actor proposals (OD-170).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    model_validator,
)

from ecf.errors import InvalidInputError
from ecf.schema import CompiledSchema, FieldKind
from ecf.yamlio import load_yaml

FACTS: dict[str, tuple[str, ...] | None] = {  # None = boolean fact
    "sender_origin": ("internal", "external"),
    "auth_result": ("pass", "fail", "none"),
    "sender_seen_before": None,
    "reply_to_mismatch": None,
    "recipient_mismatch": None,
    "bulk_signal": None,
    "content_unscanned": None,
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


# ---------------------------------------------------------------------------- conditions


class Compare(BaseModel):
    model_config = _STRICT
    field: str | None = None
    fact: str | None = None
    trigger: str | None = None
    address: str | None = None
    eq: str | bool | None = None
    in_: list[str] | None = Field(default=None, alias="in")
    gte: str | None = None
    lte: str | None = None

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


# ---------------------------------------------------------------------------- rules


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


# ---------------------------------------------------------------------------- evaluation


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

    def evaluate(self, inp: RuleInput) -> Decision:
        for rule in self.rules:
            if rule.when is None or _eval(rule.when, inp, self.schema):
                actions = tuple(
                    Action(a.action, _target(a.target, inp))
                    for a in rule.then
                    if a.if_ is None or _eval(a.if_, inp, self.schema)
                )
                if isinstance(rule.actor, ContinueIf):
                    to_actor = _eval(rule.actor.continue_if, inp, self.schema)
                else:
                    to_actor = rule.actor == "continue"
                return Decision(rule.id, actions, to_actor, rule.hide)
        raise InvalidInputError("no rule matched; the last rule must match everything")


def _target(t: str | LabelFrom | None, inp: RuleInput) -> str | None:
    if isinstance(t, LabelFrom):
        return str(inp.classification[t.field])
    return t


def _eval(c: Compare | And | Or | Not, inp: RuleInput, schema: CompiledSchema) -> bool:
    if isinstance(c, And):
        return all(_eval(x, inp, schema) for x in c.and_)
    if isinstance(c, Or):
        return any(_eval(x, inp, schema) for x in c.or_)
    if isinstance(c, Not):
        return not _eval(c.not_, inp, schema)
    return _compare(c, inp, schema)


def _compare(c: Compare, inp: RuleInput, schema: CompiledSchema) -> bool:
    if c.trigger is not None:
        return c.trigger in inp.triggers
    if c.field is not None:
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
    rank = spec.rank(str(value))
    return rank >= spec.rank(c.gte) if c.gte is not None else rank <= spec.rank(cast(str, c.lte))


# ---------------------------------------------------------------------------- compile


def compile_rules(text: str, schema: CompiledSchema, *, source: str = "rules") -> CompiledRules:
    raw = load_yaml(text, source=source)
    try:
        rs = RuleSet.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise InvalidInputError(f"{source}: {where}: {first['msg']}") from exc
    ids = [r.id for r in rs.rules]
    if len(set(ids)) != len(ids):
        raise InvalidInputError(f"{source}: duplicate rule ids")
    for r in rs.rules:
        _check_rule(r, schema, source)
    if rs.rules[-1].when is not None:
        raise InvalidInputError(f"{source}: the last rule must have no `when` (a catch-all)")
    return CompiledRules(tuple(rs.rules), schema)


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


def load_starter_rules(schema: CompiledSchema) -> CompiledRules:
    text = resources.files("ecf_server.data").joinpath("starter_rules.yaml").read_text("utf-8")
    return compile_rules(text, schema, source="starter_rules.yaml")
