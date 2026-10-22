"""Shared seeds for the PROD pilot (eval generate plan step 4; OD-545): a seed run writes seeds
only; email runs copy them, make no seed call and no top-up, and keep that through --resume; a
mismatched or edited seed run is refused; a group the seed run has no seeds for is unmeasured,
never a refusal of the model writing emails. With the fake generator of test_eval_generate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import mix as mixmod
from ecf.eval import report as rep
from ecf.eval import tools
from ecf.eval.results import load_result
from tests.test_eval_generate import calls, fake_tool


def _seed_run(tmp_path: Path, scenario: dict[str, Any] | None = None,
              count: int = 20) -> Path:  # fmt: skip
    (tmp_path / "s").mkdir(exist_ok=True)
    tool, environ = fake_tool(tmp_path / "s", scenario or {})
    root = tmp_path / "seeds"
    gen.start(root, resume=False)
    adapter = tools.CliAdapter(tool, "fake-seeder-1", environ)
    summary = gen.Run(root, mixmod.default(), count, adapter, "dev",
                      seeds_only=True).run(resume=False)  # fmt: skip
    assert summary.state == "seeded"
    return root


def _email_run(tmp_path: Path, source: Path, name: str, scenario: dict[str, Any] | None = None,
               count: int = 20, resume: bool = False) -> tuple[gen.Summary, Path]:  # fmt: skip
    (tmp_path / name).mkdir(exist_ok=True)
    tool, environ = fake_tool(tmp_path / name, scenario or {})
    root = tmp_path / f"{name}-set"
    adapter = tools.CliAdapter(tool, f"fake-{name}-1", environ)
    if resume:
        shared = gen.read_manifest(root)["seeds_from"]
    else:
        gen.start(root, resume=False)
        shared = gen.share_seeds(root, source, "dev", count, mixmod.default(), gen.K_DEFAULT)
    run = gen.Run(root, mixmod.default(), count, adapter, "dev", shared=shared)
    return run.run(resume=True), root


def test_a_seed_run_writes_seeds_only(tmp_path: Path) -> None:
    root = _seed_run(tmp_path)
    m = gen.read_manifest(root)
    assert m["state"] == "seeded" and m["seeds_only"] and m["cases"] == {}
    assert m["seeds_sha256"] == gen.seeds_digest(root)
    assert not any(c for c in calls(tmp_path / "s") if not c["seeds"])  # no email call
    assert "a seed run: seeds only, no emails" in "\n".join(rep.report(root, []))


def test_two_models_write_from_the_same_seeds_with_no_seed_call(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    ids = {r["id"] for r in gen.Work(source).read("seeds") if r.get("id")}
    for name in ("one", "two"):
        summary, root = _email_run(tmp_path, source, name)
        assert summary.kept == 20
        assert not any(c["seeds"] for c in calls(tmp_path / name))  # never a seed call
        m = gen.read_manifest(root)
        assert m["seeds_from"]["set"] == "seeds" and set(m["kept_seeds"]) <= ids
        assert m["seeds_from"]["generator"] == "fake:fake-seeder-1"
        text = "\n".join(rep.report(root, []))
        assert "seeds written by fake:fake-seeder-1 (seed run seeds" in text


def test_shared_seeds_are_never_topped_up(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    summary, _root = _email_run(tmp_path, source, "one", {"html": True})
    assert summary.state == "short"
    # the pool only: 1.5 x each bucket's count (10 scam x 2 + 5 ordinary x 3), no top-up
    assert summary.dropped == {"HTML markup": 35}
    assert not any(c["seeds"] for c in calls(tmp_path / "one"))


def test_a_seed_writer_refusal_is_unmeasured_not_the_models(tmp_path: Path) -> None:
    source = _seed_run(tmp_path, {"refuse": ["Extortion"]})
    summary, root = _email_run(tmp_path, source, "one")
    assert "extortion" not in summary.refused
    m = gen.read_manifest(root)
    assert "extortion" in m["no_shared_seeds"]
    assert "unmeasured (the seed run has no seeds for them): " in "\n".join(rep.report(root, []))


def test_resume_keeps_the_shared_seeds(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    summary, _root = _email_run(tmp_path, source, "one", {"limit": True})
    assert summary.state == "stopped"
    summary, _root = _email_run(tmp_path, source, "one", resume=True)
    assert summary.kept == 20
    assert not any(c["seeds"] for c in calls(tmp_path / "one"))


def test_a_mismatched_or_edited_seed_run_is_refused(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    with pytest.raises(InvalidInputError, match="count differs"):
        _email_run(tmp_path, source, "a", count=30)
    with pytest.raises(InvalidInputError, match="mode differs"):
        target = tmp_path / "b-set"
        gen.start(target, resume=False)
        gen.share_seeds(target, source, "prod", 20, mixmod.default(), gen.K_DEFAULT)
    seeds = source / "work" / "seeds.jsonl"
    seeds.write_text(seeds.read_text().replace("role 0", "role zero"))
    with pytest.raises(InvalidInputError, match="seeds changed since the seed run"):
        _email_run(tmp_path, source, "c")


def test_an_email_set_is_not_a_seed_source(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    _summary, made = _email_run(tmp_path, source, "one")
    with pytest.raises(InvalidInputError, match="not a finished seed run"):
        _email_run(tmp_path, made, "two")


def test_the_seed_writer_never_grades_the_set(tmp_path: Path) -> None:
    source = _seed_run(tmp_path)
    _summary, root = _email_run(tmp_path, source, "one")
    tool, environ = fake_tool(tmp_path / "one", {})
    seeder = tools.CliAdapter(tool, "fake-seeder-1", environ)
    with pytest.raises(InvalidInputError, match="never grades its own set"):
        grading.Grading(root, seeder).run(resume=False)
    # nor does the report take a grade file the seed writer made (draft 24)
    tool2, environ2 = fake_tool(tmp_path / "s", {})
    path, _c, _d = grading.Grading(root, tools.CliAdapter(tool2, "fake-judge-2", environ2)).run(
        resume=False
    )
    assert path is not None
    g = load_result(path)
    summary: dict[str, Any] = dict(g.summary or {})
    grader: dict[str, Any] = dict(summary["grader"]) | {"model": "fake-seeder-1"}
    forged = g.model_copy(update={"summary": summary | {"grader": grader}})
    with pytest.raises(InvalidInputError, match="wrote this set or its seeds"):
        rep.report(root, [forged])


def test_grading_refuses_a_judge_of_the_seed_writers_family_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eval plan draft 24 (G2): on a PROD set whose seeds an OpenAI model wrote, an OpenAI judge
    is refused before its first call, though the generator's family differs; the shared check
    (`models.check_judge`) names the seed writer."""
    from ecf.eval import models  # noqa: PLC0415

    fams = {
        "fake-seeder-1": "openai",
        "fake-one-1": "anthropic",
        "fake-judge-1": "openai",
        "gpt-6.1-sol": "openai",
        "claude-sonnet-5-5": "anthropic",
    }

    def family(_provider: str, model: str) -> str:
        return fams[model]

    monkeypatch.setattr(models, "family", family)
    (tmp_path / "s").mkdir()
    tool, environ = fake_tool(tmp_path / "s", {})
    source = tmp_path / "seeds"
    gen.start(source, resume=False)
    gen.Run(source, mixmod.default(), 20, tools.CliAdapter(tool, "fake-seeder-1", environ),
            "prod", seeds_only=True).run(resume=False)  # fmt: skip
    (tmp_path / "one").mkdir()
    tool, environ = fake_tool(tmp_path / "one", {})
    root = tmp_path / "one-set"
    gen.start(root, resume=False)
    shared = gen.share_seeds(root, source, "prod", 20, mixmod.default(), gen.K_DEFAULT)
    gen.Run(root, mixmod.default(), 20, tools.CliAdapter(tool, "fake-one-1", environ), "prod",
            shared=shared).run(resume=True)  # fmt: skip
    (tmp_path / "judge").mkdir()
    tool, environ = fake_tool(tmp_path / "judge", {})
    judge = tools.CliAdapter(tool, "fake-judge-1", environ)
    with pytest.raises(
        InvalidInputError, match=r"the seed writer \(fake:fake-seeder-1\) and the judge"
    ):
        grading.Grading(root, judge).run(resume=False)
    log = tmp_path / "judge" / "calls.log"
    assert not log.exists() or not log.read_text()  # never called
    claude = {"mode": "prod", "generator": {"provider": "codex", "model": "gpt-6.1-sol"}}
    with pytest.raises(InvalidInputError, match="is Claude, the classifier's family"):
        models.check_judge(claude, ("claude", "claude-sonnet-5-5"))
    models.check_judge(claude | {"mode": "dev"}, ("claude", "claude-sonnet-5-5"))  # DEV allows
