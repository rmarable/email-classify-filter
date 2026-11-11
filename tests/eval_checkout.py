"""A set the go-live gate can count, for tests: a checkout's `tests/eval/<set>` with its private
half (`eval/private/<set>`), built, confirmed, merged, committed, and the private half pushed to
a local bare remote (EVAL-PRIVATE.md, "The gate guard": only such a set counts). Every test
that needs a run for the gate builds its set here."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from ecf.eval import labels as label_file
from ecf.eval import markers
from ecf.eval import merged as mg
from ecf.eval.builder import build_all

SYNTHETIC = Path(__file__).resolve().parent / "eval" / "synthetic"
INJ_TEST = "inj-test"


def _inj_test() -> str:
    text = (SYNTHETIC / "cases" / "starter-injection.md").read_text(encoding="utf-8")
    return text.replace("id: starter-injection\n", f"id: {INJ_TEST}\n", 1)


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}  # fmt: skip
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env,
                       check=False)  # fmt: skip
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout


def checkout_set(top: Path, public: tuple[str, ...], private: tuple[str, ...],
                 confirm: tuple[str, ...] = (), *, name: str = "synthetic",
                 texts: dict[str, str] | None = None) -> Path:  # fmt: skip
    """The cards `public` and `private` (copied from the synthetic set, or made up in `texts`;
    every private one must carry an injection marker) split into a checkout's two halves,
    built, `confirm` confirmed in its card's own half, merged, committed and pushed. Returns the
    public half. `INJ_TEST` is a private card made from `starter-injection` under its own ID."""
    key = (public, private, confirm, name, tuple(sorted((texts or {}).items())))
    tmpl = _TEMPLATES.get(key)
    if tmpl is None:  # built once per worker, then copied (round 4, performance B1)
        tmpl = Path(tempfile.mkdtemp(prefix="ecf-checkout-"))
        atexit.register(shutil.rmtree, tmpl, True)
        _build(tmpl, public, private, confirm, name, texts)
        _TEMPLATES[key] = tmpl
    shutil.copytree(tmpl, top, symlinks=True, dirs_exist_ok=True)
    git(top / "eval" / "private", "remote", "set-url", "origin", str(top / "remote.git"))
    pub = top / "tests" / "eval" / name
    mg.refresh_merged(pub)  # the copies' new inodes recorded: no hashing on every check
    return pub


_TEMPLATES: dict[tuple[object, ...], Path] = {}


def _build(top: Path, public: tuple[str, ...], private: tuple[str, ...],
           confirm: tuple[str, ...], name: str, texts: dict[str, str] | None) -> Path:  # fmt: skip
    texts = {INJ_TEST: _inj_test()} | (texts or {})
    pub = top / "tests" / "eval" / name
    priv = top / "eval" / "private" / name
    for half, ids in ((pub, public), (priv, private)):
        (half / "cases").mkdir(parents=True, exist_ok=True)
        for i in ids:
            card = half / "cases" / f"{i}.md"
            if i in texts:
                card.write_text(texts[i], encoding="utf-8")
            else:
                card.write_bytes((SYNTHETIC / "cases" / f"{i}.md").read_bytes())
        assert not build_all(half).findings
    for i in confirm:
        label_file.confirm(priv if i in private else pub, i, date(2026, 10, 1))
    mg.refresh_merged(pub)
    commit(top, first=True)
    return pub


def split_set(top: Path, name: str, texts: dict[str, str], confirm: tuple[str, ...] = (),
              on: date = date(2026, 10, 1)) -> Path:  # fmt: skip
    """A set laid out as the real ones after the move, without git: each card of `texts` (ID ->
    card text) in the private half when X1 marks it (bar the pinned starter), else in the public
    half; both halves built, `confirm` confirmed in its card's own half, the merged view fresh.
    Returns the public half, the path every reader is given."""
    pub = top / "tests" / "eval" / name
    priv = top / "eval" / "private" / name
    halves = {False: pub}
    for i, text in texts.items():
        data = text.encode("utf-8")
        marked = markers.reasons(markers.front_matter(text, source=f"{i}.md"))
        private = bool(marked) and not markers.is_pinned_starter(i, data)
        half = halves.setdefault(private, priv)
        (half / "cases").mkdir(parents=True, exist_ok=True)
        (half / "cases" / f"{i}.md").write_bytes(data)
    (pub / "cases").mkdir(parents=True, exist_ok=True)
    for half in halves.values():
        assert not build_all(half).findings
    for i in confirm:
        half = priv if (priv / "cases" / f"{i}.md").is_file() else pub
        label_file.confirm(half, i, on)
    if priv in halves.values():  # no marked card: a public set alone, as before the move
        mg.refresh_merged(pub)
    return pub


def card_path(pub: Path, card_id: str) -> Path:
    """A card's source file in whichever half holds it."""
    priv = pub.parents[2] / "eval" / "private" / pub.name
    mine = priv / "cases" / f"{card_id}.md"
    return mine if mine.is_file() else pub / "cases" / f"{card_id}.md"


def settle_set(pub: Path, confirm: tuple[str, ...] = (), on: date = date(2026, 10, 1)) -> None:
    """After a test edits cards of a `split_set`: both halves built, `confirm` confirmed in its
    card's own half, the merged view refreshed (what `ecf eval build` and a confirm do)."""
    priv = pub.parents[2] / "eval" / "private" / pub.name
    for half in (pub, priv) if priv.is_dir() else (pub,):
        assert not build_all(half).findings
    for i in confirm:
        label_file.confirm(card_path(pub, i).parents[1], i, on)
    if priv.is_dir():
        mg.refresh_merged(pub)


def edit_in_place(path: Path, data: bytes) -> None:
    """Write over a merged copy where it is (read-only by design): the in-place edit the loader's
    sha256 check catches. No ecf tool writes there; the test lifts the mode itself, so the check
    it exercises is the same one an edit through any other path meets."""
    mode = path.stat().st_mode
    path.chmod(0o600)
    path.write_bytes(data)
    path.chmod(mode & 0o777)


def commit(top: Path, *, first: bool = False) -> None:
    """Commit both halves and push the private one (the first call makes the repositories)."""
    priv = top / "eval" / "private"
    if first:
        (top / ".gitignore").write_text("eval/private/\nremote.git/\n")
        git(top, "init", "-q", "-b", "main")
        (priv / ".gitignore").write_text(".merged/\n.build/\n*.tmp\n")
        git(priv, "init", "-q", "-b", "main")
    for repo in (top, priv):
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "set")
    if first:
        git(top, "init", "-q", "--bare", str(top / "remote.git"))
        git(priv, "remote", "add", "origin", str(top / "remote.git"))
        git(priv, "push", "-q", "-u", "origin", "main")
    else:
        git(priv, "push", "-q")


def settle(pub: Path) -> None:
    """After a test edits a checkout set: refresh the merged view, commit and push."""
    mg.refresh_merged(pub)
    commit(pub.parents[2])
