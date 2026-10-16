"""What `ecf upgrade` checks before it stops anything (SPEC §11.10; OD-374 to OD-382; V1.5 step
11a). Client code: it reads the new wheel as a zip and asks the service for this install's state;
it imports nothing from the service package.

- **The wheel** (OD-375): `ecf_server/data/release.json` (product, version, `api_version`,
  `data_format`, `schema_version`, `min_client`) and its Claude pins (`models.lock`), read
  without installing it. A wheel's `ollama.lock` (the local model's pin before v3.0.0) is read
  only when there: its digest is the "local" pin of a release before v3.0.0. From v3.0.0 it is a
  stub `{"retired": true}` through v3.0.x (R-D9), which has no digest: a v2.2.x client, which
  requires the file, reads it and names a "local" pin change for its A and B addresses.
- **The install** (OD-380): ecf must run from a `uv tool` environment (its `uv-receipt.toml`
  names this package); an editable or other install is refused with instructions.
- **Compatibility**: the same product; a newer version (an older one is `--to`, step 11c); a
  schema at least this install's; a data format this one or the next; this CLI at least
  `min_client`.
- **Model pins** (OD-381): families whose pin changes, and the addresses that use them, which
  drop to `assist` when the upgrade completes. From v3.0.0 step 4 (S4-D12, P18) the wheel's
  `jev.lock` is the "jev" pin and the canonical digest of its `jev_mapping.json` (step 3's, when
  the wheel ships one) the "jev_mapping" pin: either changed moves preset D's gate key, so the D
  addresses (the service's `pin_users`) drop to assist.
- **Prompt** (plan 0.3, Q6): `release.json`'s `prompt_digest`, the shipped classifier prompt's
  digest (shipped schema). It was in the local gate key, so a different one, or one either side
  lacks (before v2.1.0), counts as "prompt" and every address is affected. From v3.0.0 it is
  Claude's (`claude_review.prompt_digest`, ADR 0031, P4), which C's gate key doesn't hold either
  way; after an upgrade from before 3.0 its change is expected and affects no address
  (`after_upgrade`). A v2.2.x client's pre-check still says every address drops (it can't be
  patched; the release notes say so).
- **Actor's instructions** (v3.0.1, OD-523): `release.json`'s `actor_prompt_digest`, the shipped
  `agents/actor.md`'s digest, is in C's and D's gate key. Compared like the prompt, as
  "actor_prompt": different, or missing on either side (a release before 3.0.1), affects every
  address. After an upgrade from before 3.0 its arrival is expected and affects none, as for the
  prompt (P5); from 3.0.0 it counts (a 3.0.0 CLI's pre-check doesn't see it; phase 2 names them).
- **Converted addresses** (v3.0.0, P3): the new service's `converted` (A and B addresses made C
  and paused by migration 0038, with their waiting emails), printed after the upgrade.
- **Rules with a fixed id** (v3.0.0 step 5, OD-508): after the upgrade, the new CLI names each
  applied rule that stopped the new service's v2 conversion (a rule of your own named `phishing`,
  or a changed copy of a fixed rule) and what to do; the old CLI's pre-check can't (phase 1 runs
  the installed version's code).
- **Your own fields** (v3.0.0 S4, P11): stored but not yet in use, they reach the classifier at
  the new version's first start (announced then); the C addresses that drop to assist with them
  (`extension_pending` in the state) are listed. A service from before S4 doesn't report it.
"""

from __future__ import annotations

import hashlib
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
CLAUDE_PROMPT_MAJOR = 3  # release.json's prompt_digest is Claude's from v3.0.0 (P4)


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
    # Claude family -> model ID; "local" -> the Ollama digest before v3.0.0; "jev" -> the Jev pin
    # and "jev_mapping" -> its mapping's digest (v3.0.0 step 4)
    pins: dict[str, str]

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
            names = set(z.namelist())
            local: Any = (json.loads(z.read(DATA + "ollama.lock"))
                          if DATA + "ollama.lock" in names else {})  # fmt: skip
            jev: Any = (json.loads(z.read(DATA + "jev.lock"))
                        if DATA + "jev.lock" in names else {})  # fmt: skip
            mapping: Any = (json.loads(z.read(DATA + "jev_mapping.json"))
                            if DATA + "jev_mapping.json" in names else None)  # fmt: skip
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise InvalidInputError(f"{path.name} isn't an ecf wheel with release information"
                                f" ({type(exc).__name__})") from None  # fmt: skip
    if not isinstance(info, dict) or cast("dict[str, Any]", info).get("product") != PRODUCT:
        raise InvalidInputError(f"{path.name} isn't an {PRODUCT} wheel")
    pins = {f: str(cast("dict[str, Any]", claude).get(f)) for f in CLAUDE_FAMILIES}
    digest = cast("dict[str, Any]", local).get("digest") if isinstance(local, dict) else None
    if isinstance(digest, str):
        pins["local"] = digest
    model = cast("dict[str, Any]", jev).get("model") if isinstance(jev, dict) else None
    if isinstance(model, str):
        pins["jev"] = model
    if mapping is not None:
        pins["jev_mapping"] = mapping_digest(mapping)
    return Release(path, cast("dict[str, Any]", info), pins)


NOUL_TRUE = 0.5  # `ecf_server.systemone.NOUL_TRUE`, in the mapping's digest


def mapping_digest(doc: Any) -> str:
    """A `jev_mapping.json`'s digest as the service computes it (`ecf_server.jev_mapping.digest`
    over the parsed mapping: numbers as floats, tables sorted, the fixed yes threshold and tie
    rule), so formatting or the comment never moves it. The new wheel's service checks the file;
    a table that isn't an object reads as empty here."""
    d: dict[str, Any] = cast("dict[str, Any]", doc) if isinstance(doc, dict) else {}

    def table(key: str) -> dict[str, float]:
        raw: Any = d.get(key) or {}
        t = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        return {
            k: float(v)
            for k, v in sorted(t.items())
            if isinstance(v, int | float) and not isinstance(v, bool)
        }  # as `_number`

    canon = {"noul_true": NOUL_TRUE, "score_tie": "higher", "bands": table("bands"),
             "bars": table("bars"), "none_bars": table("none_bars")}  # fmt: skip
    raw = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    # families, "schema", "prompt", "actor_prompt"
    pin_changes: list[str] = field(default_factory=list[str])
    affected: list[str] = field(default_factory=list[str])  # addresses that drop to assist
    # C addresses your stored, not yet used fields move to assist at the first start (S4, P11)
    schema_pending: list[str] = field(default_factory=list[str])
    # A and B addresses the new version made C and paused: {address_id, from, waiting} (P3)
    converted: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # the applied rules' rules with a fixed id the new version left: {id, renamed_to,
    # renamed_from} (OD-508)
    rules_customised: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


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
    r.pin_changes, r.affected = changes(
        now["pins"], int(now.get("classifier_schema", 1)), rel.pins,
        int(new.get("classifier_schema", 1)), now["pin_users"],
        prompts=(now.get("prompt_digest"), new.get("prompt_digest")),
        actor_prompts=(now.get("actor_prompt_digest"), new.get("actor_prompt_digest")))  # fmt: skip
    pending: list[str] = now.get("extension_pending") or []
    r.schema_pending = sorted(str(a) for a in pending)
    r.affected = sorted(set(r.affected) | set(r.schema_pending))
    return r


def changes(old_pins: dict[str, str], old_schema: int | None, new_pins: dict[str, str],
            new_schema: int | None, uses: dict[str, list[str]], *,
            prompts: tuple[str | None, str | None] | None = None,
            actor_prompts: tuple[str | None, str | None] | None = None
            ) -> tuple[list[str], list[str]]:  # fmt: skip
    """(families whose pin changes, plus "schema", "prompt" and "actor_prompt"; the addresses
    that drop to assist). `uses` is family -> addresses. A new classifier schema asks every
    address differently (OD-475); a schema of None is unknown and counts as unchanged. `prompts`
    is the (old, new) prompt digest: different, or missing on either side, is "prompt" (Q6);
    `actor_prompts` the same for the actor's instructions, as "actor_prompt" (OD-523)."""
    fams = sorted(f for f, v in new_pins.items() if old_pins.get(f) != v)
    affected = sorted({a for f in fams for a in uses.get(f, [])})
    everyone = sorted({a for addrs in uses.values() for a in addrs})
    if old_schema is not None and new_schema is not None and old_schema != new_schema:
        fams.append("schema")
        affected = everyone
    if prompts is not None and (None in prompts or prompts[0] != prompts[1]):
        fams.append("prompt")
        affected = everyone
    if actor_prompts is not None and (None in actor_prompts
                                      or actor_prompts[0] != actor_prompts[1]):  # fmt: skip
        fams.append("actor_prompt")
        affected = everyone
    return fams, affected


def crosses_to_claude_prompt(old_version: str, state: dict[str, Any]) -> bool:
    """An upgrade from before v3.0.0 to v3.0.0 or later: release.json's `prompt_digest` went from
    the local model's to Claude's (P4), an expected change that moves no address."""
    try:
        new = version_key(str(state.get("version")))[0]
    except InvalidInputError:
        return False
    return version_key(old_version)[0] < CLAUDE_PROMPT_MAJOR <= new


def after_upgrade(old_version: str, old_wheel: Path, state: dict[str, Any]) -> Report:
    """Phase 2 of `ecf upgrade` (the new CLI): the pins and schema that changed, by this
    version's rules, between the kept old wheel and the new service's `GET /v1/upgrade/state`.
    The old CLI's pre-check can't see a change it doesn't know of (v1.0.0 doesn't know
    `classifier_schema`), so the new CLI names the affected addresses after the upgrade (SPEC
    §11.10; operator decision 2026-10-09). If the old wheel can't be read, only the schema is
    compared: a 1.x release has no `classifier_schema`, so it was 1. From before 3.0 the prompt
    digest was the local model's, and v3.0.0's is Claude's (P4): the change is expected and isn't
    compared, nor is the actor's instructions' digest, which arrived in v3.0.1 (P5). The
    addresses the new version converted to C (`converted`, P3) are listed."""
    try:
        old = read_wheel(old_wheel)
        old_pins = old.pins
        old_schema: int | None = int(old.info.get("classifier_schema", 1))
        old_prompt = old.info.get("prompt_digest")
        old_actor = old.info.get("actor_prompt_digest")
    except InvalidInputError:
        old_pins = dict[str, str](state["pins"])
        old_schema = 1 if version_key(old_version)[0] < 2 else None
        old_prompt = old_actor = None  # unknown: counts as a new prompt
    r = Report()
    crossing = crosses_to_claude_prompt(old_version, state)
    prompts = None if crossing else (old_prompt, state.get("prompt_digest"))
    actors = None if crossing else (old_actor, state.get("actor_prompt_digest"))
    r.pin_changes, r.affected = changes(old_pins, old_schema, state["pins"],
                                        int(state.get("classifier_schema", 1)),
                                        state["pin_users"], prompts=prompts,
                                        actor_prompts=actors)  # fmt: skip
    listed: list[Any] = state.get("converted") or []
    r.converted = [cast("dict[str, Any]", c) for c in listed if isinstance(c, dict)]
    listed = state.get("rules_customised") or []  # a service before step 5 has none
    r.rules_customised = [cast("dict[str, Any]", c) for c in listed if isinstance(c, dict)]
    return r
