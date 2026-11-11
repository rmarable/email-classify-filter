"""The gate's promise, checked over every combination of its inputs (EVAL-PRIVATE.md, "The gate
guard"; Step 3 review round 3): a run counts only if it read the whole merged set of a checkout,
fresh, committed and pushed. Each case builds the set state, records a run, and compares
`gate.synthetic` with one oracle that states the promise directly; no case may pass the gate
unless the oracle says it should, and every case the oracle passes must pass."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import warnings
from collections.abc import Generator
from pathlib import Path

import pytest

from ecf.eval import gitcheck
from ecf.eval import merged as mg
from ecf_server import claude_pins, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.eval_checkout import checkout_set, git
from tests.test_gate import DIGEST

# where the gate's root is, and how it is reached: a link at the set or any folder between it and
# the checkout top, on either half, is refused (review round 6, operator decision 2026-11-06);
# a link above the top ("aliased top") is the same checkout
LINKED = ["linked set", "linked tests/eval", "linked tests", "linked eval", "linked eval/private",
          "linked private set", "linked merged set", "linked lock"]  # fmt: skip
LINK_AT = {"linked set": "tests/eval/synthetic", "linked tests/eval": "tests/eval",
           "linked tests": "tests", "linked eval": "eval", "linked eval/private": "eval/private",
           "linked private set": "eval/private/synthetic",
           "linked merged set": "eval/private/.merged/synthetic",
           "linked lock": "eval/private/.merged/synthetic.lock"}  # fmt: skip
PLACES = ["canonical", "respelled", "symlink", "aliased top", "outside", "private path",
          "merged path", "generated in private", *LINKED]  # fmt: skip
HALVES = ["both", "public only"]
FRESH = ["fresh", "stale"]
RUNS = ["counts", "no counts", "private 0", "no proof", "old version"]
GITS = ["clean", "dirty", "unpushed", "hidden edit"]


def oracle(place: str, halves: str, fresh: str, run: str, git_state: str) -> bool:
    """The promise: a checkout's own set (however its path is written), both halves, fresh,
    committed and pushed, and a run that recorded reading it (or a run from before the guard
    on the same version)."""
    return (
        place in ("canonical", "respelled", "aliased top")
        and halves == "both"
        and fresh == "fresh"
        and git_state == "clean"
        and run in ("counts", "no counts")
    )


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    top = tmp_path_factory.mktemp("tmpl")
    checkout_set(top, ("starter-bec", "starter-control"), ("starter-injection",),
                 ("starter-bec", "starter-control", "starter-injection"))  # fmt: skip
    return top


def _set_up(top: Path, halves: str, fresh: str, git_state: str) -> None:
    priv = top / "eval" / "private"
    card = priv / "synthetic" / "cases" / "starter-injection.md"
    if git_state == "hidden edit":  # round 4, B3: an edit `git status` can't see, merged in
        rel = "tests/eval/synthetic/cases/starter-bec.md"  # first, so "stale" below still holds
        git(top, "update-index", "--assume-unchanged", rel)
        (top / rel).write_text((top / rel).read_text() + "hidden\n")
        mg.refresh_merged(top / "tests" / "eval" / "synthetic")
    if fresh == "stale":  # a source changed, committed and pushed, the view not rebuilt
        card.write_text(card.read_text() + "\n")
        git(priv, "commit", "-qam", "edit")
        git(priv, "push", "-q")
    if git_state == "dirty":  # an untracked file in the set: no card, so the view stays fresh
        (priv / "synthetic" / "notes.txt").write_text("x")

    elif git_state == "unpushed":
        git(priv, "commit", "-q", "--allow-empty", "-m", "local only")
    if halves == "public only":
        shutil.rmtree(priv / "synthetic")


def _root(top: Path, tmp: Path, place: str) -> Path:  # noqa: PLR0911 - one return per place
    pub = top / "tests" / "eval" / "synthetic"
    if place == "canonical":
        return pub
    if place == "respelled":
        return top / "Tests" / "EVAL" / "Synthetic"
    if place == "symlink":  # an alias of the set folder itself: refused
        (tmp / "alias").symlink_to(pub)
        return tmp / "alias"
    if place == "generated in private":  # a generated row in the private half, committed and
        priv = top / "eval" / "private"  # pushed: the set is generated, never gate evidence (P1)
        lab = priv / "synthetic" / "labels.jsonl"
        rows = [json.loads(x) for x in lab.read_text().splitlines() if x.strip()]
        rows[0] |= {"author": "generated"}
        lab.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        git(priv, "commit", "-qam", "a generated row")
        git(priv, "push", "-q")
        mg.refresh_merged(pub)
        return pub
    if place == "aliased top":  # an alias of the whole checkout: the same checkout
        (tmp / "alias-top").symlink_to(top, target_is_directory=True)
        return tmp / "alias-top" / "tests" / "eval" / "synthetic"
    if place in LINKED:  # the folder (or lock file) moved out of the checkout, a link left
        src = top / LINK_AT[place]
        if os.path.lexists(src):
            dst = tmp / "moved" / LINK_AT[place]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            src.symlink_to(dst, target_is_directory=dst.is_dir())
        return pub
    if place == "outside":  # the public half, copied out of any checkout
        out = tmp / "out" / "synthetic"
        shutil.copytree(pub, out)
        return out
    if place == "private path":
        return top / "eval" / "private" / "synthetic"
    return top / "eval" / "private" / ".merged" / "synthetic"


def _record(conn: sqlite3.Connection, clock: FakeClock, root: Path, top: Path, run: str) -> None:
    """A complete, safe run as the gate reads it, with what it recorded per `run`."""
    try:
        version = evalrun.set_version(root)
    except Exception:
        version = "f" * 16
    try:  # the set's own references, as a run that read it would record them
        refs = evalrun.reference_counts(evalrun.read_set(root).selected(fraud_only=False))
    except Exception:
        refs = {"confirmed": 3, "fraud_guard_cases": 2}
    m: dict[str, object] = dict(refs)
    m |= {
        "unsafe": [], "fraud_cases": 2, "complete": True,
        "fraud_guard_missed": [], "fraud_guard_recall": 100.0,
        "options": {"classifier": True, "actor": True}}  # fmt: skip
    head = gitcheck.read_head(top / "eval" / "private") or "0" * 40
    if run in ("counts", "private 0", "no proof"):
        m |= {"public_cases": 2, "private_cases": 0 if run == "private 0" else 1}
        if run != "no proof":
            m |= {"merged_version": version, "private_commit": head}
    if run == "old version":
        version = "0" * 16
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, digest, set_version, created_at,"
                     " metrics, gate_passed, path) VALUES ('ce3be1df00', ?, ?, ?, ?, ?, 1, 'p')",
                     (claude_pins.PAIR["C"], DIGEST, version, now, json.dumps(m)))  # fmt: skip
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, str(root), now, actor="test")


def _cases() -> list[object]:
    """One case per set state; each records every kind of run against it in turn (round 4,
    performance B2: the state is built once, not once per run)."""
    out: list[object] = []
    for place in PLACES:
        for halves in HALVES:
            for fresh in FRESH:
                for g in GITS:
                    # a link is refused before halves, freshness or git count: only the one state
                    # where the refusal decides (round 7, performance B4)
                    if (place in LINKED or place == "generated in private") and (
                        halves, fresh, g) != ("both", "fresh", "clean"):  # fmt: skip
                        continue
                    marks = [pytest.mark.macos] if place == "respelled" else []  # APFS
                    out.append(pytest.param(place, halves, fresh, g, marks=marks,
                                            id=f"{place}|{halves}|{fresh}|{g}"))  # fmt: skip
    return out


@pytest.mark.parametrize(("place", "halves", "fresh", "git_state"), _cases())
def test_the_gate_passes_exactly_when_the_promise_holds(
    conn: sqlite3.Connection, clock: FakeClock, template: Path, tmp_path: Path,
    place: str, halves: str, fresh: str, git_state: str,
) -> None:  # fmt: skip
    if place == "respelled":
        assert sys.platform == "darwin", "respelled paths need a case-insensitive file system"
    top = tmp_path / "top"
    shutil.copytree(template, top, symlinks=True)
    priv = top / "eval" / "private"
    git(priv, "remote", "set-url", "origin", str(top / "remote.git"))
    git(priv, "fetch", "-q", "origin")
    _set_up(top, halves, fresh, git_state)
    root = _root(top, tmp_path, place)
    wrong: list[str] = []
    for run in RUNS:
        with write_tx(conn):
            conn.execute("DELETE FROM eval_runs")
        gate.reset_cache()
        gitcheck.reset_backoff()
        _record(conn, clock, root, top, run)
        got = gate.synthetic(conn, DIGEST)
        if got.ok != oracle(place, halves, fresh, run, git_state):
            wrong.append(f"{run}: ok={got.ok} ({got.detail})")
    assert not wrong, wrong


# A case-sensitive volume (review round 7, B1): `tests/Eval/<set>` is another folder there, so it
# is no checkout set, while the checkout's own `tests/eval/<set>` still counts.


@pytest.fixture(scope="module")
def cs_volume(tmp_path_factory: pytest.TempPathFactory) -> Generator[Path]:
    import subprocess  # noqa: PLC0415

    base = tmp_path_factory.mktemp("csvol")
    image, mnt = base / "cs.sparseimage", base / "mnt"
    mnt.mkdir()
    make = ["hdiutil", "create", "-quiet", "-size", "200m", "-fs", "Case-sensitive APFS",
            "-volname", "ecfcs", "-type", "SPARSE", str(base / "cs")]  # fmt: skip
    subprocess.run(make, check=True)
    subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-mountpoint", str(mnt),
                    str(image)], check=True)  # fmt: skip
    try:
        yield mnt
    finally:
        r = subprocess.run(["hdiutil", "detach", "-quiet", "-force", str(mnt)], check=False)
        if r.returncode != 0:  # reported, never masking the test's own result (round 9, B4)
            warnings.warn(f"hdiutil couldn't detach {mnt} (exit {r.returncode})", stacklevel=1)


@pytest.mark.macos
@pytest.mark.parametrize("place", ["cs canonical", "cs other case"])
def test_on_a_case_sensitive_volume_only_the_checkouts_own_set_counts(
    conn: sqlite3.Connection, clock: FakeClock, template: Path, cs_volume: Path, place: str
) -> None:
    top = cs_volume / place.replace(" ", "-")
    shutil.copytree(template, top, symlinks=True)
    priv = top / "eval" / "private"
    git(priv, "remote", "set-url", "origin", str(top / "remote.git"))
    git(priv, "fetch", "-q", "origin")
    pub = top / "tests" / "eval" / "synthetic"
    mg.refresh_merged(pub)
    root = pub
    if place == "cs other case":  # another folder, untracked, edited: never the checkout's set
        other = top / "tests" / "Eval" / "synthetic"
        shutil.copytree(pub, other)
        card = other / "cases" / "starter-bec.md"
        card.write_text(card.read_text() + "uncommitted\n")
        root = other
        assert mg.paths(root).top is None
    wrong: list[str] = []
    for run in RUNS:
        with write_tx(conn):
            conn.execute("DELETE FROM eval_runs")
        gate.reset_cache()
        gitcheck.reset_backoff()
        _record(conn, clock, root, top, run)
        got = gate.synthetic(conn, DIGEST)
        want = place == "cs canonical" and run in ("counts", "no counts")
        if got.ok != want:
            wrong.append(f"{run}: ok={got.ok} ({got.detail})")
    assert not wrong, wrong
