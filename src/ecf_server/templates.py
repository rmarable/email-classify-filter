"""Reply templates (SPEC §8.7). Variables are only {sender_name} and {subject}, inserted as plain
text with CR/LF removed. Header encoding (RFC 2047) happens when the message is built (V1.5)."""

from __future__ import annotations

import string
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ecf.errors import InvalidInputError
from ecf.yamlio import load_yaml

VARIABLES = frozenset({"sender_name", "subject"})
_STRICT = ConfigDict(extra="forbid", frozen=True)


def _check_placeholders(text: str) -> str:
    for _, name, spec, conv in string.Formatter().parse(text):
        if name is None:
            continue
        if name not in VARIABLES or spec or conv:
            raise ValueError(f"only {{sender_name}} and {{subject}} are allowed, not {{{name}}}")
    return text


class Template(BaseModel):
    model_config = _STRICT
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    enabled: bool = False
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("subject", "body")
    @classmethod
    def _placeholders(cls, v: str) -> str:
        return _check_placeholders(v)


class TemplateSet(BaseModel):
    model_config = _STRICT
    version: Literal[1]
    templates: list[Template] = Field(default_factory=list[Template])


@dataclass(frozen=True)
class Rendered:
    subject: str
    body: str


def _clean(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def render(t: Template, *, sender_name: str, subject: str) -> Rendered:
    values = {"sender_name": _clean(sender_name), "subject": _clean(subject)}
    return Rendered(t.subject.format_map(values), t.body.format_map(values))


def load_templates(text: str, *, source: str = "templates") -> dict[str, Template]:
    try:
        ts = TemplateSet.model_validate(load_yaml(text, source=source))
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidInputError(
            f"{source}: {'.'.join(map(str, first['loc']))}: {first['msg']}"
        ) from exc
    ids = [t.id for t in ts.templates]
    if len(set(ids)) != len(ids):
        raise InvalidInputError(f"{source}: duplicate template ids")
    return {t.id: t for t in ts.templates}


def load_default_templates() -> dict[str, Template]:
    text = resources.files("ecf_server.data").joinpath("templates.yaml").read_text("utf-8")
    return load_templates(text, source="templates.yaml")
