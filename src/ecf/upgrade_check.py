"""What `ecf upgrade` checks before it stops anything (SPEC §11.10; OD-374 to OD-382; V1.5 step
11a). Client code: it reads the new wheel as a zip and asks the service for this install's state;
it imports nothing from the service package.

- **The wheel** (OD-375): `ecf_server/data/release.json` (product, version, `api_version`,
  `data_format`, `schema_version`, `min_client`) and its model pins (`models.lock`,
  `ollama.lock`), read without installing it.
- **The install** (OD-380): ecf must run from a `uv tool` environment (its `uv-receipt.toml`
  names this package); an editable or other install is refused with instructions.
- **Compatibility**: the same product; a newer version (an older one is `--to`, step 11c); a
  schema at least this install's; a data format this one or the next; this CLI at least
  `min_client`.
- **Model pins** (OD-381): families whose pin changes, and the addresses that use them, which
  drop to `assist` when the upgrade completes.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError

PRODUCT = "email-classify-filter"
DATA = "ecf_server/data/"
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(rc)(\d+)|\.dev(\d+))?$")
CLAUDE_FAMILIES = ("main_session", "classifier", "classifier_high", "actor", "actor_high")


def version_key(v: str) -> tuple[int, int, int, int, int]:
    """Orders ecf's versions: X.Y.Z.devN < X.Y.ZrcN < X.Y.Z."""
    m = _VERSION.match(v)
    if not m:
        raise InvalidInputError(f"not an ecf version: {v!r}")
    major, minor, patch = int(m[1]), int(m[2]), int(m[3])
    if m[6] is not None:
        return (major, minor, patch, 0, int(m[6]))
    if m[4]:
        return (major, minor, patch, 1, int(m[5]))
    return (major, minor, patch, 2, 0)


@dataclass(frozen=True)
class Release:
    wheel: Path
    info: dict[str, Any]
    pins: dict[str, str]  # Claude family -> model ID, and "local" -> the Ollama digest

    @property
    def version(self) -> str:
        return str(self.info["version"])


def read_wheel(path: Path) -> Release:
    if path.suffix != ".whl" or not path.is_file():
        raise InvalidInputError(f"{path} isn't a wheel file (.whl)")
    try:
        with zipfile.ZipFile(path) as z:
            info = json.loads(z.read(DATA + "release.json"))
            claude = json.loads(z.read(DATA + "models.lock"))
            local = json.loads(z.read(DATA + "ollama.lock"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise InvalidInputError(f"{path.name} isn't an ecf wheel with release information"
                                f" ({type(exc).__name__})") from None  # fmt: skip
    if not isinstance(info, dict) or cast("dict[str, Any]", info).get("product") != PRODUCT:
        raise InvalidInputError(f"{path.name} isn't an {PRODUCT} wheel")
    pins = {f: str(cast("dict[str, Any]", claude).get(f)) for f in CLAUDE_FAMILIES}
    pins["local"] = str(cast("dict[str, Any]", local).get("digest"))
    return Release(path, cast("dict[str, Any]", info), pins)


@dataclass(frozen=True)
class Install:
    """Where this ecf runs from (OD-380)."""

    prefix: Path
    receipt: dict[str, Any] | None

    @property
    def wheel(self) -> Path | None:
        """The wheel `uv tool install` used, if the receipt names one that still exists."""
        for req in (self.receipt or {}).get("tool", {}).get("requirements", []):
            if isinstance(req, dict) and cast("dict[str, Any]", req).get("name") == PRODUCT:
                p = cast("dict[str, Any]", req).get("path")
                if isinstance(p, str) and Path(p).is_file():
                    return Path(p)
        return None


def this_install(prefix: Path | None = None) -> Install:
    base = Path(prefix or sys.prefix)
    receipt_path = base / "uv-receipt.toml"
    receipt: dict[str, Any] | None = None
    if receipt_path.is_file():
        try:
            receipt = tomllib.loads(receipt_path.read_text())
        except tomllib.TOMLDecodeError:
            receipt = None
    return Install(base, receipt)


def install_problem(inst: Install) -> str | None:
    if inst.receipt is None:
        return ("ecf isn't installed with `uv tool` here (no uv-receipt.toml), so `ecf upgrade`"
                " can't replace it; install it with `uv tool install <wheel>`, or upgrade it the"
                " way you installed it")  # fmt: skip
    if PRODUCT not in _names(inst.receipt):
        return f"this uv tool environment isn't {PRODUCT}'s"
    if inst.wheel is None:
        return ("the wheel ecf was installed from is gone (uv-receipt.toml names it), so ecf"
                " couldn't roll back; put it back or reinstall from a wheel file")  # fmt: skip
    return None


def _names(receipt: dict[str, Any]) -> list[str]:
    reqs: Any = receipt.get("tool", {}).get("requirements", [])
    return [str(cast("dict[str, Any]", r).get("name")) for r in reqs if isinstance(r, dict)]


@dataclass
class Report:
    problems: list[str] = field(default_factory=list[str])
    pin_changes: list[str] = field(default_factory=list[str])  # families, and "local"
    affected: list[str] = field(default_factory=list[str])  # addresses that drop to assist


def compare(state: dict[str, Any], rel: Release, client_version: str) -> Report:
    """`state` is the service's `GET /v1/upgrade/state`."""
    r = Report()
    new, now = rel.info, state
    if version_key(rel.version) <= version_key(str(now["version"])):
        r.problems.append(f"{rel.version} isn't newer than {now['version']} (to go back, use"
                          " ecf upgrade --to <version>)")  # fmt: skip
    if int(new["schema_version"]) < int(now["schema_version"]):
        r.problems.append(f"its database schema ({new['schema_version']}) is older than this"
                          f" install's ({now['schema_version']})")  # fmt: skip
    if int(new["data_format"]) - int(now["data_format"]) not in (0, 1):
        r.problems.append(f"it uses data format {new['data_format']}; this install has"
                          f" {now['data_format']}")  # fmt: skip
    if version_key(client_version) < version_key(str(new["min_client"])):
        r.problems.append(f"it needs ecf {new['min_client']} or later to start the upgrade;"
                          " upgrade to that first")  # fmt: skip
    pins: dict[str, str] = now["pins"]
    r.pin_changes = sorted(f for f, v in rel.pins.items() if pins.get(f) != v)
    uses: dict[str, list[str]] = now["pin_users"]  # family -> addresses
    r.affected = sorted({a for f in r.pin_changes for a in uses.get(f, [])})
    return r
