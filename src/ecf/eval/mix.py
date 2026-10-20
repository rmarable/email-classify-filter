"""The mix for `ecf eval generate` (eval generate plan, decision 2, P10, P16).

The shipped file (`ecf/data/eval_mix.yaml`) holds the published lists: FBI IC3 crime types with
their definitions, MITRE delivery methods, Gmail's inbox categories, and the operator's default
mix. A user's mix file holds ids and shares only; any other text is refused, so nothing a user
writes reaches a prompt. Shares become counts by largest-remainder rounding, deterministically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.yamlio import load_yaml

SHIPPED = "eval_mix.yaml"
ID = re.compile(r"^[a-z0-9-]{1,40}$")
MIX_KEYS = ("ordinary_share", "groups", "scam", "ordinary", "delivery")


@dataclass(frozen=True)
class Entry:
    """One published item: an IC3 crime type, a delivery method or an inbox category."""

    id: str
    name: str
    source: str
    definition: str = ""  # IC3's Appendix B text; empty for the others
    technique: str = ""  # MITRE's sub-technique id
    complaints: int = 0
    kept: bool = True


@dataclass(frozen=True)
class Published:
    sources: dict[str, dict[str, str]]
    keep_rule: str
    ic3: dict[str, Entry]
    delivery: dict[str, Entry]
    ordinary: dict[str, Entry]
    default: dict[str, Any]


@dataclass(frozen=True)
class Mix:
    ordinary_share: float
    groups: dict[str, tuple[str, ...]]  # group -> IC3 type ids
    scam: dict[str, float]  # group -> share
    ordinary: dict[str, float]  # inbox category -> share
    delivery: dict[str, float]  # method -> share

    def as_dict(self) -> dict[str, Any]:
        return {"ordinary_share": self.ordinary_share,
                "groups": {g: list(t) for g, t in self.groups.items()},
                "scam": dict(self.scam), "ordinary": dict(self.ordinary),
                "delivery": dict(self.delivery)}  # fmt: skip


@dataclass(frozen=True)
class Slot:
    """One email to generate: scam (a group and a delivery method) or ordinary (a category)."""

    kind: str  # "scam" or "ordinary"
    group: str  # a scam group or an inbox category
    delivery: str | None  # scam only


def _text(raw: str) -> str:
    return resources.files("ecf.data").joinpath(raw).read_text(encoding="utf-8")


@cache
def published() -> Published:
    doc = cast(dict[str, Any], load_yaml(_text(SHIPPED), source=SHIPPED))

    def entries(key: str) -> dict[str, Entry]:
        rows = cast(list[dict[str, Any]], doc[key])
        return {r["id"]: Entry(id=r["id"], name=r["name"], source=r.get("source", "ic3"),
                               definition=" ".join(str(r.get("definition", "")).split()),
                               technique=r.get("technique", ""),
                               complaints=int(r.get("complaints", 0)),
                               kept=bool(r.get("kept", True)))
                for r in rows}  # fmt: skip

    return Published(sources=doc["sources"], keep_rule=" ".join(doc["keep_rule"].split()),
                     ic3=entries("ic3_types"), delivery=entries("delivery"),
                     ordinary=entries("ordinary"), default=doc["default_mix"])  # fmt: skip


def _shares(raw: object, what: str, allowed: dict[str, Entry] | None) -> dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        raise InvalidInputError(f"mix: {what} must map ids to shares")
    out: dict[str, float] = {}
    for k, v in cast(dict[object, object], raw).items():
        if not isinstance(k, str) or not ID.fullmatch(k):
            raise InvalidInputError(f"mix: {what}: {k!r} isn't an id (a-z, 0-9, -, up to 40)")
        if allowed is not None and k not in allowed:
            raise InvalidInputError(f"mix: {what}: {k!r} isn't one of {', '.join(allowed)}")
        if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
            raise InvalidInputError(f"mix: {what}: {k}'s share must be a number, 0 or more")
        out[k] = float(v)
    if not any(out.values()):
        raise InvalidInputError(f"mix: {what}: at least one share must be above 0")
    return out


def parse(doc: object, *, source: str = "mix") -> Mix:  # noqa: PLR0912 - one refusal a check
    """A mix from a user's file or the shipped default: ids and shares only (P16)."""
    pub = published()
    if not isinstance(doc, dict):
        raise InvalidInputError(f"{source}: must be a mapping of {', '.join(MIX_KEYS)}")
    d = cast(dict[str, object], doc)
    if extra := sorted(set(map(str, d)) - set(MIX_KEYS)):
        raise InvalidInputError(f"{source}: unknown keys {', '.join(extra)}; a mix holds only"
                                f" {', '.join(MIX_KEYS)}")  # fmt: skip
    if missing := [k for k in MIX_KEYS if k not in d]:
        raise InvalidInputError(f"{source}: missing {', '.join(missing)}")
    share = d["ordinary_share"]
    if isinstance(share, bool) or not isinstance(share, int | float) or not 0 <= share <= 1:
        raise InvalidInputError(f"{source}: ordinary_share must be a number from 0 to 1")
    raw_groups = d["groups"]
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise InvalidInputError(f"{source}: groups must map group ids to lists of IC3 type ids")
    groups: dict[str, tuple[str, ...]] = {}
    used: dict[str, str] = {}
    for g, types in cast(dict[object, object], raw_groups).items():
        if not isinstance(g, str) or not ID.fullmatch(g):
            raise InvalidInputError(f"{source}: groups: {g!r} isn't an id")
        if not isinstance(types, list) or not types:
            raise InvalidInputError(f"{source}: groups: {g} must list IC3 type ids")
        for t in cast(list[object], types):
            entry = pub.ic3.get(t) if isinstance(t, str) else None
            if entry is None:
                raise InvalidInputError(f"{source}: groups: {g}: {t!r} isn't an IC3 type id")
            if not entry.kept:
                raise InvalidInputError(f"{source}: groups: {g}: {t} is left out by the keep"
                                        " rule (not a message aimed at the"
                                        " recipient)")  # fmt: skip
            name = cast(str, t)
            if name in used:
                raise InvalidInputError(f"{source}: {name} is in both {used[name]} and {g}")
            used[name] = g
        groups[g] = tuple(cast(list[str], types))
    scam = _shares(d["scam"], "scam", None)
    if set(scam) != set(groups):
        raise InvalidInputError(f"{source}: scam must give a share for each group, and only"
                                " those")  # fmt: skip
    return Mix(ordinary_share=float(share), groups=groups,
               scam={g: scam[g] for g in groups},
               ordinary=_shares(d["ordinary"], "ordinary", pub.ordinary),
               delivery=_shares(d["delivery"], "delivery", pub.delivery))  # fmt: skip


def default() -> Mix:
    return parse(published().default, source=SHIPPED)


def load(path: Path) -> Mix:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidInputError(f"{path}: can't read it ({exc.strerror})") from None
    return parse(load_yaml(text, source=path.name), source=path.name)


def write_default(path: Path) -> None:
    """The shipped default mix as a file to edit (`ecf eval generate --write-mix`)."""
    if path.exists():
        raise InvalidInputError(f"{path} already exists")
    m = default().as_dict()
    pub = published()
    lines = ["# ecf eval generate mix: ids and shares only (names and definitions are ecf's",
             "# shipped published lists). Shares are relative; --count turns them into counts.",
             f"ordinary_share: {m['ordinary_share']}", "groups:"]  # fmt: skip
    lines += [f"  {g}: {json.dumps(t)}" for g, t in m["groups"].items()]
    lines.append("scam:")
    lines += [f"  {g}: {s:g}" for g, s in m["scam"].items()]
    lines.append(f"ordinary:  # {', '.join(e.name for e in pub.ordinary.values())}")
    lines += [f"  {c}: {s:g}" for c, s in m["ordinary"].items()]
    lines.append("delivery:")
    lines += [f"  {k}: {s:g}" for k, s in m["delivery"].items()]
    kept = [t.id for t in pub.ic3.values() if t.kept]
    lines.append(f"# IC3 type ids you can group: {', '.join(kept)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apportion(shares: dict[str, float], total: int, rotate: int = 0) -> dict[str, int]:
    """Largest-remainder rounding of `total` over `shares`, in their order. Ties go to the key
    first in the order rotated by `rotate` (so equal delivery shares rotate across groups)."""
    keys = list(shares)
    weight = sum(shares.values())
    quotas = {k: total * shares[k] / weight for k in keys}
    counts = {k: int(quotas[k]) for k in keys}
    left = total - sum(counts.values())
    order = sorted(keys, key=lambda k: (-(round(quotas[k] - counts[k], 9)),
                                        (keys.index(k) + rotate) % len(keys)))  # fmt: skip
    for k in order[:left]:
        counts[k] += 1
    return counts


def plan(m: Mix, count: int) -> list[Slot]:
    """The emails to generate for `count` kept: scam and ordinary by `ordinary_share`, then by
    group or category, and each scam group's emails by delivery method (rotating ties)."""
    if count < 1:
        raise InvalidInputError("--count must be 1 or more")
    split = apportion({"scam": 1 - m.ordinary_share, "ordinary": m.ordinary_share}, count)
    slots: list[Slot] = []
    for i, (g, n) in enumerate(apportion(m.scam, split["scam"]).items()):
        for method, k in apportion(m.delivery, n, rotate=i).items():
            slots += [Slot("scam", g, method)] * k
    for c, n in apportion(m.ordinary, split["ordinary"]).items():
        slots += [Slot("ordinary", c, None)] * n
    return slots
