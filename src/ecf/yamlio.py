"""YAML loading for all ecf config (SPEC §7.4, §17.2): ruamel.yaml safe loader, YAML 1.2 rules,
duplicate keys rejected. Import bundles use the same loader."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ecf.errors import InvalidInputError


def _loader() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.allow_duplicate_keys = False
    return y


def load_yaml(text: str, *, source: str = "<string>") -> Any:
    try:
        load = cast(Callable[[str], Any], _loader().load)  # pyright: ignore[reportUnknownMemberType]
        return load(text)
    except YAMLError as exc:
        raise InvalidInputError(f"{source}: invalid YAML: {exc}".splitlines()[0]) from exc


def load_yaml_file(path: Path) -> Any:
    return load_yaml(path.read_text(encoding="utf-8"), source=str(path))
