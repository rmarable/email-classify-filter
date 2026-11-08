"""Anything of a set that can't be read (Step 3 review rounds 16-17, operator decisions 2026-11-06:
one rule, proved over every placement): in both layouts (a split set; today's public-only set),
for each folder or file made unreadable in turn and each mode (000; 0o444, listable but not
searchable for a folder; 0o300, searchable but not listable), the service tick and the gate never
raise, and every other entry point either works or refuses with a named InvalidInputError, never
a raw OSError. On a set that passes the gate (round 18), the gate fails for every placement and
mode that takes content away, passes for the rest, and passes again once the permission is put
back, with no cache reset. A heal test checks the refresh repairs the merged view's own parts."""

from __future__ import annotations

import atexit
import contextlib
import errno
import getpass
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import genset, gitcheck, setio
from ecf.eval import merged as mg
from ecf.eval.builder import build_all, marker_refusals
from ecf_server import evalrun, gate, ruletest, stages
from ecf_server.clock import FakeClock
from tests.test_eval_private_gate import _counts, _run, git, repos  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_resolver_wiring import _split_set, complete_copy  # pyright: ignore[reportPrivateUsage]
from tests.test_gate import DIGEST, make_address

SET = "heldout3"  # the smallest real set (30 cards): what's proved doesn't depend on its size
S = f"tests/eval/{SET}"
P = f"eval/private/{SET}"
M = f"eval/private/.merged/{SET}"
# (heldout3 has no fixed_labels.jsonl; it is read by the same calls as labels.jsonl, which is here;
# no case may skip: the macOS merge gate runs with 0 skipped)
COMMON = ["tests", "tests/eval", S, f"{S}/cases", f"{S}/labels.jsonl", "<a public card>"]
SPLIT = [*COMMON, "eval", "eval/private", P, f"{P}/cases", f"{P}/labels.jsonl",
         "eval/private/.merged", M, f"{M}/cases", f"{M}/labels.jsonl", f"{M}/merge.json",
         f"{M}.lock"]  # fmt: skip
MODES = [0o000, 0o444, 0o300]
_TEMPLATES: dict[str, Path] = {}


def _template(layout: str) -> Path:
    """Each layout built once per worker and copied for every case (fixture time, not the
    code under test, dominates otherwise)."""
    if layout not in _TEMPLATES:
        top = Path(tempfile.mkdtemp(prefix=f"ecf-unreadable-{layout}-"))
        atexit.register(shutil.rmtree, top, True)
        pub, _priv, _n = _split_set(top, SET, 4)
        if layout == "public only":
            shutil.rmtree(top / "eval")
            (top / ".gitignore").write_text("eval/private/\n")
            git(top, "init", "-q", "-b", "main")
            git(top, "add", "-A")
            git(top, "commit", "-q", "-m", "set")
        else:
            repos(top)
            mg.refresh_merged(pub)
        _TEMPLATES[layout] = top
    return _TEMPLATES[layout]


def _copy(layout: str, dst: Path) -> Path:
    shutil.copytree(_template(layout), dst, symlinks=True)
    if layout == "split":
        git(dst / "eval" / "private", "remote", "set-url", "origin", str(dst / "remote.git"))
        mg.refresh_merged(dst / S)  # the copies' own inodes
    return dst / S


def _entry_points(conn: sqlite3.Connection, clock: FakeClock, pub: Path,
                  ) -> list[tuple[str, Callable[[], Any]]]:  # fmt: skip
    top = pub.parents[2]

    def take() -> None:
        mg.release(mg.take_shared(pub))

    return [
        ("resolve", lambda: mg.resolve(pub)),
        ("is_fresh", lambda: mg.is_fresh(mg.paths(pub))),
        ("membership", lambda: mg.membership(pub)),
        ("owners", lambda: mg.owners(pub)),
        ("take_shared", take),
        ("read_set", lambda: evalrun.read_set(pub)),
        ("set_version", lambda: evalrun.set_version(pub)),
        ("load_cases", lambda: ruletest.load_cases(pub)),
        ("is_generated", lambda: genset.is_generated(pub)),
        ("clean_and_pushed", lambda: gitcheck.clean_and_pushed(top, pub.name)),
        ("fingerprint", lambda: gitcheck.fingerprint(top, pub.name)),
        ("marker_refusals", lambda: marker_refusals(pub, private=False)),
        ("refresh_merged", lambda: mg.refresh_merged(pub)),  # last: it repairs the view
    ]


def _cases() -> list[object]:
    out: list[object] = []
    for layout, places in (("split", SPLIT), ("public only", COMMON)):
        for place in places:
            for mode in MODES:
                out.append(pytest.param(layout, place, mode, id=f"{layout}|{place}|{mode:o}"))
    return out


@pytest.mark.parametrize(("layout", "place", "mode"), _cases())
def test_nothing_unreadable_escapes_and_the_tick_never_raises(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, layout: str, place: str,
    mode: int,
) -> None:  # fmt: skip
    pub = _copy(layout, tmp_path / "top")
    top = pub.parents[2]
    make_address(conn, clock, "assist")
    _run(conn, clock, pub, _counts(pub))
    target = (next((pub / "cases").glob("*.md")) if place == "<a public card>"
              else top / place)  # fmt: skip
    assert target.exists(), f"{place} isn't in this layout's set"
    before = target.stat().st_mode & 0o777
    target.chmod(mode)
    raw: list[str] = []
    try:
        gate.reset_cache()
        gitcheck.reset_backoff()
        for name, call in (("tick", lambda: stages.tick(conn, clock)),
                           ("inputs", lambda: gate.inputs(conn, "ap", DIGEST)),
                           ("synthetic", lambda: gate.synthetic(conn, DIGEST))):  # fmt: skip
            try:  # first, as the service asks: these never raise at all
                call()
            except Exception as exc:
                raw.append(f"{name}: {type(exc).__name__}: {exc}")
        for name, call in _entry_points(conn, clock, pub):
            try:
                call()
            except InvalidInputError:
                pass  # a named refusal, or a stale set refused: both fine
            except Exception as exc:
                raw.append(f"{name}: {type(exc).__name__}: {exc}")
    finally:
        if target.exists() or target.is_symlink():
            target.chmod(before)
    assert not raw, (layout, place, oct(mode), raw)


def test_the_merged_view_heals_after_any_of_its_parts_was_unreadable(tmp_path: Path) -> None:
    """The view's own folders, files and lock are the refresh's to repair."""
    pub = _copy("split", tmp_path / "top")
    top = pub.parents[2]
    for rel in (M, f"{M}/cases", f"{M}/labels.jsonl", f"{M}/merge.json", f"{M}.lock"):
        (top / rel).chmod(0o000)
        assert mg.refresh_merged(pub) in ("refreshed", "fresh"), rel
        assert mg.is_fresh(mg.paths(pub)), rel
    doc = json.loads((top / M / mg.MERGE_FILE).read_text())
    assert doc["format"] == mg.FORMAT


@pytest.mark.parametrize("mode", [0o000, 0o300], ids=lambda m: f"{m:o}")
def test_the_builder_refuses_an_unreadable_card_by_name(tmp_path: Path, mode: int) -> None:
    """`ecf eval build` on a card that can't be read: a named refusal, never a traceback (round
    17). Kept out of the placement grid: a build rewrites every email, seconds per case."""
    pub = _copy("public only", tmp_path / "top")
    card = next((pub / "cases").glob("*.md"))
    card.chmod(mode)
    try:
        with pytest.raises(InvalidInputError, match="can't be read"):
            build_all(pub)
    finally:
        card.chmod(0o644)


GSET = "synthetic"  # the gate model needs a set that passes: built emails, both halves
GS, GP, GM = f"tests/eval/{GSET}", f"eval/private/{GSET}", f"eval/private/.merged/{GSET}"
# folders only searched, never listed, by a reader (git reads `.git` by path too)
PARENTS = ("tests", "tests/eval", "eval", "eval/private", "eval/private/.merged",
           "eval/private/.git")  # fmt: skip
PICK = {  # a placement inside a folder: its first file of that kind
    "<a public card>": (GS, "cases", "*.md"), "<a public email>": (GS, "eml", "*.eml"),
    "<a public built email>": (GS, ".build", "*.eml"),
    "<a private card>": (GP, "cases", "*.md"), "<a private email>": (GP, "eml", "*.eml"),
    "<a private built email>": (GP, ".build", "*.eml"),
    "<a merged card>": (GM, "cases", "*.md"), "<a merged email>": (GM, "eml", "*.eml"),
    "<a merged built email>": (GM, ".build", "*.eml"),
}  # fmt: skip
GATE_PLACES = [
    "tests", "tests/eval", GS, f"{GS}/cases", f"{GS}/eml", f"{GS}/.build", f"{GS}/labels.jsonl",
    f"{GS}/fixed_labels.jsonl", f"{GS}/reviews.jsonl", f"{GS}/checks.yaml",
    "eval", "eval/private", "eval/private/.git", GP, f"{GP}/cases", f"{GP}/eml", f"{GP}/.build",
    f"{GP}/labels.jsonl", f"{GP}/fixed_labels.jsonl", f"{GP}/reviews.jsonl",
    "eval/private/.merged", GM, f"{GM}/cases", f"{GM}/eml", f"{GM}/.build", f"{GM}/labels.jsonl",
    f"{GM}/fixed_labels.jsonl", f"{GM}/reviews.jsonl", f"{GM}/checks.yaml", f"{GM}/merge.json",
    f"{GM}.lock", *PICK,
]  # fmt: skip


def _to_build(half: Path) -> None:
    """Move one email of a half to `.build/` (as the builder places a large one) and point its
    label row there, so the gate model has a `.build/` file in each half and in the view."""
    eml = sorted((half / "eml").glob("*.eml"))[0]
    (half / ".build").mkdir()
    eml.rename(half / ".build" / eml.name)
    rows = [json.loads(r) for r in (half / "labels.jsonl").read_text().splitlines()]
    for r in rows:
        if r["file"] == f"eml/{eml.name}":
            r["file"] = f".build/{eml.name}"
    (half / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _gate_tree() -> Path:
    """One split synthetic set per worker, with a `.build/` file in each half and a checks.yaml,
    used by every case in turn: each case puts its mode back (the per-case copy cost ~0.4 s;
    review round 19, performance 2)."""
    if "gate" not in _TEMPLATES:
        top = Path(tempfile.mkdtemp(prefix="ecf-unreadable-gate-"))
        atexit.register(shutil.rmtree, top, True)
        pub, priv, _n = _split_set(top, GSET, 12)
        for half in (pub, priv):
            _to_build(half)
        (pub / "checks.yaml").write_text("{}\n")
        repos(top)
        with (top / ".gitignore").open("a") as f:  # as the repository does: built, ignored
            f.write(f"{GS}/.build/\n")
        git(top, "rm", "-r", "-q", "--cached", f"{GS}/.build")
        git(top, "commit", "-q", "-am", "ignore .build")
        mg.refresh_merged(pub)
        _TEMPLATES["gate"] = top
    return _TEMPLATES["gate"]


_COUNTS: dict[str, dict[str, int]] = {}


def _gate_counts(pub: Path) -> dict[str, int]:
    """The shared tree's reference counts, worked out once per worker: no case changes the
    set's content (each puts its mode back), so they hold for every case (review round 20)."""
    if "gate" not in _COUNTS:
        _COUNTS["gate"] = _counts(pub)
    return _COUNTS["gate"]


def _target(top: Path, place: str) -> Path:
    if place in PICK:
        half, folder, glob = PICK[place]
        return sorted((top / half / folder).glob(glob))[0]
    return top / place


def _takes_content(place: str, is_dir: bool, mode: int) -> bool:
    """Whether a mode takes the set's content away from a reader: nothing at 000; a file that
    can't be read (0o300); a folder that can't be searched (0o444), or one whose entries are
    listed (the set's folders; not the folders above them, nor `.git`) that can't be listed
    (0o300)."""
    if mode == 0o000:
        return True
    if is_dir:
        return mode == 0o444 or place not in PARENTS
    return mode == 0o300


def _gate(conn: sqlite3.Connection, *, fresh: bool = False) -> Any:
    if fresh:
        gate.reset_cache()
    gitcheck.reset_backoff()
    return gate.synthetic(conn, DIGEST)


@pytest.mark.parametrize(
    ("place", "mode"), [pytest.param(p, m, id=f"{p}|{m:o}") for p in GATE_PLACES for m in MODES]
)
def test_the_gate_fails_while_content_is_unreadable_and_passes_once_it_is_back(
    conn: sqlite3.Connection, clock: FakeClock, place: str, mode: int,
) -> None:  # fmt: skip
    """The gate's assertion can fail (review round 18, B2: its baseline passes); while broken
    the gate gives the same answer from its cache and from a fresh read (round 19, B4); a
    failure from a permission isn't kept after the permission is put back (round 18, B1)."""
    top = _gate_tree()
    pub = top / GS
    make_address(conn, clock, "assist")
    _run(conn, clock, pub, _gate_counts(pub))
    base = _gate(conn, fresh=True)
    assert base.ok, base.detail
    target = _target(top, place)
    assert target.exists(), place
    before = target.stat().st_mode & 0o777
    target.chmod(mode)
    try:
        cached = _gate(conn)  # the key computed under the new mode, the cache as it was
        stages.tick(conn, clock)  # never raises; caches what it may
        fresh = _gate(conn, fresh=True)
    finally:
        target.chmod(before)
    want = not _takes_content(place, target.is_dir(), mode)
    assert (cached.ok, fresh.ok) == (want, want), (cached.detail, fresh.detail)
    stages.tick(conn, clock)
    back = _gate(conn)  # no cache reset: the key must see the permission
    assert back.ok, back.detail


@pytest.mark.macos  # access rules (chmod +a) are macOS's
@pytest.mark.parametrize(
    "place", [f"{GM}.lock", "eval/private/.merged", "tests/eval", f"{GP}/cases"]
)
def test_an_access_rule_changes_the_key(
    conn: sqlite3.Connection, clock: FakeClock, place: str,
) -> None:  # fmt: skip
    """An ACL that denies reading leaves the mode as it was; a cached pass still isn't served
    while the rule holds: on the lock its ctime is keyed; on a folder the rule also stops the
    lstat of what is under it, which the key records (review rounds 19-20; tests/eval and the
    view's parent are keyed without ctime)."""
    top = _gate_tree()
    pub = top / GS
    make_address(conn, clock, "assist")
    _run(conn, clock, pub, _gate_counts(pub))
    assert _gate(conn, fresh=True).ok
    target = top / place
    rule = f"{getpass.getuser()} deny " + ("list,search" if target.is_dir() else "read")
    subprocess.run(["chmod", "+a", rule, str(target)], check=True)
    try:
        cached = _gate(conn)
    finally:
        subprocess.run(["chmod", "-N", str(target)], check=True)
    assert not cached.ok, cached.detail
    assert _gate(conn).ok


def test_a_failure_from_a_permission_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Each read that fails on a permission is retried, even if the key can't see the fix
    (review round 18, B1: the key's permission stats are one guard; this is the other)."""
    pub = _copy("split", tmp_path / "top")
    calls: list[int] = []

    def refuse(_root: Path) -> Any:
        calls.append(1)
        raise mg.unreadable(pub / "labels.jsonl", PermissionError(13, "Permission denied"))

    monkeypatch.setattr(evalrun, "read_set", refuse)
    gate.reset_cache()
    for _ in range(2):
        with gate.one_pass():
            got = gate.set_state(pub)
            assert got.error and got.transient, got
    assert len(calls) == 2


def test_nothing_is_cached_under_an_unreadable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """A key that names an error isn't the set's files: no state is stored or found under it
    (review round 18, performance B1)."""
    pub = _copy("split", tmp_path / "top")

    def broken(_root: Path) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(gate, "_key_of", broken)
    gate.reset_cache()
    first = gate.set_state(pub)
    assert first.transient
    assert not gate._STATE  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("args", [["--status"], [], ["--review", "fraud_risk", "--cases", "<id>"],
                                  ["--import-fixed", "<answers>"]],
                         ids=["status", "label", "review", "import-fixed"])  # fmt: skip
@pytest.mark.parametrize("place", ["<a public card>", "labels.jsonl"])
def test_the_label_commands_refuse_an_unreadable_file_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], place: str,
) -> None:  # fmt: skip
    """`ecf eval label` in each mode reaches the unreadable file and refuses it by name, never a
    raw PermissionError (review rounds 18-19, B3; `main` turns the refusal into a message). The
    review runs past its terminal check on one card; the import confirms that card's label."""
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf import prompts  # noqa: PLC0415
    from ecf.cli import app  # noqa: PLC0415

    def any_terminal(stdin: object = None) -> None:
        return None

    monkeypatch.setattr(prompts, "require_terminal", any_terminal)
    pub = _copy("split", tmp_path / "top")
    rows = [json.loads(r) for r in (pub / "labels.jsonl").read_text().splitlines()]
    row = next(r for r in rows if r["expected"]["labels"].get("category"))
    card = pub / "cases" / f"{row['id']}.md"
    answer = {"label": row["expected"]["labels"]["category"], "how": "single"}
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"item": "category", "on": "2026-11-06",
                                   "answers": {card.stem: answer}}))  # fmt: skip
    target = card if place == "<a public card>" else pub / place
    target.chmod(0o000)
    try:
        sub = {"<answers>": str(answers), "<id>": card.stem}
        argv = [sub.get(a, a) for a in args]
        r = CliRunner().invoke(app, ["eval", "label", *argv, "--root", str(pub)], input="")
    finally:
        target.chmod(0o644)
    assert r.exit_code != 0, r.output
    assert isinstance(r.exception, InvalidInputError), repr(r.exception)
    assert "can't be read" in str(r.exception), r.exception


NAMED = [  # (place, mode, the part a refusal must name: what the operator can fix first)
    ("<a private card>", 0o000, "<a private card>"), ("<a merged card>", 0o000, "<a merged card>"),
    (f"{GM}/merge.json", 0o000, f"{GM}/merge.json"),
    (f"{GP}/labels.jsonl", 0o000, f"{GP}/labels.jsonl"),
    (f"{GM}/cases", 0o000, f"{GM}/cases"), (f"{GM}/cases", 0o444, f"{GM}/cases"),
    (f"{GM}/cases", 0o300, f"{GM}/cases"), (f"{GP}/cases", 0o444, f"{GP}/cases"),
    (f"{GP}/cases", 0o300, f"{GP}/cases"), (GP, 0o444, GP), (GM, 0o444, GM), (GM, 0o300, GM),
    (GS, 0o444, GS), (f"{GP}/.build", 0o000, f"{GP}/.build"),
    (f"{GM}/labels.jsonl", 0o000, f"{GM}/labels.jsonl"),
]  # fmt: skip


@pytest.mark.parametrize(("place", "mode", "named"),
                         [pytest.param(*n, id=f"{n[0]}|{n[1]:o}") for n in NAMED])  # fmt: skip
def test_an_unreadable_part_is_named_and_its_failure_never_cached(
    place: str, mode: int, named: str,
) -> None:  # fmt: skip
    """A file or folder that can't be read is refused by name, and the name is the part to fix:
    the outermost folder that can't be searched, else the folder that can't be listed or the
    file that can't be read, never one inside it or a file that may not exist (review rounds
    19-20); never "missing or out of date"; the gate's failure from it is transient."""
    top = _gate_tree()
    pub = top / GS
    target = _target(top, place)
    want = os.path.realpath(_target(top, named))  # as stored: macOS /var is /private/var
    before = target.stat().st_mode & 0o777
    target.chmod(mode)
    try:
        with pytest.raises(mg.Unreadable) as got_exc:
            mg.resolve(pub)
        gate.reset_cache()
        gitcheck.reset_backoff()
        got = gate.set_state(pub)
    finally:
        target.chmod(before)
    message = str(got_exc.value)
    assert message.startswith(f"{want}: can't be read"), (message, want)
    assert got.error and "can't be read" in got.error and got.transient, got
    assert not gate._STATE  # pyright: ignore[reportPrivateUsage]


def test_a_set_outside_a_checkout_keeps_its_cache_when_its_parent_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """The key leaves out the parent of a set outside a checkout: anything may change there
    (a temporary folder), and every pass would read the set again (review round 19, B5)."""
    pub = complete_copy("heldout3", tmp_path / "loose" / "heldout3")
    reads: list[int] = []
    real = evalrun.read_set

    def counted(root: Path) -> Any:
        reads.append(1)
        return real(root)

    monkeypatch.setattr(evalrun, "read_set", counted)
    gate.reset_cache()
    gate.set_state(pub)
    (pub.parent / "other.txt").write_text("x")  # the parent's ctime changes
    gate.set_state(pub)
    assert len(reads) == 1


def test_another_sets_folders_leave_the_key_as_it_was() -> None:
    """tests/eval and the view's parent hold other sets: a new set's view and lock, or a Finder
    `.DS_Store`, change their ctime but not this set's key (review round 20, B2)."""
    top = _gate_tree()
    pub = top / GS
    before = gate._key_of(pub)  # pyright: ignore[reportPrivateUsage]
    made = [top / "eval/private/.merged/other", top / "eval/private/.merged/other.lock",
            top / "tests/eval/.DS_Store"]  # fmt: skip
    made[0].mkdir()
    made[1].write_text("")
    made[2].write_text("")
    try:
        after = gate._key_of(pub)  # pyright: ignore[reportPrivateUsage]
    finally:
        made[0].rmdir()
        made[1].unlink()
        made[2].unlink()
    assert after == before


def _named_as(message: str, part: Path) -> None:
    """The refusal names `part` (as stored: macOS /var is /private/var) and says what to check."""
    got = message.split(": can't be read or written", maxsplit=1)[0]
    assert os.path.realpath(got) == os.path.realpath(part), (message, part)
    assert "lock flag" in message and "ls -ldO" in message, message


def _temps(folder: Path) -> list[str]:
    """Every temporary file in `folder` and its subfolders."""
    return sorted(str(f.relative_to(folder)) for f in folder.rglob("*.tmp"))


WRITES = ["folder not writable", "a new file, folder not writable", "the file locked (uchg)"]


@pytest.mark.parametrize("case", WRITES)
def test_a_refused_write_names_the_file_it_writes_and_leaves_no_temporary_file(case: str) -> None:
    """A refused write names the file being written, never its temporary file, or the folder
    when the file doesn't exist yet; the message says to check the permissions and lock flags
    of both, without guessing which is the cause. A write refused after its temporary file was
    made (the file locked: the rename is refused) leaves none (review rounds 21-23, B1)."""
    locked = case.endswith("(uchg)")
    if locked and not hasattr(os, "chflags"):
        pytest.skip("no file flags on this system")
    top = _gate_tree()
    folder = top / GP
    new = case.startswith("a new file")
    path = folder / ("absent.jsonl" if new else "labels.jsonl")
    if locked:
        os.chflags(path, stat.UF_IMMUTABLE)
    else:
        folder.chmod(0o555)
    try:
        with pytest.raises(mg.Unreadable) as got, mg.named_errors():
            setio.atomic_write(path, b"" if new else path.read_bytes())
        temps = _temps(folder)  # before any cleanup
    finally:
        folder.chmod(0o755)
        if locked:
            os.chflags(path, 0)
    _named_as(str(got.value), folder if new else path)
    assert ".tmp" not in str(got.value), got.value
    if locked:
        assert temps == [], temps


def test_a_write_never_touches_another_writers_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Each write makes a temporary file of its own, never one already there: another writer's
    temporary file, even under the very name this write drew first, is neither removed nor
    renamed into place, and the write still lands (review round 23, B5)."""
    top = _gate_tree()
    path = top / GP / "labels.jsonl"
    theirs = path.with_name(f".{path.name}.aaaaaaaaaaaa.tmp")
    theirs.write_text("another writer's half-written file")
    names = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])

    def drawn(_n: int) -> str:
        return next(names)

    monkeypatch.setattr(setio.secrets, "token_hex", drawn)
    data = path.read_bytes()
    try:
        setio.atomic_write(path, data)
        after = theirs.read_text()
    finally:
        theirs.unlink(missing_ok=True)
    assert after == "another writer's half-written file"
    assert path.read_bytes() == data and _temps(top / GP) == []


REFRESH_LOCKS = ["<a merged card>", "<a merged built email>", "merge.json's rename", GM]


def _source_of(top: Path, merged: Path) -> Path:
    rel = merged.relative_to(top / GM)
    return next(top / h / rel for h in (GP, GS) if (top / h / rel).exists())


@contextlib.contextmanager
def _healed(pub: Path) -> Generator[None]:
    """The view refreshed again on the way out, for the worker's later cases; a heal that fails
    after the case failed is added to that failure as a note, never in its place (round 24)."""
    failed: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        failed = exc
        raise
    finally:
        try:
            mg.refresh_merged(pub)
        except Exception as heal:
            if failed is None:
                raise
            failed.add_note(f"(the healing refresh failed too: {heal!r})")


@pytest.mark.macos
@pytest.mark.parametrize("locked", REFRESH_LOCKS)
def test_a_refused_refresh_names_what_it_writes_and_leaves_no_temporary_file(
    locked: str, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """A refresh refused while it writes (a lock flag on a copied card or a cloned built email;
    merge.json's final rename refused, after its temporary file was made; the view folder
    locked, which stops the removal of the old merge.json) names the file it writes, never a
    temporary file, and leaves none; the view is healed on the way out (review rounds 22-24)."""
    top = _gate_tree()
    pub = top / GS
    renamed: list[str] = []
    if locked == "merge.json's rename":
        target, source = top / GM / "merge.json", None
        real = os.replace

        def replace(src: Any, dst: Any, *a: Any, **k: Any) -> None:
            if Path(dst).name == "merge.json":
                renamed.append(str(src))
                raise PermissionError(
                    errno.EPERM, "Operation not permitted", str(src), None, str(dst)
                )
            real(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", replace)
    else:
        target = _target(top, locked)
        source = _source_of(top, target) if locked in PICK else None
    with _healed(pub), _stale(top, source):
        if locked != "merge.json's rename":
            os.chflags(target, stat.UF_IMMUTABLE)
        try:
            with pytest.raises(mg.Unreadable) as got:
                mg.refresh_merged(pub)
            temps = _temps(top / GM)  # before the heal, which would sweep one away
        finally:
            monkeypatch.undo()
            if locked != "merge.json's rename":
                os.chflags(target, 0)
    message = str(got.value)
    assert ".tmp" not in message and "lock flag" in message, message
    assert temps == [], temps
    named = message.split(": can't be read or written", maxsplit=1)[0]
    if locked == GM:  # refused at the old merge.json's removal, before any write
        assert Path(os.path.realpath(named)) == Path(os.path.realpath(target / "merge.json"))
    elif locked == "merge.json's rename":  # the old one is gone by then: a new file, its folder
        assert os.path.realpath(named) == os.path.realpath(target.parent), message
    else:
        assert os.path.realpath(named) == os.path.realpath(target), message
    if locked == "merge.json's rename":
        assert renamed and renamed[0].endswith(".tmp"), renamed  # a temporary file was made


@contextlib.contextmanager
def _stale(top: Path, card: Path | None = None) -> Generator[None]:
    """The shared tree made stale by an edit of a source card (a private one by default), then
    put back byte for byte (freshness is by content, so the view is fresh again)."""
    card = card or _target(top, "<a private card>")
    data = card.read_bytes()
    card.write_bytes(data + b"\n")
    try:
        yield
    finally:
        card.write_bytes(data)


ONLY_STALE = [  # each takes away nothing a reader reads
    "the public half's folder searchable, not listable (0o311)",
    "the private half's folder searchable, not listable (0o311)",
    "a stray subfolder no one can open in a card folder",
]


def _only_stale(top: Path, which: str) -> Callable[[], None]:
    if which in ONLY_STALE[:2]:
        half = top / (GS if which == ONLY_STALE[0] else GP)
        before = half.stat().st_mode & 0o777
        half.chmod(0o311)
        return lambda: half.chmod(before)
    stray = top / GP / "cases" / "stray"
    stray.mkdir()
    stray.chmod(0o000)

    def undo() -> None:
        stray.chmod(0o700)
        stray.rmdir()

    return undo


@pytest.mark.parametrize("which", ONLY_STALE)
def test_a_part_no_reader_reads_is_never_named(which: str) -> None:
    """A folder that can be searched but not listed, where readers only search it, or a stray
    subfolder no reader opens, is no permission problem: the fresh set reads as usual and a
    stale one is "out of date", never "can't be read" (review round 21, B3; the one freshness
    check names only what it reads)."""
    top = _gate_tree()
    pub = top / GS
    undo = _only_stale(top, which)
    try:
        assert os.path.realpath(mg.resolve(pub)) == os.path.realpath(top / GM)
        with _stale(top), pytest.raises(InvalidInputError) as got:
            mg.resolve(pub)
    finally:
        undo()
    assert not isinstance(got.value, mg.Unreadable), got.value
    assert "missing or out of date" in str(got.value), got.value


LINKED_PLACES = [(h, f) for h in (GS, GP, GM) for f in ("cases", "eml", ".build")]


@pytest.mark.parametrize(("half", "folder"), LINKED_PLACES)
def test_a_link_in_place_of_a_set_folder_is_refused_as_a_link(
    half: str, folder: str, tmp_path: Path,
) -> None:  # fmt: skip
    """A symbolic link where a half's or the view's `cases/`, `eml/` or `.build/` goes is
    refused as a link, its target never listed, even one that can't be read (review rounds
    21-22, B2; OD-705). Each step is undone only if it happened, so a failed setup never
    leaves the worker's shared tree broken or a folder pytest can't remove (round 22, P1)."""
    top = _gate_tree()
    place, aside = top / half / folder, top / half / f"{folder}.aside"
    shut = tmp_path / "shut"
    shut.mkdir()
    try:
        shut.chmod(0o000)
        place.rename(aside)
        place.symlink_to(shut)
        with pytest.raises(InvalidInputError) as got:
            mg.resolve(top / GS)
        gate.reset_cache()
        state = gate.set_state(top / GS)
    finally:  # the folder pytest must remove first, then each step only if it happened
        shut.chmod(0o700)
        if place.is_symlink():
            place.unlink()
        if aside.exists():
            aside.rename(place)
    assert not isinstance(got.value, mg.Unreadable), got.value
    assert "symbolic link" in str(got.value), got.value
    assert state.error and "symbolic link" in state.error, state


@pytest.mark.parametrize("meanwhile", ["shut", "swapped"])
def test_a_folder_changed_while_it_is_listed_is_never_read_as_absent(
    meanwhile: str, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """A card folder shut (EACCES) or swapped for another folder between its listing and the
    stat after it is refused, by name or as changed, never read as an empty folder (review
    rounds 22-23, P2 and B3: races, so the stat after the listing is made to see them)."""
    top = _gate_tree()
    target, other = top / GP / "cases", top / GP / "eml"
    real = os.lstat
    calls = [0]

    def lstat(path: Any, *a: Any, **k: Any) -> os.stat_result:
        if Path(path) == target:
            calls[0] += 1
            if calls[0] == 2:  # the stat after the listing
                if meanwhile == "shut":
                    raise PermissionError(errno.EACCES, "Permission denied", str(path))
                return real(other)
        return real(path, *a, **k)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(InvalidInputError) as got:
        mg._stats(top / GP)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.undo()
    assert calls[0] >= 2, calls  # (naming it may stat it again)
    if meanwhile == "shut":
        assert isinstance(got.value, mg.Unreadable), got.value
        assert str(got.value).startswith(f"{target}: can't be read"), got.value
    else:
        assert "changed while it was read" in str(got.value), got.value
