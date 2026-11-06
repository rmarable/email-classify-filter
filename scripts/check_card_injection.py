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
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.eval import markers
from ecf.eval.builder import build_bytes, scan_card
from ecf.eval.cards import Card, parse_card
from ecf_server.triggers import card_injection, text_injection

TOP = Path(__file__).resolve().parents[1]
LIST = "tests/eval/known-public-injection-blobs.txt"
# main's tip the branch sits on (X1): blobs in main's history here are old marked cards, not new
# ones; moved with the list if the branch is rebased
X1_ANCHOR = "8b26fd6ae4a5d74c650e2b6fc775809332655496"
STARTER_EML_SHA256 = "2cfe1cf857be1b02621ef36e22ca3d8eb358f4a058cd1e551b0b3e997cb7ed9f"
CARD = re.compile(r"^tests/eval/[^/]+/cases/[^/]+\.md$")
EML = re.compile(r"^tests/eval/[^/]+/(eml|\.build)/[^/]+\.eml$")


def git(*args: str, stdin: str | None = None, top: Path = TOP) -> str:
    r = subprocess.run(["git", "-C", str(top), *args], input=stdin, capture_output=True,
                       text=True, check=False)  # fmt: skip
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


def changed(revs: list[str], top: Path = TOP) -> list[tuple[str, str]]:
    """(blob, path) for every card and email file each commit in `revs` added or changed, merge
    commits included and deleted files skipped, through one batched `git diff-tree --stdin`."""
    commits = git("rev-list", *revs, top=top)
    if not commits.strip():
        return []
    out = git("diff-tree", "--stdin", "-r", "-m", "--root", "--no-renames", "--diff-filter=d",
              stdin=commits, top=top)  # fmt: skip
    seen: dict[tuple[str, str], None] = {}
    for line in out.splitlines():
        if not line.startswith(":"):
            continue
        meta, path = line.split("\t", 1)
        blob = meta.split()[3]
        if wanted(path):
            seen[(blob, path)] = None
    return list(seen)


def tree(treeish: str, top: Path = TOP) -> list[tuple[str, str]]:
    out = git("ls-tree", "-r", treeish, "--", "tests/eval", top=top)
    found: list[tuple[str, str]] = []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        kind, blob = meta.split()[1:3]
        if kind == "blob" and wanted(path):
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
    todo = [(b, p) for b, p in dict.fromkeys(items) if b not in skip]
    data = cat(sorted({b for b, _ in todo}), top)
    refused: list[str] = []
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
        if wanted(path):
            v = check(hashlib.sha1(data).hexdigest(), path, data, x1=True)  # noqa: S324 - a label
            refused += v.refused
            warned += v.warned
    return Verdict(refused, warned)


def anchor_history(top: Path = TOP) -> set[str]:
    r = subprocess.run(["git", "-C", str(top), "cat-file", "-e", f"{X1_ANCHOR}^{{commit}}"],
                       capture_output=True, check=False)  # fmt: skip
    return {b for b, _ in changed([X1_ANCHOR], top)} if r.returncode == 0 else set()


def run_range(ref: str, revs: list[str], tip: str | None, *, tip_build: bool,
              top: Path = TOP) -> Verdict:  # fmt: skip
    """A pushed range: every changed blob (listed blobs skipped; X1 only on blobs new since the
    anchor), then the tip's path checks and, except in the hook, a build of the tip's other
    blobs: unskipped with X1 on main, with the list elsewhere."""
    items = changed(revs, top)
    listed, old = known(top), anchor_history(top)
    v = scan(items, skip=listed, x1_old=old, top=top)
    refused, warned = list(v.refused), list(v.warned)
    if tip:
        found = tree(tip, top)
        refused += [f"{p}: no card for it in its set" for p in orphans(found)]
        if tip_build:
            covered = {b for b, _ in items}
            main = is_main(ref)
            rest = [(b, p) for b, p in found if b not in covered or (main and b in listed)]
            t = scan(rest, skip=set() if main else listed, x1_old=None if main else old, top=top)
            refused += t.refused
            warned += t.warned
    return Verdict(refused, warned)


def run_tree(treeish: str, *, no_skip: bool, top: Path = TOP) -> Verdict:
    found = tree(treeish, top)
    v = scan(found, skip=set() if no_skip else known(top), x1_old=None, top=top)
    return Verdict([*v.refused, *(f"{p}: no card for it in its set" for p in orphans(found))],
                   v.warned)  # fmt: skip


def list_known(rev: str, top: Path = TOP) -> list[str]:
    """Every card and email blob in `rev`'s history that trigger 10 flags (no exemption)."""
    items = changed([rev], top)
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


def run_hook(remote: str, url: str, lines: list[str], cwd: Path) -> Verdict:
    """The pre-push hook: each pushed (local ref, local sha, remote ref, remote sha). In the
    private repository, only a push to its recorded remote; in the public one, refuses deleting
    main and a ref that isn't a commit, and scans each pushed range (the pushed blobs only)."""
    here = Path(git("rev-parse", "--show-toplevel", top=cwd).strip())
    if here.resolve() == (TOP / "eval" / "private").resolve():
        want = git("config", "--default", "", "ecf.privateRemote", top=here).strip()
        if not want or remote_id(url) != remote_id(want):
            return Verdict([f"eval/private pushes only to its recorded remote ({want or 'none'})"
                            f", not {url}"], [])  # fmt: skip
        return Verdict([], [])
    refused: list[str] = []
    warned: list[str] = []
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
        revs = ([f"{rsha}..{lsha}"] if known_rsha
                else [lsha, "--not", *(theirs or ["refs/remotes/origin/main"])])  # fmt: skip
        main_ref = "refs/heads/main" if rref == "refs/heads/main" else rref
        v = run_range(main_ref, revs, lsha, tip_build=False, top=cwd)
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
