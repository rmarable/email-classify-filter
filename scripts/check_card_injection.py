"""Refuse a public prompt-injection test card (planning-docs/EVAL-PRIVATE.md, X1, X5, X12).

A public card or `.eml` blob is refused when trigger 10 flags its built email
(`ecf_server.triggers.card_injection`) or any string in the card file (`text_injection`), or
when X1's front-matter markers mark a card that is new since the move. Exempt: the pinned
`starter-injection` (V3), and blobs on the known list (public already;
`tests/eval/known-public-injection-blobs.txt`) everywhere except main's tip. PDF attachments are
built as empty placeholders (`message.parse` reads no PDF text). Fails closed: a card that
doesn't parse or build is refused.

    --ref REF [--tip SHA] [--no-tip-build] --revs REVSPEC...   a pushed range (hook, workflow)
    --tree TREE [--no-skip]     a whole tree (the merge gate)
    --list-known REV            print the flagged blobs in REV's history
    --daily                     main's tip unskipped; other refs' new blobs

Run with `uv run --no-sync python scripts/check_card_injection.py ...` from the repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval import markers, merged
from ecf.eval.builder import build_bytes, scan_card
from ecf.eval.cards import Card, parse_card
from ecf.yamlio import load_yaml
from ecf_server.triggers import card_injection, text_injection

TOP = Path(__file__).resolve().parents[1]
LIST = "tests/eval/known-public-injection-blobs.txt"
# main's tip the branch sits on (X1): blobs in main's history here are old marked cards, not new
# ones; moved with the list if the branch is rebased
X1_ANCHOR = "8b26fd6ae4a5d74c650e2b6fc775809332655496"
STARTER_EML_SHA256 = "2cfe1cf857be1b02621ef36e22ca3d8eb358f4a058cd1e551b0b3e997cb7ed9f"
# What may sit under tests/eval/, a closed list (review round 2, H2, M6): a path is recognised by
# these exact forms or refused, so a card under another name, a subfolder or an odd extension
# can't pass unread. IDs are lower-case letters, digits and hyphens, as every card's is.
ID = r"[a-z0-9][a-z0-9-]*"
CARD = re.compile(rf"^tests/eval/[a-z0-9][a-z0-9_-]*/cases/{ID}\.md$")
EML = re.compile(rf"^tests/eval/[a-z0-9][a-z0-9_-]*/(eml|\.build)/{ID}\.eml$")
SET_FILES = re.compile(r"^tests/eval/[a-z0-9][a-z0-9_-]*/(labels\.jsonl|fixed_labels\.jsonl"
                       r"|reviews\.jsonl|checks\.yaml)$")  # fmt: skip
TOP_FILES = ("tests/eval/known-public-injection-blobs.txt", "tests/eval/moved-ids.sha256")
PRIVATE_CARD = re.compile(rf"^[a-z0-9][a-z0-9_-]*/cases/{ID}\.md$")  # in the private repository


def git(*args: str, stdin: str | None = None, top: Path = TOP) -> str:
    r = subprocess.run(["git", "-C", str(top), *args], input=stdin, capture_output=True,
                       text=True, encoding="utf-8", errors="surrogateescape",
                       check=False)  # fmt: skip
    if r.returncode != 0:
        raise SystemExit(f"card scan: git {args[0]} failed: {r.stderr.strip()}")
    return r.stdout


def cat(ids: list[str], top: Path = TOP) -> dict[str, bytes]:
    """Blob bytes through one `git cat-file --batch`."""
    if not ids:
        return {}
    r = subprocess.run(["git", "-C", str(top), "cat-file", "--batch"],
                       input="".join(i + "\n" for i in ids).encode(), capture_output=True,
                       check=False)  # fmt: skip
    if r.returncode != 0:
        raise SystemExit("card scan: git cat-file failed")
    out: dict[str, bytes] = {}
    buf, pos = r.stdout, 0
    for i in ids:
        nl = buf.index(b"\n", pos)
        head = buf[pos:nl].split()
        if len(head) < 3 or head[1] != b"blob":
            raise SystemExit(f"card scan: {i} is not a blob")
        size = int(head[2])
        out[i] = buf[nl + 1 : nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


def is_main(ref: str) -> bool:
    return ref == "refs/heads/main" or re.fullmatch(r"refs/remotes/[^/]+/main", ref) is not None


def wanted(path: str) -> bool:
    return CARD.match(path) is not None or EML.match(path) is not None


def under_eval(path: str) -> bool:
    """Under tests/eval/ in any letter case (a case-insensitive checkout reads `tests/Eval/`
    there): such a path must be one of the exact recognised forms or it is refused."""
    return path.lower().startswith("tests/eval/")


def unrecognised(path: str) -> bool:
    """A path under tests/eval/ that is none of the recognised kinds: refused."""
    return (under_eval(path) and not wanted(path) and SET_FILES.match(path) is None
            and path not in TOP_FILES)  # fmt: skip


def changed(revs: list[str], top: Path = TOP,
            want: Callable[[str], bool] = under_eval) -> list[tuple[str, str]]:  # fmt: skip
    """(blob, path) for every file (`want`, default: everything under tests/eval/) each commit
    in `revs` added or changed, merge commits included and deleted files skipped, through one
    batched `git diff-tree --stdin -z` (NUL-separated: git never quotes a path there)."""
    commits = git("rev-list", *revs, top=top)
    if not commits.strip():
        return []
    out = git("diff-tree", "--stdin", "-z", "-r", "-m", "--root", "--no-renames",
              "--diff-filter=d", stdin=commits, top=top)  # fmt: skip
    seen: dict[tuple[str, str], None] = {}
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok.startswith(":") and i + 1 < len(parts):  # ":<modes> <shas> <status>", then path
            blob, path = tok.split()[3], parts[i + 1]
            if want(path):
                seen[(blob, path)] = None
            i += 2
        else:  # a commit ID line, or the empty tail
            i += 1
    return list(seen)


def tree(treeish: str, top: Path = TOP) -> list[tuple[str, str]]:
    """Every blob under tests/eval/ in a tree, in any letter case (`ls-tree -z`: paths never
    quoted; no pathspec, which git matches case-sensitively, so `under_eval` picks; review
    round 4, B5)."""
    out = git("ls-tree", "-r", "-z", treeish, top=top)
    found: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        kind, blob = meta.split()[1:3]
        if kind == "blob" and under_eval(path):
            found.append((blob, path))
    return found


def orphans(found: list[tuple[str, str]]) -> list[str]:
    """`eml/` or `.build/` files with no card in their set, in one tree."""
    cards = {(p.split("/")[2], Path(p).stem) for _, p in found if CARD.match(p)}
    return sorted(
        p for _, p in found if EML.match(p) and (p.split("/")[2], Path(p).stem) not in cards
    )


def known(top: Path = TOP) -> set[str]:
    p = top / LIST
    if not p.is_file():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def body_of(text: str) -> str:
    """The card's body: everything after the front matter's closing `---` line."""
    lines = text.splitlines(keepends=True)
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), len(lines))
    return "".join(lines[end + 1 :])


def strings(node: Any) -> list[str]:
    """Every string in a card's front matter, keys included, at any level."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*strings(k), *strings(v))]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]  # pyright: ignore[reportUnknownVariableType]
    return []


def placeholders(card: Card) -> Card:
    """Generated PDFs become empty placeholders under the same name: `message.parse` extracts no
    PDF text, so the render buys nothing (EVAL-PRIVATE.md X5)."""
    atts = [a.model_copy(update={"generate": None, "content_type": "application/pdf",
                                 "text": None}) if a.generate == "invoice_pdf" else a
            for a in card.attachments]  # fmt: skip
    return card.model_copy(update={"attachments": atts})


@dataclass(frozen=True)
class Verdict:
    refused: list[str]
    warned: list[str]


def check(blob: str, path: str, data: bytes, *, x1: bool, lenient: bool = False) -> Verdict:
    """Why this blob is refused (trigger 10, X1, or not parsing/building). `lenient` (another ref
    in the daily run): a card main's code can't parse still gets X1 and the text scan on its raw
    front matter and body, and its build is only warned about."""
    if EML.match(path):
        if hashlib.sha256(data).hexdigest() == STARTER_EML_SHA256:
            return Verdict([], [])
        hits = card_injection(data)
        return Verdict([f"{path} ({blob[:12]}): trigger 10 flags its email: {', '.join(hits)}"]
                       if hits else [], [])  # fmt: skip
    stem = Path(path).stem
    if markers.is_pinned_starter(stem, data):
        return Verdict([], [])
    try:
        text = data.decode("utf-8")
        header = markers.front_matter(text, source=path)
    except Exception as exc:
        return Verdict([f"{path} ({blob[:12]}): doesn't parse ({type(exc).__name__})"], [])
    refused: list[str] = []
    if x1 and (why := markers.reasons(header)):
        refused.append(f"{path} ({blob[:12]}): marked as an injection card ({', '.join(why)})")
    for s in [*strings(header), body_of(text)]:
        if hits := text_injection(s):
            refused.append(f"{path} ({blob[:12]}): trigger 10 flags its text: {', '.join(hits)}")
            break
    try:
        card = parse_card(text, source=path)
        if not lenient:  # the hygiene scan, as `ecf eval build` runs it (fake data only)
            refused += [f"{path} ({blob[:12]}): hygiene: {f.where} {f.kind}"
                        for f in scan_card(card, Path(path).name)]  # fmt: skip
        hits = card_injection(build_bytes(placeholders(card)))
    except Exception as exc:
        msg = f"{path} ({blob[:12]}): doesn't build ({type(exc).__name__})"
        return Verdict(refused, [msg]) if lenient else Verdict([*refused, msg], [])
    if hits:
        refused.append(f"{path} ({blob[:12]}): trigger 10 flags its email: {', '.join(hits)}")
    return Verdict(refused, [])


def scan(items: list[tuple[str, str]], *, skip: set[str], x1_old: set[str] | None,
         lenient: bool = False, top: Path = TOP) -> Verdict:  # fmt: skip
    """Check each (blob, path) once. `skip`: blobs not to check; `x1_old`: blobs exempt from X1
    (None: X1 on every blob)."""
    items = list(dict.fromkeys(items))
    refused = [
        f"{p} ({b[:12]}): not a recognised file under tests/eval (a card, its email, a set"
        " file or a list): rename or remove it"
        for b, p in items
        if unrecognised(p)
    ]
    todo = [(b, p) for b, p in items if wanted(p) and b not in skip]
    data = cat(sorted({b for b, _ in todo}), top)
    warned: list[str] = []
    for b, p in todo:
        v = check(b, p, data[b], x1=x1_old is None or b not in x1_old, lenient=lenient)
        refused += v.refused
        warned += v.warned
    return Verdict(refused, warned)


def check_files(items: list[tuple[str, bytes]]) -> Verdict:
    """Files outside git (the sdist test): every card and email checked, nothing skipped, X1 on."""
    refused: list[str] = []
    warned: list[str] = []
    for path, data in items:
        if unrecognised(path):
            refused.append(f"{path}: not a recognised file under tests/eval")
        elif wanted(path):
            v = check(hashlib.sha1(data).hexdigest(), path, data, x1=True)  # noqa: S324 - a label
            refused += v.refused
            warned += v.warned
    return Verdict(refused, warned)


def anchor_history(top: Path = TOP) -> set[str]:
    r = subprocess.run(["git", "-C", str(top), "cat-file", "-e", f"{X1_ANCHOR}^{{commit}}"],
                       capture_output=True, check=False)  # fmt: skip
    return {b for b, _ in changed([X1_ANCHOR], top)} if r.returncode == 0 else set()


def exempt(top: Path = TOP) -> set[str]:
    """The listed blobs that are public already: on the list and in main's history at the anchor.
    The list is read from the tree being checked, so an entry is trusted only if main's history
    has the blob (a push can't exempt its own new card by listing it)."""
    return known(top) & anchor_history(top)


def run_range(ref: str, revs: list[str], tip: str | None, *, tip_build: bool,
              top: Path = TOP, published: frozenset[str] = frozenset(),
              old: set[str] | None = None) -> Verdict:  # fmt: skip
    """A pushed range: every changed blob (listed blobs skipped; X1 only on blobs new since the
    anchor), then the tip's path checks and, except in the hook, a build of the tip's other
    blobs: unskipped with X1 on main, with the list elsewhere."""
    items = changed(revs, top)
    old = anchor_history(top) if old is None else old  # the hook reads it once per push
    listed = known(top) & old  # `exempt`, with the history read once
    found = tree(tip, top) if tip else []
    # main's tip is checked whole, X1 on, nothing skipped: a marked card restored onto main (a
    # revert of the move) is refused at its push (review round 3, M2). That scan is stricter
    # than the range's, so the range skips the blobs it covers (round 4, performance B3).
    whole = bool(tip) and tip_build and is_main(ref)
    in_tip: set[tuple[str, str]] = set(found) if whole else set()
    # `published`: blobs main's history already has (the hook only): main's own runs checked them
    v = scan([i for i in items if i not in in_tip], skip=listed | published, x1_old=old, top=top)
    refused, warned = list(v.refused), list(v.warned)
    if tip:
        refused += [f"{p}: no card for it in its set" for p in orphans(found)]
        refused += unowned(found, top)
        if tip_build:
            covered = {b for b, _ in items}
            rest = found if whole else [(b, p) for b, p in found if b not in covered]
            t = scan(rest, skip=set() if whole else listed, x1_old=None if whole else old, top=top)
            refused += t.refused
            warned += t.warned
    return Verdict(refused, warned)


def run_tree(treeish: str, *, no_skip: bool, top: Path = TOP) -> Verdict:
    found = tree(treeish, top)
    v = scan(found, skip=set() if no_skip else exempt(top), x1_old=None, top=top)
    return Verdict([*v.refused, *(f"{p}: no card for it in its set" for p in orphans(found)),
                    *unowned(found, top)], v.warned)  # fmt: skip


def unowned(found: list[tuple[str, str]], top: Path = TOP) -> list[str]:
    """Every set file (labels, fixed labels, reviews, checks.yaml) in this tree that doesn't
    parse as the merge reads it, names an ID the merge's own rule refuses for one half
    (`merged.stray`: a label row or `checks.yaml` key with no card of its set; reviews are
    append-only history, so a removed card's rows stay, and moved IDs there are the moved-ID
    guard's), or holds text trigger 10's text check flags in any value (review round 3, M3;
    round 4, B1, B2). Read from the tree, so it holds for what is pushed."""
    cards: dict[str, set[str]] = {}
    for _b, path in found:
        if CARD.match(path):
            cards.setdefault(path.split("/")[2], set()).add(Path(path).stem)
    files = [(b, p) for b, p in found if SET_FILES.match(p)]
    data = cat(sorted({b for b, _ in files}), top)
    out: list[str] = []
    for b, path in files:
        kind = path.rsplit("/", 1)[1]
        where = f"{path} ({b[:12]})"
        text = data[b].decode("utf-8", "replace")
        try:
            ids = merged.ids_in(kind, text, where)
            rows = [ln for ln in text.splitlines() if ln.strip()]
            values: list[Any] = ([load_yaml(text, source=where)] if kind == merged.CHECKS
                                 else [json.loads(ln) for ln in rows])  # fmt: skip
        except InvalidInputError as exc:
            out.append(str(exc))
            continue
        own = cards.get(path.split("/")[2], set())
        out += [f"{where}: names {i}, which has no card in its set"
                for i in merged.stray(kind, ids, own, set())]  # fmt: skip
        flagged = sorted({h for v in values for s in strings(v) for h in text_injection(s)})
        if flagged:
            out.append(f"{where}: trigger 10's text check flags it ({', '.join(flagged[:5])})")
    return out


def list_known(rev: str, top: Path = TOP) -> list[str]:
    """Every card and email blob in `rev`'s history that trigger 10 flags (no exemption)."""
    items = changed([rev], top, want=wanted)
    data = cat(sorted({b for b, _ in items}), top)
    flagged: set[str] = set()
    for b, p in items:
        if EML.match(p):
            if card_injection(data[b]):
                flagged.add(b)
            continue
        text = data[b].decode("utf-8", "replace")
        try:
            header = markers.front_matter(text, source=p)
            card = parse_card(text, source=p)
        except Exception as exc:
            raise SystemExit(
                f"card scan: {p} ({b[:12]}) doesn't parse ({type(exc).__name__})"
            ) from exc
        if any(text_injection(s) for s in [*strings(header), body_of(text)]) or card_injection(
            build_bytes(placeholders(card))
        ):
            flagged.add(b)
    return sorted(flagged)


def run_daily(top: Path = TOP) -> Verdict:
    """Main's tip with nothing skipped and X1 on; every other remote branch and tag (tips and
    commits not on main) reading only blobs not already in main's history, leniently."""
    main = "refs/remotes/origin/main"
    v = run_tree(main, no_skip=True, top=top)
    refused, warned = list(v.refused), list(v.warned)
    history = {b for b, _ in changed([main], top)}
    refs = [r for r in git("for-each-ref", "--format=%(refname)", "refs/remotes", "refs/tags",
                            top=top).split() if r != main and not r.endswith("/HEAD")]  # fmt: skip
    items: list[tuple[str, str]] = []
    for r in refs:
        items += tree(r, top)
    items += changed([*refs, "--not", main], top) if refs else []
    o = scan(items, skip=history, x1_old=None, lenient=True, top=top)
    return Verdict(refused + o.refused, warned + o.warned)


ZERO = "0" * 40


def remote_id(url: str) -> str:
    """host/owner/repo of a remote URL, so its SSH and HTTPS forms compare equal."""
    u = url.strip().removesuffix("/").removesuffix(".git")
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)[:/](.+)$", u)
    return f"{m.group(1)}/{m.group(2)}".lower() if m else u.lower()


def peels_to_commit(sha: str, top: Path = TOP) -> bool:
    r = subprocess.run(["git", "-C", str(top), "cat-file", "-t", f"{sha}^{{}}"],
                       capture_output=True, text=True, check=False)  # fmt: skip
    return r.returncode == 0 and r.stdout.strip() == "commit"


def pushed(remote: str, lines: list[str], cwd: Path,
           ) -> tuple[list[str], list[tuple[str, str, list[str]]]]:  # fmt: skip
    """git's pre-push input as (refusals, [(remote ref, pushed sha, revs)]): deleting main and a
    ref that isn't a commit are refused; each range is the pushed commits only."""
    refused: list[str] = []
    ranges: list[tuple[str, str, list[str]]] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _lref, lsha, rref, rsha = parts
        if lsha == ZERO:
            if is_main(rref) or rref == "refs/heads/main":
                refused.append("deleting main is refused")
            continue
        if not peels_to_commit(lsha, cwd):
            refused.append(f"{rref}: the pushed object isn't a commit")
            continue
        known_rsha = rsha != ZERO and subprocess.run(
            ["git", "-C", str(cwd), "cat-file", "-e", f"{rsha}^{{commit}}"],
            capture_output=True, check=False).returncode == 0  # fmt: skip
        theirs = [r for r in git("for-each-ref", "--format=%(refname)",
                                 f"refs/remotes/{remote}", top=cwd).split()]  # fmt: skip
        if not theirs and peels_to_commit("refs/remotes/origin/main", cwd):
            theirs = ["refs/remotes/origin/main"]
        # nothing the remote is known to have: every commit is checked (a first push)
        revs = ([f"{rsha}..{lsha}"] if known_rsha
                else [lsha, "--not", *theirs] if theirs else [lsha])  # fmt: skip
        ranges.append((rref, lsha, revs))
    return refused, ranges


def _published(remote: str, top: Path) -> frozenset[str]:
    """Blobs in the remote's main history: public already and scanned by main's own runs, so
    the hook doesn't rebuild them when a branch merges main (review round 2, P7; the CI run of
    the push still scans the whole range)."""
    ref = f"refs/remotes/{remote}/main"
    if not peels_to_commit(ref, top):
        return frozenset()
    return frozenset(b for b, _ in changed([ref], top))


def check_private(blob: str, path: str, data: bytes) -> list[str]:
    """A private card: it must parse and pass the hygiene scan (fake data only). Its markers and
    trigger 10 are expected there (every private card is an injection card)."""
    try:
        card = parse_card(data.decode("utf-8"), source=path)
    except Exception as exc:
        return [f"{path} ({blob[:12]}): doesn't parse ({type(exc).__name__})"]
    return [f"{path} ({blob[:12]}): hygiene: {f.where} {f.kind}"
            for f in scan_card(card, Path(path).name)]  # fmt: skip


def run_hook(remote: str, url: str, lines: list[str], cwd: Path) -> Verdict:
    """The pre-push hook: each pushed (local ref, local sha, remote ref, remote sha). Both
    repositories refuse deleting main and a ref that isn't a commit and check each pushed range
    (the pushed blobs only): the private one only to its recorded remote, with the hygiene scan
    on its cards; the public one with the full card scan."""
    here = Path(git("rev-parse", "--show-toplevel", top=cwd).strip())
    # which repository this is, from the repository itself (review round 2, M3): a private
    # clone records its remote in `ecf.privateRemote` wherever it sits and whichever checkout's
    # hook folder it uses; anything else must be the public checkout this script belongs to
    want = git("config", "--default", "", "ecf.privateRemote", top=here).strip()
    private = bool(want)
    if not private and here.resolve() != TOP.resolve():
        return Verdict([f"this hook guards {TOP} and private clones that record"
                        f" ecf.privateRemote; {here} is neither"], [])  # fmt: skip
    if private and remote_id(url) != remote_id(want):
        return Verdict([f"eval/private pushes only to its recorded remote ({want}), not {url}"],
                       [])  # fmt: skip
    refused, ranges = pushed(remote, lines, cwd)
    warned: list[str] = []
    history = None if private or not ranges else (_published(remote, cwd), anchor_history(cwd))
    for rref, lsha, revs in ranges:
        if private:
            items = changed(revs, cwd, want=lambda p: PRIVATE_CARD.match(p) is not None)
            data = cat(sorted({b for b, _ in items}), cwd)
            for b, p in dict.fromkeys(items):
                refused += check_private(b, p, data[b])
            continue
        main_ref = "refs/heads/main" if rref == "refs/heads/main" else rref
        if history is None:  # unreachable: set whenever there are ranges
            continue
        v = run_range(main_ref, revs, lsha, tip_build=False, top=cwd, published=history[0],
                      old=history[1])  # fmt: skip
        refused += v.refused
        warned += v.warned
    return Verdict(refused, warned)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--ref")
    ap.add_argument("--revs", nargs=argparse.REMAINDER)
    ap.add_argument("--tip")
    ap.add_argument("--no-tip-build", action="store_true")
    ap.add_argument("--tree")
    ap.add_argument("--no-skip", action="store_true")
    ap.add_argument("--list-known")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--hook", nargs=2, metavar=("REMOTE", "URL"))
    a = ap.parse_args(argv)
    if a.hook:
        v = run_hook(a.hook[0], a.hook[1], sys.stdin.read().splitlines(), Path.cwd())
        for w in v.warned:
            print(f"warning: {w}", file=sys.stderr)
        for r in v.refused:
            print(f"refused: {r}", file=sys.stderr)
        return 1 if v.refused else 0
    if a.list_known:
        sys.stdout.write("".join(b + "\n" for b in list_known(a.list_known)))
        return 0
    if a.tree:
        v = run_tree(a.tree, no_skip=a.no_skip)
    elif a.daily:
        v = run_daily()
    elif a.ref and a.revs:
        v = run_range(a.ref, a.revs, a.tip, tip_build=not a.no_tip_build)
    else:
        ap.error("give --ref with --revs, --tree, --list-known or --daily")
    for w in v.warned:
        print(f"warning: {w}", file=sys.stderr)
    for r in v.refused:
        print(f"refused: {r}", file=sys.stderr)
    return 1 if v.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
