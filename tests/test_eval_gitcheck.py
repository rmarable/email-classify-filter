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
    (cases / ".x.md.3f9a1c2b7e4d.tmp").write_text("half")  # `.<name>.<random>.tmp`, C12
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


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_an_edit_hidden_by_an_index_flag_is_dirty(top: Path, flag: str) -> None:
    """Review round 4, B3: the content is compared with HEAD's tree, so an index flag that
    makes `git status` skip a file can't make its edit look committed."""
    p = _priv(top)
    card = sorted((p / "heldout3" / "cases").glob("*.md"))[0]
    git(p, "update-index", flag, f"heldout3/cases/{card.name}")
    card.write_text(card.read_text() + "edited\n")
    assert subprocess.run(["git", "-C", str(p), "status", "--porcelain"], capture_output=True,
                          text=True, check=True).stdout == ""  # fmt: skip
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and card.name in got.detail


def test_the_repositorys_status_options_dont_change_the_check(top: Path) -> None:
    """Review round 4, B4: no `git status`, so `status.showUntrackedFiles=no` neither breaks
    the check nor hides a new card."""
    p = _priv(top)
    git(p, "config", "status.showUntrackedFiles", "no")
    assert gitcheck.clean_and_pushed(top, "heldout3").ok
    (p / "heldout3" / "cases" / "new.md").write_text("x")
    gitcheck.reset_backoff()
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and "new.md" in got.detail


@pytest.mark.parametrize("name", ["checks.yaml", "reviews.jsonl", "notes.md"])
def test_the_fingerprint_follows_every_set_file(top: Path, name: str) -> None:
    """Review round 4, B3: the fingerprint lists the clean check's own files, so an edit to
    any of them makes the gate check again."""
    seen = gitcheck.fingerprint(top, "heldout3")
    f = _priv(top) / "heldout3" / name
    f.write_text((f.read_text() if f.is_file() else "") + "x\n")
    assert gitcheck.fingerprint(top, "heldout3") != seen


@pytest.mark.parametrize("what", ["file", "folder", "labels"])
def test_a_committed_symbolic_link_in_a_set_is_never_clean(
    top: Path, tmp_path_factory: pytest.TempPathFactory, what: str
) -> None:
    """Round 5, B-1: a link's committed text matches its blob, but readers would follow it to
    content no commit holds, so any link in a set is dirty, committed and pushed or not."""
    out = tmp_path_factory.mktemp("outside")
    p = _priv(top)
    s = p / "heldout3"
    if what == "file":
        (out / "c.md").write_text("x")
        (s / "cases" / "linked.md").symlink_to(out / "c.md")
    elif what == "folder":
        (out / "e").mkdir()
        (s / "eml").mkdir(exist_ok=True)
        (s / "eml" / "sub").symlink_to(out / "e", target_is_directory=True)
    else:
        (out / "labels.jsonl").write_bytes((s / "labels.jsonl").read_bytes())
        (s / "labels.jsonl").unlink()
        (s / "labels.jsonl").symlink_to(out / "labels.jsonl")
    git(p, "add", "-A")
    git(p, "commit", "-q", "-m", "a link")
    git(p, "push", "-q")
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and "uncommitted" in got.detail


def test_a_pipe_in_a_set_is_dirty_and_never_blocks(top: Path) -> None:
    import os  # noqa: PLC0415

    card = next((_priv(top) / "heldout3" / "cases").glob("*.md"))
    card.unlink()
    os.mkfifo(card)  # a committed card's path: the check compares it, never reading a pipe
    got = gitcheck.clean_and_pushed(top, "heldout3")  # an open that waited would hang here
    assert not got.ok and card.name in got.detail


def test_the_fingerprint_follows_a_rewrite_that_restores_the_mtime(top: Path) -> None:
    """Round 5, B-4: same size, same inode, mtime put back; only ctime moves."""
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415

    card = next((top / "tests" / "eval" / "heldout3" / "cases").glob("*.md"))
    st = card.stat()
    before = gitcheck.fingerprint(top, "heldout3")
    time.sleep(0.01)
    with card.open("r+b") as f:
        data = f.read()
        f.seek(0)
        f.write(data[:-1] + (b"y" if data[-1:] != b"y" else b"z"))
    os.utime(card, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert card.stat().st_mtime_ns == st.st_mtime_ns and card.stat().st_size == st.st_size
    assert gitcheck.fingerprint(top, "heldout3") != before


def test_the_private_head_is_read_once_per_check(
    top: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 5, B-3: the commit compared, checked pushed and recorded is one commit."""
    calls: list[Path] = []
    real = gitcheck.read_head

    def counting(t: Path) -> str | None:
        calls.append(t)
        return real(t)

    monkeypatch.setattr(gitcheck, "read_head", counting)
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert got.ok
    assert sum(1 for c in calls if c == _priv(top)) == 1


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
def test_a_private_clone_stored_in_another_case_is_no_private_half(top: Path) -> None:
    """Round 9: the clean check and the fingerprint use the one rule `merged.paths` uses."""
    (top / "eval" / "private").rename(top / "eval" / "Private")
    got = gitcheck.clean_and_pushed(top, "heldout3")
    assert not got.ok and got.no_private
    assert gitcheck.fingerprint(top, "heldout3")[3] is None


def test_a_looped_gitdir_is_unreadable_never_raised(tmp_path: Path) -> None:
    """A `.git` file whose `gitdir:` is a symbolic link loop: the checks say the repository
    can't be read; nothing raises out of the gate (Python 3.12's resolve() raised; Step 7)."""
    (tmp_path / "tests" / "eval" / "syn").mkdir(parents=True)
    (tmp_path / "eval" / "private" / "syn").mkdir(parents=True)
    (tmp_path / ".git").write_text(f"gitdir: {tmp_path}/loop\n")
    (tmp_path / "loop").symlink_to("loop")
    got = gitcheck.clean_and_pushed(tmp_path, "syn", fresh=True)
    assert not got.ok and got.error and "can't be read" in got.detail, got
    assert gitcheck.read_head(tmp_path) is None
    assert isinstance(gitcheck.fingerprint(tmp_path, "syn"), tuple)
