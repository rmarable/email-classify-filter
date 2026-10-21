"""The shipped generator and grader model list (`ecf/data/eval_models.json`; eval generate plan,
Model switches; P24) and the family check.

A model is accepted only when it is listed for its provider, role and mode; a model from a lab
the model filter bars is refused with the reason, and any other unlisted id is refused as
unknown. Family is the lab that made the model. In PROD the generator, the grader and ecf's
classifier come from three different families; the classifier's family is read from each eval
result, never assumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, cast

from ecf.errors import InvalidInputError

SHIPPED = "eval_models.json"
# the classifier's family from a result's `pair` (its first part; `claude_pins.PAIR`, Jev's)
CLASSIFIER_FAMILY = {"claude": "anthropic", "jev": "typesafe"}


@dataclass(frozen=True)
class Model:
    provider: str
    id: str
    family: str
    roles: tuple[str, ...]
    modes: tuple[str, ...]
    price: dict[str, Any] | None = None  # US$ per 1M tokens: input, output, source, fetched
    reasoning_effort: str | None = (
        None  # sent to the provider when set (Mistral's default unstated)
    )


@cache
def _doc() -> dict[str, Any]:
    raw = resources.files("ecf.data").joinpath(SHIPPED).read_text(encoding="utf-8")
    return cast(dict[str, Any], json.loads(raw))


def listed() -> list[Model]:
    return [Model(m["provider"], m["id"], m["family"], tuple(m["roles"]), tuple(m["modes"]),
                  m.get("price"), m.get("reasoning_effort"))
            for m in _doc()["models"]]  # fmt: skip


def excluded(provider: str, model: str) -> dict[str, str] | None:
    """The `excluded` entry that bars `model`, or None."""
    for e in cast(list[dict[str, str]], _doc()["excluded"]):
        if e["provider"] in {"*", provider} and model.lower().startswith(e["prefix"]):
            return e
    return None


def check(provider: str, model: str, role: str, mode: str) -> Model:
    """The listed entry for this provider, model, role and mode; refused otherwise."""
    if (bar := excluded(provider, model)) is not None:
        raise InvalidInputError(f"{provider}:{model} is made by {bar['lab']}, {bar['why']}:"
                                " ecf's model filter refuses it")  # fmt: skip
    found = [m for m in listed() if m.provider == provider and m.id == model]
    if not found:
        known = ", ".join(m.id for m in listed() if m.provider == provider and role in m.roles)
        raise InvalidInputError(f"{provider}:{model} isn't on ecf's model list"
                                f"{f' (for the {role}: {known})' if known else ''}")  # fmt: skip
    m = found[0]
    if role not in m.roles:
        raise InvalidInputError(f"{provider}:{model} isn't listed as a {role} (only"
                                f" {', '.join(m.roles)})")  # fmt: skip
    if mode not in m.modes:
        raise InvalidInputError(f"{provider}:{model} is listed for {', '.join(m.modes)} sets"
                                f" only, not {mode}")  # fmt: skip
    return m


def family(provider: str, model: str) -> str:
    """The family of a listed model; refused when it isn't listed."""
    for m in listed():
        if m.provider == provider and m.id == model:
            return m.family
    raise InvalidInputError(f"{provider}:{model} isn't on ecf's model list")


def classifier_family(pair: str) -> str | None:
    """The classifier's family from an eval result's `pair` (`claude/claude`, `jev/claude`), or
    None when ecf doesn't know it."""
    return CLASSIFIER_FAMILY.get(pair.split("/", 1)[0])


def check_not_self(generator_model: str, grader_model: str, mode: str) -> None:
    """A model never grades the set it generated (operator rule 2026-10-20, OD-543), in DEV and
    PROD; compared by model id alone, whoever hosts it."""
    if generator_model == grader_model:
        others = sorted(m.id for m in listed() if "grader" in m.roles and mode in m.modes
                        and m.id != generator_model)  # fmt: skip
        raise InvalidInputError(
            f"{grader_model} generated this set, and a model never grades its"
            f" own set: grade it with {', '.join(others) or 'another model'}"
        )


def check_roles(generator: tuple[str, str], grader: tuple[str, str], mode: str) -> None:
    """Never the same model; in PROD, not the same family either."""
    check_not_self(generator[1], grader[1], mode)
    if mode == "prod":
        check_pair(generator, grader)


def check_pair(generator: tuple[str, str], grader: tuple[str, str]) -> None:
    """PROD: the generator and the grader from different families."""
    a, b = family(*generator), family(*grader)
    if a == b:
        raise InvalidInputError(f"the generator ({':'.join(generator)}) and the grader"
                                f" ({':'.join(grader)}) are both {a}: a PROD set needs them from"
                                " different families")  # fmt: skip
