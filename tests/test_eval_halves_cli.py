"""The CLI on a set with a private half (EVAL-PRIVATE.md, "The overlay"): `ecf eval build` builds
both halves and refreshes the merged view; single-ID commands write to the card's own half;
whole-set modes cover both; `--import-fixed` checks both parts before writing either."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.eval import fixed_labels
from ecf.eval import labels as label_file
from ecf.eval import merged as mg

SYN = Path(__file__).resolve().parent / "eval" / "synthetic"
runner = CliRunner()


@pytest.fixture
def pub(tmp_path: Path) -> Path:
    return make_halves(tmp_path)


def make_halves(tmp_path: Path) -> Path:
    """Two starter cards public, a marked card private, unbuilt; returns the public half."""
    p = tmp_path / "tests" / "eval" / "synthetic"
    priv = tmp_path / "eval" / "private" / "synthetic"
    for half, ids in ((p, ("starter-bec", "starter-control")), (priv, ())):
        (half / "cases").mkdir(parents=True)
        for i in ids:
            (half / "cases" / f"{i}.md").write_bytes((SYN / "cases" / f"{i}.md").read_bytes())
    # the private card: the public starter-injection card under another ID (no moving card's
    # text sits in a test, EVAL-PRIVATE.md "Releases and CI")
    text = (SYN / "cases" / "starter-injection.md").read_text(encoding="utf-8")
    (priv / "cases" / "inj-test.md").write_text(
        text.replace("id: starter-injection\n", "id: inj-test\n", 1), encoding="utf-8")  # fmt: skip
    return p


def _build(pub: Path) -> str:
    r = runner.invoke(app, ["eval", "build", "--root", str(pub)])
    assert r.exit_code == 0, r.output
    return r.output


def test_build_builds_both_halves_and_refreshes(pub: Path) -> None:
    out = _build(pub)
    assert "[public set]" in out and "[private set]" in out
    assert "commit tests/eval/synthetic and eval/private/synthetic" in out
    assert mg.resolve(pub) == mg.paths(pub).merged
    assert [r["id"] for r in label_file.read(mg.resolve(pub))] == [
        "inj-test", "starter-bec", "starter-control"]  # fmt: skip


def test_build_refuses_an_unmarked_private_card(pub: Path) -> None:
    priv = mg.paths(pub).private
    assert priv is not None
    (priv / "cases" / "starter-control.md").write_bytes(
        (SYN / "cases" / "starter-control.md").read_bytes())  # fmt: skip
    (pub / "cases" / "starter-control.md").unlink()
    r = runner.invoke(app, ["eval", "build", "--root", str(pub)])
    assert r.exit_code == 1 and "private card without an injection marker" in r.output


def test_new_case_routes_an_injection_card_to_the_private_half(pub: Path, tmp_path: Path) -> None:
    r = runner.invoke(app, ["eval", "new-case", "inj-new", "--template", "injection", "--root",
                            str(pub)])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert (tmp_path / "eval" / "private" / "synthetic" / "cases" / "inj-new.md").is_file()
    r = runner.invoke(app, ["eval", "new-case", "bec-new", "--template", "bec", "--root", str(pub)])
    assert r.exit_code == 0 and (pub / "cases" / "bec-new.md").is_file()
    r = runner.invoke(app, ["eval", "new-case", "inj-test", "--template", "bec", "--root",
                            str(pub)])  # fmt: skip
    assert r.exit_code != 0 and "already exists" in r.output


def test_new_case_leaves_the_merged_view_fresh(pub: Path) -> None:
    """Each CLI command that writes a set refreshes the merged view at its end (W1)."""
    _build(pub)
    view = mg.paths(pub).merged
    assert view is not None
    for case_id, template in (("inj-new", "injection"), ("bec-new", "bec")):
        r = runner.invoke(app, ["eval", "new-case", case_id, "--template", template, "--root",
                                str(pub)])  # fmt: skip
        assert r.exit_code == 0, r.output
        assert mg.is_fresh(mg.paths(pub)), case_id
        assert (view / "cases" / f"{case_id}.md").is_file()


def test_new_case_refuses_an_injection_card_without_a_private_half(tmp_path: Path) -> None:
    root = tmp_path / "tests" / "eval" / "synthetic"
    root.mkdir(parents=True)
    r = runner.invoke(app, ["eval", "new-case", "inj-x", "--template", "injection", "--root",
                            str(root)])  # fmt: skip
    assert r.exit_code != 0 and "private set" in r.output


def test_label_status_counts_both_halves(pub: Path) -> None:
    _build(pub)
    r = runner.invoke(app, ["eval", "label", "--status", "--root", str(pub)])
    assert r.exit_code == 0, r.output
    assert "0 of 2 cases confirmed" in r.output and "0 of 1 cases confirmed" in r.output


def _page(tmp_path: Path, answers: dict[str, object]) -> Path:
    f = tmp_path / "page.json"
    f.write_text(json.dumps({"item": "phishing", "on": "2026-11-06", "answers": answers}))
    return f


def test_import_fixed_writes_each_part_to_its_own_half(pub: Path, tmp_path: Path) -> None:
    _build(pub)
    yes = {"label": True, "how": "single"}
    page = _page(tmp_path, {"starter-bec": yes, "inj-test": yes})
    r = runner.invoke(app, ["eval", "label", "--root", str(pub), "--import-fixed", str(page)])
    assert r.exit_code == 0, r.output
    priv = mg.paths(pub).private
    assert priv is not None
    assert [x["id"] for x in fixed_labels.read(pub)] == ["starter-bec"]
    assert [x["id"] for x in fixed_labels.read(priv)] == ["inj-test"]
    assert mg.resolve(pub) == mg.paths(pub).merged  # refreshed at the end


@pytest.mark.parametrize("bad", ["an unknown id", "a bad answer"])
def test_import_fixed_with_one_bad_part_writes_neither(pub: Path, tmp_path: Path, bad: str) -> None:
    _build(pub)
    yes = {"label": True, "how": "single"}
    answers: dict[str, object] = {"starter-bec": yes, "inj-test": yes}
    if bad == "an unknown id":
        answers["nope"] = yes
    else:
        answers["inj-test"] = {"label": "maybe", "how": "single"}
    r = runner.invoke(app, ["eval", "label", "--root", str(pub), "--import-fixed",
                            str(_page(tmp_path, answers))])  # fmt: skip
    assert r.exit_code != 0
    priv = mg.paths(pub).private
    assert priv is not None
    assert fixed_labels.read(pub) == [] and fixed_labels.read(priv) == []


def test_build_all_itself_refuses_an_unmarked_private_card(pub: Path) -> None:
    """The refusal is in build_all, so `label --review`'s rebuild and any other caller get it."""
    from ecf.errors import InvalidInputError  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415

    priv = mg.paths(pub).private
    assert priv is not None
    (priv / "cases" / "plain.md").write_bytes(
        (SYN / "cases" / "starter-control.md")
        .read_bytes()
        .replace(b"id: starter-control", b"id: plain")
    )
    with pytest.raises(InvalidInputError, match="plain: private card without an injection"):
        build_all(priv)


@pytest.mark.parametrize("exc", [KeyError("id"), FileNotFoundError("labels.jsonl")])
def test_a_failed_refresh_never_masks_the_writers_result(
    pub: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    _build(pub)

    def boom(*_a: object, **_k: object) -> str:
        raise exc

    monkeypatch.setattr(mg, "refresh_merged", boom)
    assert (mg.refresh_after_write(pub) or "").startswith("note: the merged set wasn't refreshed")
    r = runner.invoke(app, ["eval", "label", "no-such-card", "--root", str(pub)])
    assert r.exit_code != 0 and "no-such-card" in str(r.exception)  # the writer's own refusal


def test_a_failed_lookup_of_the_halves_is_a_note_too(
    pub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding the halves sits inside the refresh's guard: its error is a note, never raised."""
    _build(pub)

    def boom(*_a: object, **_k: object) -> object:
        raise PermissionError("tests")

    monkeypatch.setattr(mg, "paths", boom)
    assert (mg.refresh_after_write(pub) or "").startswith("note: the merged set wasn't refreshed")


def test_review_refreshes_after_each_changed_card(
    pub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`label --review`'s rebuild refreshes the merged view after each changed card (B25)."""
    from ecf import cli_card_review  # noqa: PLC0415

    _build(pub)
    priv = mg.paths(pub).private
    assert priv is not None
    seen: list[Path] = []
    real = mg.refresh_after_write

    def spy(root: Path) -> str | None:
        seen.append(root)
        return real(root)

    monkeypatch.setattr(mg, "refresh_after_write", spy)
    for half in (pub, priv):
        cli_card_review.rebuild(half)
    assert seen == [pub, pub]  # each half's change refreshes the one logical set
    assert mg.resolve(pub) == mg.paths(pub).merged


def test_review_goes_through_both_halves(pub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`label --review`, a whole-set mode, reviews each half in turn and writes each change to
    the card's own half."""
    from ecf import cli  # noqa: PLC0415

    _build(pub)
    seen: list[Path] = []

    def review(half: Path, *_a: object, **_k: object) -> None:
        seen.append(half)

    monkeypatch.setattr(cli, "_review_cards", review)
    r = runner.invoke(app, ["eval", "label", "--review", "all", "--root", str(pub)])
    assert r.exit_code == 0, r.output
    assert seen == mg.halves(pub) and len(seen) == 2


def test_a_writers_refresh_waits_out_a_brief_read(pub: Path) -> None:
    """P9: the service holds the shared lock for a moment while it reads the set (the gate,
    status); a CLI writer's refresh waits it out rather than failing."""
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    _build(pub)
    card = (mg.paths(pub).private or pub) / "cases" / "inj-test.md"
    card.write_text(card.read_text() + "\n")
    fd = mg.take_shared(pub)
    threading.Timer(0.3, lambda: mg.release(fd)).start()  # a 0.3 s read
    t0 = time.monotonic()
    assert mg.refresh_after_write(pub) is None  # refreshed, no note
    assert 0.25 < time.monotonic() - t0 < mg.CLI_WAIT_S
    assert mg.resolve(pub) == mg.paths(pub).merged
