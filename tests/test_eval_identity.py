"""A set is recognised by what its folder is, never by how its path was typed (EVAL-PRIVATE.md,
"Finding the private set"; Step 3 review round 2, H1, L9): every letter-case spelling and a
symlink of a checkout's set find the same top, private half and merged view, and the private
and merged folders are refused however they are spelled. The file system here is APFS
(case-insensitive); on a case-sensitive one a respelled path is simply another folder."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ecf.errors import InvalidInputError
from ecf.eval import gitcheck
from ecf.eval import merged as mg
from ecf.eval.setio import canonical, is_merged, writable_root
from ecf_server import claude_eval, evalrun, gate
from ecf_server.clock import FakeClock
from tests.test_claude_eval import start as claude_start
from tests.test_eval_private_gate import DIGEST, _counts, _run, repos  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_resolver_wiring import _split_set  # pyright: ignore[reportPrivateUsage]

CASE_INSENSITIVE = os.path.exists(str(Path(__file__).parent).upper()) or os.path.exists(
    str(Path(__file__).parent).swapcase()
)


def _respell(part: str, flips: list[bool]) -> str:
    return "".join(
        c.swapcase() if f else c for c, f in zip(part, flips + [False] * len(part), strict=False)
    )


@pytest.fixture(scope="module")
def split(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert CASE_INSENSITIVE or os.uname().sysname != "Darwin", "these tests assume APFS"
    top = tmp_path_factory.mktemp("id")
    pub, _priv, _n = _split_set(top)
    repos(top)
    mg.refresh_merged(pub)
    return pub


FIXTURE = HealthCheck.function_scoped_fixture
flips = st.lists(st.booleans(), min_size=0, max_size=9)


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(a=flips, b=flips, c=flips)
def test_every_spelling_of_a_sets_path_is_the_same_set(
    split: Path, a: list[bool], b: list[bool], c: list[bool]
) -> None:
    top = split.parents[2]
    variant = top / _respell("tests", a) / _respell("eval", b) / _respell("synthetic", c)
    want = mg.paths(split)
    got = mg.paths(variant)
    assert (got.top, got.private, got.merged) == (want.top, want.private, want.merged)
    assert mg.resolve(variant) == mg.resolve(split)
    assert evalrun.logical(variant) == evalrun.logical(split)
    assert mg.membership(variant) == mg.membership(split)


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(a=flips, b=flips, c=flips)
def test_the_private_and_merged_folders_are_refused_however_spelled(
    split: Path, a: list[bool], b: list[bool], c: list[bool]
) -> None:
    top = split.parents[2]
    priv = top / _respell("eval", a) / _respell("private", b) / "synthetic"
    assert mg.is_private_root(priv)
    with pytest.raises(InvalidInputError, match="public set's path"):
        mg.resolve(priv)
    view = top / "eval" / "private" / _respell(".merged", c) / "synthetic"
    assert is_merged(view)
    with pytest.raises(InvalidInputError, match="merged eval set"):
        writable_root(view)


def test_a_symlink_to_a_checkouts_set_is_refused(split: Path, tmp_path: Path) -> None:
    """Round 6, operator decision 2026-11-06 (reverses round 2's alias rule): a set's location
    is never found through a link; an alias of the set folder is refused, not that set."""
    link = tmp_path / "alias"
    link.symlink_to(split)
    assert mg.paths(link).refused is not None and mg.paths(link).private is None
    with pytest.raises(InvalidInputError, match="symbolic link"):
        mg.resolve(link)


def test_canonical_keeps_a_missing_tail(tmp_path: Path) -> None:
    assert canonical(tmp_path / "not" / "there") == canonical(tmp_path) / "not" / "there"


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
def test_a_respelled_root_cant_pass_the_gate_without_the_private_set(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    """H1: `Tests/Eval/synthetic` once found no private half, so a for-gate run skipped the git
    check and the gate counted a public-only run. Now it is the checkout's set: a run there
    needs the set committed and pushed, and with the private half gone it is refused."""
    pub, _priv, _n = _split_set(tmp_path)
    repos(tmp_path)
    mg.refresh_merged(pub)
    gitcheck.reset_backoff()
    gate.reset_cache()
    claude_eval.reset()
    assert CASE_INSENSITIVE, "this test needs a case-insensitive file system (APFS)"
    variant = tmp_path / "Tests" / "EVAL" / "Synthetic"
    claude_start(db_path, clock, variant, preset="C")  # a for-gate run: checked, recorded
    assert (claude_eval.current() or pytest.fail()).private_commit
    claude_eval.reset()
    _run(conn, clock, variant, _counts(variant))
    assert gate.synthetic(conn, DIGEST).ok
    import shutil  # noqa: PLC0415

    shutil.rmtree(tmp_path / "eval" / "private" / "synthetic")
    gate.reset_cache()
    with pytest.raises(InvalidInputError, match="no eval/private/synthetic"):
        claude_start(db_path, clock, variant, preset="C")
    check = gate.synthetic(conn, DIGEST)
    assert not check.ok
    claude_eval.reset()


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
def test_a_respelled_path_is_canonical_when_f_getpath_fails(
    split: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 4, B7: without F_GETPATH each name's stored case comes from its folder's
    listing, so a respelled set is still its checkout's set, private half and all."""
    from ecf.eval import setio  # noqa: PLC0415

    want = canonical(split)

    def no_getpath(_p: Path) -> None:
        return None

    monkeypatch.setattr(setio, "_getpath", no_getpath)
    typed = Path(str(split).replace("tests/eval", "Tests/EVAL"))
    assert canonical(typed) == want
    assert mg.paths(typed).private is not None
