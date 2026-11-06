"""Whether the gate's set is committed and pushed (EVAL-PRIVATE.md "The gate guard": dirty or
stale clone), and the fingerprint that says when to check again."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
    assert "timed out" in gitcheck.clean_and_pushed(top, "heldout3").detail
    for _ in range(gitcheck.BACKOFF):
        assert "retried shortly" in gitcheck.clean_and_pushed(top, "heldout3").detail
    assert len(calls) == 1  # one timeout, then no git while backing off
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
