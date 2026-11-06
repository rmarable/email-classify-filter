"""The eval/private move (planning-docs/EVAL-PRIVATE.md, Step 5) and the guard that follows it.

Which cards move: every card X1's markers mark (`ecf.eval.markers.is_injection_card`) in the
moving sets, except the pinned `starter-injection` (V3). The list is worked out from the cards
and pinned by its count per set and its sha256, so no moved ID is written in a public file
(Step 5's after-check); a list that differs from the pin is refused.

    --list                 print the list, one `<set>/<id>` per line (local use only)
    --check                the list matches the pin
    --apply                move the listed cards, their `.eml` and `.build/` files, rows and
                           `checks.yaml` keys to `<top>/eval/private/<set>/`; checks the split
                           (merged label bytes equal the old files, `checks.yaml` parses equal)
    --public-check         refuse a public card, file, row or key whose ID was moved (by the
                           hashed list `tests/eval/moved-ids.sha256`, or the `d-inj-`/`h3-inj-`
                           prefixes); CI runs it from the move commit on
    --dry-run              what --apply would move, checked, with nothing written
    --write-hashes         write `tests/eval/moved-ids.sha256` from the list (plus `long-093`)
    --top DIR              the checkout (default: this script's)

Run with `uv run --no-sync python scripts/move_private_cards.py ...` from the repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval import markers
from ecf.eval.merged import CHECKS, FIXED, LABELS, REVIEWS, merge_sources
from ecf.eval.setio import atomic_write
from ecf.yamlio import load_yaml

TOP = Path(__file__).resolve().parents[1]
SETS = ("synthetic", "drafts", "heldout3")
PIN_COUNTS = {"synthetic": 49, "drafts": 54, "heldout3": 4}
PIN_SHA256 = "8a76c4c58a85b38426de83042f2779173cc8f95ceb1d49e9eed85830a6a3a399"
HASHES = "tests/eval/moved-ids.sha256"
# IDs guarded before their card exists (OD-632's planned trigger-10 card, written in private)
LATER = ("long-093",)
PREFIXES = ("d-inj-", "h3-inj-")
KEY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]*):")


def _public(top: Path, name: str) -> Path:
    return top / "tests" / "eval" / name


def _private(top: Path, name: str) -> Path:
    return top / "eval" / "private" / name


def move_list(top: Path, sets: tuple[str, ...] = SETS) -> dict[str, list[str]]:
    """Each set's marked cards, the pinned starter left out, in ID order."""
    out: dict[str, list[str]] = {}
    for name in sets:
        ids: list[str] = []
        for p in sorted((_public(top, name) / "cases").glob("*.md")):
            data = p.read_bytes()
            if markers.is_pinned_starter(p.stem, data):
                continue
            if markers.is_injection_card(data.decode("utf-8"), source=str(p)):
                ids.append(p.stem)
        out[name] = ids
    return out


def digest(listed: dict[str, list[str]]) -> str:
    lines = sorted(f"{s}/{i}" for s, ids in listed.items() for i in ids)
    return hashlib.sha256("".join(f"{x}\n" for x in lines).encode()).hexdigest()


def check_pin(listed: dict[str, list[str]], counts: dict[str, int] = PIN_COUNTS,
              sha: str = PIN_SHA256) -> list[str]:  # fmt: skip
    """What differs from the pin; empty when the list is the pinned one."""
    bad = [f"{s}: {len(ids)} cards to move, pinned {counts.get(s)}"
           for s, ids in listed.items() if len(ids) != counts.get(s)]  # fmt: skip
    if digest(listed) != sha:
        bad.append(f"the list's sha256 is {digest(listed)}, pinned {sha}")
    return bad


# The move. Label files hold one row per card; the rows keep their bytes. checks.yaml is split by
# its top-level keys; its leading comment lines stay public (the merged file keeps them).


def _rows(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(str(json.loads(x)["id"]), x) for x in lines if x.strip()]


def _blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
    """A checks.yaml's header and its top-level blocks, each `(key, text)`."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and not KEY.match(lines[i]):
        i += 1
    head, blocks = "".join(lines[:i]), list[tuple[str, str]]()
    for line in lines[i:]:
        m = KEY.match(line)
        if m:
            blocks.append((m.group(1), line))
        else:
            k, t = blocks[-1]
            blocks[-1] = (k, t + line)
    return head, blocks


def _parsed(text: str, source: str) -> dict[str, Any]:
    data: object = load_yaml(text, source=source) or {}
    if not isinstance(data, dict):
        raise InvalidInputError(f"{source}: must be a mapping of card IDs")
    return dict(data)  # pyright: ignore[reportUnknownArgumentType]


@dataclass
class Moved:
    cards: int = 0
    files: list[str] = field(default_factory=list[str])
    rows: dict[str, int] = field(default_factory=dict[str, int])


def plan_move(top: Path, name: str, ids: list[str]) -> Moved:
    """What `apply` would move for one set, checked before anything is written: every card is
    public, nothing it moves is in the private half already (no card, file, row or key)."""
    pub, priv = _public(top, name), _private(top, name)
    want = set(ids)
    missing = sorted(i for i in want if not (pub / "cases" / f"{i}.md").is_file())
    if missing:
        raise InvalidInputError(f"{name}: no public card for {', '.join(missing)}")
    out = Moved(cards=len(ids))
    for d in ("cases", "eml", ".build"):
        src = pub / d
        if src.is_dir():
            out.files += [f"{d}/{q.name}" for q in sorted(src.iterdir())
                          if q.name.split(".", 1)[0] in want]  # fmt: skip
    there = sorted(f for f in out.files if (priv / f).exists())
    there += sorted(f"{k}: {i}" for k in (LABELS, FIXED, REVIEWS)
                    for i, _ in _rows(priv / k) if i in want)  # fmt: skip
    if (priv / CHECKS).is_file():
        there += sorted(f"{CHECKS}: {k}" for k, _ in _blocks(
            (priv / CHECKS).read_text(encoding="utf-8"))[1] if k in want)  # fmt: skip
    if there:
        raise InvalidInputError(f"{name}: already in the private set: {', '.join(there[:10])}")
    for k in (LABELS, FIXED, REVIEWS):
        if n := sum(1 for i, _ in _rows(pub / k) if i in want):
            out.rows[k] = n
    if (pub / CHECKS).is_file():
        _head, blocks = _blocks((pub / CHECKS).read_text(encoding="utf-8"))
        if n := sum(1 for k, _ in blocks if k in want):
            out.rows[CHECKS] = n
    return out


def apply(top: Path, name: str, ids: list[str]) -> Moved:
    """Move one set's listed cards to its private half (after `plan_move`'s checks) and check
    the split."""
    plan_move(top, name, ids)
    pub, priv = _public(top, name), _private(top, name)
    want = set(ids)
    before = {k: (pub / k).read_bytes() for k in (LABELS, FIXED) if (pub / k).is_file()}
    checks_before = (pub / CHECKS).read_text(encoding="utf-8") if (pub / CHECKS).is_file() else ""
    out = Moved(cards=len(ids))
    for kind in (LABELS, FIXED, REVIEWS):
        n = _move_rows(pub, priv, kind, want)
        if n:
            out.rows[kind] = n
    if checks_before:
        n = _move_checks(pub, priv, checks_before, want)
        if n:
            out.rows[CHECKS] = n
    out.files = _move_files(pub, priv, want)
    _check_split(pub, priv, before, checks_before)
    return out


def _move_rows(pub: Path, priv: Path, kind: str, want: set[str]) -> int:
    rows = _rows(pub / kind)
    move = [(i, x) for i, x in rows if i in want]
    if not move:
        return 0
    old = _rows(priv / kind)
    # reviews.jsonl is append-only history: the moved rows after any it had; the rest in ID order
    new = [x for _, x in (old + move if kind == REVIEWS else sorted(old + move))]
    priv.mkdir(parents=True, exist_ok=True)
    atomic_write(priv / kind, "".join(f"{x}\n" for x in new))
    atomic_write(pub / kind, "".join(f"{x}\n" for i, x in rows if i not in want))
    return len(move)


def _move_checks(pub: Path, priv: Path, text: str, want: set[str]) -> int:
    head, blocks = _blocks(text)
    move = [t for k, t in blocks if k in want]
    if not move:
        return 0
    old = (priv / CHECKS).read_text(encoding="utf-8") if (priv / CHECKS).is_file() else ""
    priv.mkdir(parents=True, exist_ok=True)
    atomic_write(priv / CHECKS, old + "".join(move))
    atomic_write(pub / CHECKS, head + "".join(t for k, t in blocks if k not in want))
    return len(move)


def _move_files(pub: Path, priv: Path, want: set[str]) -> list[str]:
    moved: list[str] = []
    for d in ("cases", "eml", ".build"):
        src = pub / d
        if not src.is_dir():
            continue
        for p in sorted(src.iterdir()):
            if p.name.split(".", 1)[0] in want:
                (priv / d).mkdir(parents=True, exist_ok=True)
                shutil.move(p, priv / d / p.name)
                moved.append(f"{d}/{p.name}")
    return moved


def _check_split(pub: Path, priv: Path, before: dict[str, bytes], checks_before: str) -> None:
    got = merge_sources(pub, priv, (LABELS, FIXED, CHECKS))
    for kind in (LABELS, FIXED):
        if got.get(kind) != before.get(kind):
            raise InvalidInputError(f"{pub.name} {kind}: the merged bytes differ from the old file")
    if checks_before:
        merged = got.get(CHECKS) or b""
        if _parsed(merged.decode(), "merged checks.yaml") != _parsed(checks_before, "checks.yaml"):
            raise InvalidInputError(f"{pub.name} {CHECKS}: the merged file parses differently")


# The guard after the move: no public trace of a moved ID.


def _h(card_id: str) -> str:
    return hashlib.sha256(card_id.encode()).hexdigest()


def hashes(top: Path) -> set[str]:
    p = top / HASHES
    return set(p.read_text().split()) if p.is_file() else set()


def write_hashes(top: Path, listed: dict[str, list[str]]) -> int:
    lines = sorted({_h(i) for ids in listed.values() for i in ids} | {_h(i) for i in LATER})
    atomic_write(top / HASHES, "".join(f"{x}\n" for x in lines))
    return len(lines)


def public_check(top: Path, known: set[str] | None = None) -> list[str]:
    """Every public card, file, label row or `checks.yaml` key whose ID is moved."""
    known = hashes(top) if known is None else known

    def moved(card_id: str) -> bool:
        return card_id.startswith(PREFIXES) or _h(card_id) in known

    found: list[str] = []
    for root in sorted(p for p in (top / "tests" / "eval").iterdir() if p.is_dir()):
        where = root.relative_to(top)
        for d in ("cases", "eml", ".build"):
            if (root / d).is_dir():
                found += [f"{where}/{d}/{p.name}" for p in sorted((root / d).iterdir())
                          if moved(p.name.split(".", 1)[0])]  # fmt: skip
        for kind in (LABELS, FIXED, REVIEWS):
            found += [f"{where}/{kind}: a row for {i}" for i, _ in _rows(root / kind) if moved(i)]
        if (root / CHECKS).is_file():
            _head, blocks = _blocks((root / CHECKS).read_text(encoding="utf-8"))
            found += [f"{where}/{CHECKS}: a key for {k}" for k, _ in blocks if moved(k)]
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The eval/private move and its guard.")
    mode = ap.add_mutually_exclusive_group(required=True)
    for flag in ("--list", "--check", "--apply", "--dry-run", "--public-check", "--write-hashes"):
        mode.add_argument(flag, action="store_true")
    ap.add_argument("--top", type=Path, default=TOP)
    a = ap.parse_args(argv)
    top: Path = a.top.resolve()
    if a.public_check:
        found = public_check(top)
        for x in found:
            print(f"refused: {x}")
        return 1 if found else 0
    listed = move_list(top)
    if a.list:
        for s, ids in listed.items():
            for i in ids:
                print(f"{s}/{i}")
        return 0
    bad = check_pin(listed)
    for x in bad:
        print(f"refused: {x}")
    if bad:
        return 1
    if a.check:
        print(f"the list matches the pin: {sum(len(v) for v in listed.values())} cards")
        return 0
    if a.write_hashes:
        print(f"wrote {write_hashes(top, listed)} hashes to {HASHES}")
        return 0
    return _move(top, listed, dry_run=a.dry_run)


def _move(top: Path, listed: dict[str, list[str]], *, dry_run: bool) -> int:
    plans = {s: plan_move(top, s, ids) for s, ids in listed.items()}  # every set, before writing
    if dry_run:
        for s, m in plans.items():
            print(f"{s}: would move {m.cards} cards, {len(m.files)} files, rows {m.rows}")
            for f in m.files:
                print(f"  {f}")
        return 0
    for s, ids in listed.items():
        m = apply(top, s, ids)
        print(f"{s}: moved {m.cards} cards, {len(m.files)} files, rows {m.rows}")
    print("now `ecf eval build --root tests/eval/<set>` for each set, then commit both. To undo"
          " before committing: `git checkout -- tests/eval && git clean -fd tests/eval` here and"
          " `rm -rf` the new files in eval/private")  # fmt: skip
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InvalidInputError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
