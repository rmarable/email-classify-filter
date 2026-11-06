"""Whether the gate's set is committed and pushed (EVAL-PRIVATE.md "The gate guard": dirty or
stale clone), and the fingerprint that says when to check again."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import gitcheck
from tests.test_eval_private_gate import git, repos
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def top(tmp_path: Path) -> Path:
    _split_set(tmp_path, "heldout3", 4)
    repos(tmp_path)
    gitcheck.reset_backoff()
    return tmp_path


def _priv(top: Path) -> Path:
    return top / "eval" / "private"


def test_a_committed_and_pushed_set_is_clean_and_names_its_commit(top: Path) -> None:
    got = gitcheck.clean_and_pushed(top, "heldout3")
    head = git(_priv(top), "rev-parse", "HEAD").strip()
    assert got.ok and got.private_commit == head and head[:8] in got.detail


@pytest.mark.parametrize("where", ["public", "private"])
def test_an_edit_or_a_new_card_is_dirty(top: Path, where: str) -> None:
    root = top / "tests" / "eval" / "heldout3" if where == "public" else _priv(top) / "heldout3"
    card = next((root / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    assert not gitcheck.clean_and_pushed(top, "heldout3").ok
    git(card.parent.parent.parent if where == "private" else top, "checkout", "--", ".")
    (root / "cases" / "new.md").write_text("x")
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and "new.md" in got.detail


def test_an_ignored_card_is_dirty_but_temporary_files_are_not(top: Path) -> None:
    cases = _priv(top) / "heldout3" / "cases"
    (cases / ".x.md.tmp").write_text("half")
    (cases / ".DS_Store").write_text("finder")
    assert gitcheck.clean_and_pushed(top, "heldout3").ok
    (_priv(top) / ".gitignore").write_text((_priv(top) / ".gitignore").read_text() + "hid.md\n")
    git(_priv(top), "commit", "-q", "-am", "ignore a card")
    git(_priv(top), "push", "-q")
    (cases / "hid.md").write_text("x")
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and "hid.md" in got.detail


def test_changes_outside_the_set_dont_count(top: Path) -> None:
    (top / "README.md").write_text("x")
    assert gitcheck.clean_and_pushed(top, "heldout3").ok


def test_an_unpushed_commit_a_detached_head_or_no_upstream_is_not_clean(top: Path) -> None:
    p = _priv(top)
    git(p, "commit", "-q", "--allow-empty", "-m", "local")
    assert "not pushed" in gitcheck.clean_and_pushed(top, "heldout3").detail
    git(p, "push", "-q")
    assert gitcheck.clean_and_pushed(top, "heldout3").ok
    git(p, "checkout", "-q", "--detach")
    assert "no upstream" in gitcheck.clean_and_pushed(top, "heldout3").detail
    git(p, "checkout", "-q", "main")
    git(p, "branch", "-q", "--unset-upstream")
    assert "no upstream" in gitcheck.clean_and_pushed(top, "heldout3").detail


def test_an_upstream_that_is_a_local_branch_is_not_clean(top: Path) -> None:
    p = _priv(top)
    git(p, "branch", "-q", "other")
    git(p, "branch", "-q", "--set-upstream-to=other")
    assert "isn't a remote branch" in gitcheck.clean_and_pushed(top, "heldout3").detail


def test_a_reftable_repository_is_refused(top: Path) -> None:
    cfg = _priv(top) / ".git" / "config"
    cfg.write_text(cfg.read_text() + "[extensions]\n\trefStorage = reftable\n")
    assert "reftable" in gitcheck.clean_and_pushed(top, "heldout3").detail


def test_git_failing_is_not_clean_and_a_timeout_backs_off(
    top: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gitcheck, "GIT", "/nonexistent/git")
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and got.error
    calls: list[list[str]] = []

    def slow(cmd: list[str], **_kw: object) -> None:
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(gitcheck, "_run_git", slow)
    now = [1000.0]
    monkeypatch.setattr(gitcheck, "_now", lambda: now[0])
    assert "timed out" in gitcheck.clean_and_pushed(top, "heldout3").detail
    for _ in range(20):  # however often it is asked within the backoff: no git
        now[0] += gitcheck.BACKOFF_S / 25
        assert "retried shortly" in gitcheck.clean_and_pushed(top, "heldout3").detail
    assert len(calls) == 1  # one timeout, then no git while backing off
    now[0] += gitcheck.BACKOFF_S
    gitcheck.clean_and_pushed(top, "heldout3")
    assert len(calls) == 2


def test_the_argv_reads_only_the_repositorys_own_config() -> None:
    a = gitcheck.argv(Path("/r"), "status")
    assert a[:3] == ["/usr/bin/git", "-C", "/r"]
    for flag in ("--no-optional-locks", "core.fsmonitor=false", "core.hooksPath=/dev/null",
                 "core.attributesFile=/dev/null"):  # fmt: skip
        assert flag in a


def test_the_fingerprint_follows_a_commit_a_push_a_new_card_and_an_edit(top: Path) -> None:
    p = _priv(top)
    seen = gitcheck.fingerprint(top, "heldout3")
    git(p, "commit", "-q", "--allow-empty", "-m", "c")
    assert gitcheck.fingerprint(top, "heldout3") != seen
    seen = gitcheck.fingerprint(top, "heldout3")
    git(p, "push", "-q")
    assert gitcheck.fingerprint(top, "heldout3") != seen
    seen = gitcheck.fingerprint(top, "heldout3")
    (p / "heldout3" / "cases" / "n.md").write_text("x")
    assert gitcheck.fingerprint(top, "heldout3") != seen
    seen = gitcheck.fingerprint(top, "heldout3")
    card = next((top / "tests" / "eval" / "heldout3" / "cases").glob("*.md"))
    with card.open("r+") as f:  # in place: no folder or index time changes
        f.write("-")
    assert gitcheck.fingerprint(top, "heldout3") != seen
    seen = gitcheck.fingerprint(top, "heldout3")
    git(p, "pack-refs", "--all")
    assert gitcheck.fingerprint(top, "heldout3") != seen


def test_the_fingerprint_reads_a_worktrees_git_file(top: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    git(top, "worktree", "add", "-q", "--detach", str(wt))
    dirs = gitcheck.git_dir(wt)
    assert dirs is not None and dirs[1] == (top / ".git").resolve()
    seen = gitcheck.fingerprint(wt, "heldout3")
    git(wt, "commit", "-q", "--allow-empty", "-m", "w")
    assert gitcheck.fingerprint(wt, "heldout3") != seen


def _changes(top: Path, priv: Path, set_name: str) -> None:
    """A commit, a push, a new card, an in-place edit and an upstream change in the private
    clone's config each change the fingerprint of the checkout at `top`."""
    seen = gitcheck.fingerprint(top, set_name)
    steps: list[tuple[str, Any]] = [
        ("commit", lambda: git(priv, "commit", "-q", "--allow-empty", "-m", "c")),
        ("push", lambda: git(priv, "push", "-q")),
        ("new card", lambda: (priv / set_name / "cases" / "n.md").write_text("x")),
        ("in-place edit", lambda: _edit_in_place(top / "tests" / "eval" / set_name)),
        ("upstream", lambda: git(priv, "config", "branch.main.merge", "refs/heads/other")),
    ]
    for name, step in steps:
        step()
        now = gitcheck.fingerprint(top, set_name)
        assert now != seen, name
        seen = now


def _edit_in_place(pub: Path) -> None:
    card = next((pub / "cases").glob("*.md"))
    with card.open("r+") as f:  # in place: no folder or index time changes
        f.write("-")


def test_the_fingerprint_follows_every_change_in_a_normal_clone(top: Path) -> None:
    _changes(top, _priv(top), "heldout3")


def test_the_fingerprint_follows_every_change_in_a_worktree(top: Path, tmp_path: Path) -> None:
    """C7 in a worktree: the public checkout is a `git worktree`, its private set a clone of
    the private repository with the same upstream."""
    wt = tmp_path / "wt"
    git(top, "worktree", "add", "-q", "--detach", str(wt))
    priv = wt / "eval" / "private"
    git(top, "clone", "-q", "-b", "main", str(top / "remote.git"), str(priv))
    assert (priv / "heldout3").is_dir()
    _changes(wt, priv, "heldout3")
    pub_remote = tmp_path / "pub.git"
    git(top, "init", "-q", "--bare", str(pub_remote))
    git(wt, "remote", "add", "pub", str(pub_remote))
    for name, step in (
        ("a commit in the worktree", lambda: git(wt, "commit", "-q", "--allow-empty", "-m", "w")),
        ("a push from the worktree", lambda: git(wt, "push", "-q", "pub", "HEAD:refs/heads/w")),
    ):
        seen = gitcheck.fingerprint(wt, "heldout3")
        step()
        assert gitcheck.fingerprint(wt, "heldout3") != seen, name


@pytest.mark.parametrize("name", ["fixed_labels.jsonl", "labels.jsonl", "checks.yaml",
                                  "reviews.jsonl", "notes.md"])  # fmt: skip
def test_an_ignored_set_file_is_dirty(top: Path, name: str) -> None:
    """M4: an ignored label or check file defines the set's version, so a local ignore rule
    can't make it look committed; only `.build/`, `*.tmp` and `.DS_Store` are left out."""
    p = _priv(top)
    f = p / "heldout3" / name
    f.write_text(f.read_text() + "\n" if f.is_file() else "{}\n")
    git(p, "rm", "-q", "--cached", "--ignore-unmatch", f"heldout3/{name}")
    git(p, "commit", "-q", "-m", "untracked now", "--allow-empty")
    git(p, "push", "-q")
    (p / ".git" / "info" / "exclude").write_text(f"heldout3/{name}\n")
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and name in got.detail


def test_build_files_tmp_files_and_ds_store_stay_clean(top: Path) -> None:
    p = _priv(top) / "heldout3"
    (p / ".build").mkdir(exist_ok=True)
    (p / ".build" / "x.eml").write_text("x")
    (p / ".DS_Store").write_text("x")
    (p / ".labels.jsonl.tmp").write_text("x")
    gitcheck.reset_backoff()
    assert gitcheck.clean_and_pushed(top, "heldout3").ok


def test_an_oddly_named_uncommitted_card_is_reported_by_its_real_name(top: Path) -> None:
    (_priv(top) / "heldout3" / "cases" / 'a "quoted"é.md').write_text("x")
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and 'a "quoted"é.md' in got.detail


@pytest.mark.parametrize("packed", [False, True])
def test_the_private_head_is_read_from_the_ref_files(top: Path, packed: bool) -> None:
    """P3: HEAD from its files (loose ref or packed-refs), equal to what git says."""
    p = _priv(top)
    if packed:
        git(p, "pack-refs", "--all")
    assert gitcheck.read_head(p) == git(p, "rev-parse", "HEAD").strip()
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert got.ok and got.private_commit == gitcheck.read_head(p)
