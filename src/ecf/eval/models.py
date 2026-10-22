"""The shipped generator and grader model list (`ecf/data/eval_models.json`; eval generate plan,
Model switches; P24) and the family check.

A model is accepted only when it is listed for its provider, role and mode; a model from a lab
the model filter bars is refused with the reason, and any other unlisted id is refused as
unknown. Family is the lab that made the model. In PROD a judge never shares a family with the
set's generator, its seed writer or ecf's classifier (Claude), and two judges differ in family
(`check_judge`, `check_pair`). The classifier's family is read from each eval result, never
assumed, except that in PROD a judge is never Claude, ecf's classifier (OD-547).
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


def check_not_self(writer_model: str, grader_model: str, mode: str) -> None:
    """A model never grades a set it wrote, the emails or their seeds (operator rule 2026-10-20,
    OD-543, OD-545), in DEV and PROD; compared by model id alone, whoever hosts it. The
    suggestions leave out Claude in PROD, where it can't judge (OD-547)."""
    if writer_model == grader_model:
        others = sorted(m.id for m in listed() if "grader" in m.roles and mode in m.modes
                        and m.id != writer_model
                        and not (mode == "prod" and m.family == "anthropic"))  # fmt: skip
        raise InvalidInputError(
            f"{grader_model} wrote this set or its seeds, and a model never grades its"
            f" own set: grade it with {', '.join(others) or 'another model'}"
        )


def check_pair(one: tuple[str, str], other: tuple[str, str]) -> None:
    """PROD: two judges from different families (`check_judge` covers each judge against the
    set's writers)."""
    a, b = family(*one), family(*other)
    if a == b:
        raise InvalidInputError(f"{':'.join(one)} and {':'.join(other)} are both {a}: a PROD set"
                                " needs its judges from different families")  # fmt: skip


def writers(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """A generated set's writers from its manifest: the generator, and the model that wrote its
    seeds when they came from a seed run (`seeds_from.generator`, "provider:model"; OD-545)."""
    g = cast(dict[str, Any], doc["generator"])
    out = [(str(g.get("provider")), str(g.get("model")))]
    if shared := cast(dict[str, Any] | None, doc.get("seeds_from")):
        provider, _, model = str(shared.get("generator")).partition(":")
        out.append((provider, model))
    return out


def check_judge(doc: dict[str, Any], judge: tuple[str, str]) -> None:
    """A grader or judge of this set: never one of its writers; in PROD never a writer's family
    and never Claude, the classifier's (OD-543, OD-547). The one check the CLI, grading and
    `ecf eval agree` all make (eval plan draft 24, G2)."""
    mode = str(doc.get("mode"))
    for w in writers(doc):
        check_not_self(w[1], judge[1], mode)
    if mode == "prod":
        mine = family(*judge)
        for role, w in zip(("generator", "seed writer"), writers(doc), strict=False):
            if family(*w) == mine:
                raise InvalidInputError(f"the {role} ({':'.join(w)}) and the judge"
                                        f" ({':'.join(judge)}) are both {mine}: a PROD set needs"
                                        " them from different families")  # fmt: skip
        if mine == "anthropic":
            raise InvalidInputError(f"{judge[0]}:{judge[1]} is Claude, the classifier's family: a"
                                    " judge can't be")  # fmt: skip
