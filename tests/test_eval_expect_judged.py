"""`ecf eval expect` on a judge-labelled set (eval plan draft 21, OD-549 proposed): sender_type and
priority are asked only when they decide the expected rule; the rule and flags come from every
combination of the judges' values, through the service's route on the starter rules; provenance
stays the judges' unless you settle an asked field. With the fake generator and judges of
test_eval_agree, and the route called as the CLI calls it."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import agree as ag
from ecf.eval import expect as ex
from ecf.eval import labels as label_file
from ecf.eval import review
from ecf.eval import settle as st
from ecf.eval.builder import build_all
from ecf.eval.cards import load_cards
from tests.test_api_keys import Env
from tests.test_eval_agree import _ids, _judge  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_generate import run

ON = date(2026, 10, 21)
NOTE = {"category": "notification", "sender_type": "automated"}


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> Env:
    del conn  # migrated
    return Env(db_path)


Answers = dict[str, dict[str, Any]]
Make = Callable[[Answers, Answers], tuple[Path, list[str]]]
_BUILT: dict[str, Path] = {}  # one judged set per pair of answers, per test worker


def _fresh(base: Path, a: Answers, b: Answers) -> Path:
    """A DEV set judged by two fakes, every case a notification; `a` and `b` change the
    judges' answers per case (index -> fields)."""
    _s, root = run(base, mode="dev")
    ids = _ids(root)
    _judge(base, root, "judge-a", {i: NOTE | a.get(str(n), {}) for n, i in enumerate(ids)})
    _judge(base, root, "judge-b", {i: NOTE | b.get(str(n), {}) for n, i in enumerate(ids)})
    ag.run(root, ON)
    return base


@pytest.fixture
def make(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Make:
    """The judged set for these answers, built once and copied for each test."""

    def build(a: Answers, b: Answers) -> tuple[Path, list[str]]:
        key = json.dumps([a, b], sort_keys=True)
        if key not in _BUILT:
            _BUILT[key] = _fresh(tmp_path_factory.mktemp("judged"), a, b)
        copy = tmp_path / "copy"
        shutil.copytree(_BUILT[key], copy)
        root = (copy / "set").resolve()
        return root, _ids(root)

    return build


def _expect(env: Env, root: Path) -> dict[str, int]:
    p = ex.plan(root)
    r = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options})
    assert r.status_code == 200, r.text
    reply = r.json()
    return ex.apply(root, reply["cases"], ON, plan=p, digest=reply["rules_digest"])


def _rows(root: Path) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in label_file.read(root)}


def _rule(root: Path, cid: str) -> str | None:
    return {c.id: c for c in load_cards(root / "cases")}[cid].expected.rule


def test_a_sender_type_that_decides_the_rule_goes_to_you_once(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "vendor"}})
    a = ids[0]
    assert label_file.counts(root)["confirmed"] == 20  # sender_type never disputed in agree
    counts = _expect(env, root)
    assert counts["to_settle"] == 1 and counts["undecided"] == 0
    assert _rule(root, a) is None and not label_file.is_confirmed(_rows(root)[a], models_ok=True)
    assert all(_rule(root, i) == "automated_notification" for i in ids[1:])
    assert label_file.counts(root)["confirmed"] == 19
    [q] = st.questions(root)
    assert q["note"] == st.NOTE and [f["id"] for f in q["fields"]] == ["sender_type"]
    assert st.apply(root, {a: {"sender_type": "automated"}}, ON) == 1
    row = _rows(root)[a]
    assert label_file.settled(row) == ["sender_type"] and label_file.source(row) == "models"
    counts = _expect(env, root)
    assert counts["to_settle"] == 0 and _rule(root, a) == "automated_notification"
    assert label_file.counts(root)["confirmed"] == 20
    assert gen_log(root)[-1]["to_settle"] == 0 and gen_log(root)[-1]["rules_digest"]


def gen_log(root: Path) -> list[dict[str, Any]]:
    from ecf.eval import generate as gen  # noqa: PLC0415

    return gen.Work(root).read(ex.LOG)


def test_an_agree_rerun_keeps_your_answer_and_asks_nothing(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "vendor"}})
    _expect(env, root)
    st.apply(root, {ids[0]: {"sender_type": "automated"}}, ON)
    o = ag.run(root, ON)
    assert o.skipped == 20 and not ag.open_disputes(root)
    card = {c.id: c for c in load_cards(root / "cases")}[ids[0]]
    assert card.expected.labels["sender_type"] == "automated"
    _expect(env, root)
    assert _rule(root, ids[0]) == "automated_notification"


def test_a_hand_edit_stays_unconfirmed_until_agree_decides_it_again(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "vendor"}})
    _expect(env, root)
    st.apply(root, {ids[0]: {"sender_type": "automated"}}, ON)
    _expect(env, root)
    path = root / "cases" / f"{ids[0]}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[ids[0]].expected.labels
    review.set_labels(path, labels | {"requires_reply": True})
    assert not build_all(root).findings
    _expect(env, root)
    assert not label_file.is_confirmed(_rows(root)[ids[0]], models_ok=True)
    with pytest.raises(InvalidInputError, match="ecf eval settle"):
        label_file.confirm(root, ids[0], ON)
    assert ag.run(root, ON).redecided == 1  # the judges' requires_reply, your sender_type kept
    row = _rows(root)[ids[0]]
    assert label_file.is_confirmed(row, models_ok=True) and _rule(root, ids[0]) is None
    assert row["expected"]["labels"]["requires_reply"] is False
    assert row["expected"]["labels"]["sender_type"] == "automated"
    assert label_file.settled(row) == ["sender_type"]
    _expect(env, root)
    assert _rule(root, ids[0]) == "automated_notification"


def test_a_regulator_answer_reaches_the_regulatory_rule_and_is_asked(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "regulator"}})
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    case = next(c for c in reply["cases"] if c["id"] == ids[0])
    by = {v["labels"]["sender_type"]: v for v in case["variants"]}
    assert by["regulator"]["rule"] == "regulatory" and by["regulator"]["safety"]["must_escalate"]
    assert by["automated"]["rule"] == "automated_notification"
    assert case["rule"] != "automated_notification"  # the card alone lacks sender_type
    assert _expect(env, root)["to_settle"] == 1


def test_a_field_no_judge_answered_validly_goes_to_you(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({"0": {"sender_type": "martian"}},
                     {"0": {"sender_type": None}, "1": {"sender_type": "martian"}})  # fmt: skip
    p = ex.plan(root)
    assert p.missing == {ids[0]: ["sender_type"]}
    assert p.options[ids[1]]["sender_type"] == ["automated"]  # the valid one only
    assert _expect(env, root)["to_settle"] == 1
    assert [d["fields"] for d in ag.open_disputes(root)] == [["sender_type"]]


def test_expect_skips_an_email_with_an_open_dispute(make: Make, env: Env, tmp_path: Path) -> None:
    root, ids = make({}, {"0": {"phishing": True}})
    p = ex.plan(root)
    assert ids[0] not in p.options and len(p.options) == 19
    _expect(env, root)
    assert [d["fields"] for d in ag.open_disputes(root)] == [["phishing"]]


def test_an_email_every_judge_refused_asks_every_field_in_one_sitting(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    first = _ids(root)[0]
    _judge(tmp_path, root, "judge-a", {first: {}})
    _judge(tmp_path, root, "judge-b", {first: {}})
    ag.run(root, ON)
    d = next(x for x in ag.open_disputes(root) if x["id"] == first)
    assert d["whole"] and {"sender_type", "priority", "phishing"} <= set(d["fields"])


def test_a_rules_error_leaves_the_case_undecided_and_never_asks(make: Make, tmp_path: Path) -> None:
    root, ids = make({}, {})
    p = ex.plan(root)
    one = ex.Plan(options={ids[0]: p.options[ids[0]]}, values=p.values)
    each = ex.combos(one.options[ids[0]])
    bad = [{"id": ids[0], "variants": [{"labels": v, "rule": None, "safety": ex.DEFAULT_SAFETY,
                                        "error": "boom"} for v in each]}]  # fmt: skip
    counts = ex.apply(root, bad, ON, plan=one)
    assert (counts["undecided"], counts["to_settle"]) == (1, 0) and not ag.open_disputes(root)
    assert gen_log(root)[-1]["errors"] == [{"id": ids[0], "error": "boom"}]


def test_expect_refuses_a_judge_file_changed_since_agree(make: Make, tmp_path: Path) -> None:
    root, _ = make({}, {})
    path = sorted((root / "grades").glob("*.json"))[0]
    path.write_text(path.read_text() + " ")
    with pytest.raises(InvalidInputError, match="changed since `ecf eval agree`"):
        ex.plan(root)


def test_the_route_takes_only_sender_type_and_priority_and_at_most_nine(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {})
    five = ["automated", "vendor", "company", "regulator", "person"]
    for variants, why in (({ids[0]: {"category": ["invoice"]}}, "may set only"),
                          ({ids[0]: {"sender_type": ["martian"]}}, "takes a list of its values"),
                          ({ids[0]: {"sender_type": five, "priority": ["low", "high"]}},
                           "at most 9 combinations")):  # fmt: skip
        r = env.post("/v1/eval/expect", {"root": str(root), "variants": variants})
        assert r.status_code == 400 and why in r.text


def test_label_refuses_judged_rows_but_not_your_own(make: Make, tmp_path: Path) -> None:
    root, ids = make({}, {})
    with pytest.raises(InvalidInputError, match="labelled by the judges"):
        label_file.confirm(root, ids[0], ON)
    rows = label_file.read(root)
    rows[1] = {k: v for k, v in rows[1].items() if k not in ("labelled_by", "confirmed")}
    label_file.write(root, rows)
    assert label_file.confirm(root, rows[1]["id"], ON)["confirmed"]


def test_a_dev_report_compares_graders_with_your_rows_only(make: Make, tmp_path: Path) -> None:
    from ecf.eval import report as rep  # noqa: PLC0415
    from ecf.eval.results import load_result  # noqa: PLC0415

    root, _ = make({}, {})
    grades = [load_result(p) for p in sorted((root / "grades").glob("*.json"))]
    text = "\n".join(rep.report(root, grades))
    assert "no confirmed labels of yours" in text and "labels: 20 emails set by the judges" in text


def test_label_review_refuses_a_judged_set(
    make: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    root, _ = make({}, {})
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "all"])
    assert isinstance(r.exception, InvalidInputError)
    assert "ecf eval settle" in r.exception.detail


def _hand_edit(root: Path, cid: str, change: dict[str, Any]) -> None:
    path = root / "cases" / f"{cid}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[cid].expected.labels
    review.set_labels(path, labels | change)
    assert not build_all(root).findings


def test_a_hand_edit_to_a_settled_field_never_keeps_the_old_rule(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "vendor"}})
    _expect(env, root)
    st.apply(root, {ids[0]: {"sender_type": "automated"}}, ON)
    _expect(env, root)
    assert _rule(root, ids[0]) == "automated_notification"
    _hand_edit(root, ids[0], {"sender_type": "vendor"})  # the settled field itself
    p = ex.plan(root)
    assert p.edited == [ids[0]] and ids[0] not in p.options  # expect leaves it to agree
    with pytest.raises(InvalidInputError, match="no open dispute"):  # refused either way
        st.apply(root, {ids[0]: {"sender_type": "automated"}}, ON)
    ag.run(root, ON)
    assert _rule(root, ids[0]) is None  # never confirmed with the rule from other labels
    _expect(env, root)
    rule = _rule(root, ids[0])  # your edited sender_type (vendor) now decides it
    assert rule is not None and rule != "automated_notification"


def test_a_reply_without_the_combinations_is_refused(make: Make, tmp_path: Path) -> None:
    root, ids = make({}, {})
    p = ex.plan(root)
    old = [{"id": i, "rule": "otherwise", "safety": ex.DEFAULT_SAFETY} for i in ids]
    with pytest.raises(InvalidInputError, match="restart it"):
        ex.apply(root, old, ON, plan=p)  # a service older than this ecf ignores `variants`


def test_a_new_judge_decides_the_rows_again(make: Make, tmp_path: Path) -> None:
    root, ids = make({}, {})
    rows = label_file.read(root)
    rows[0][label_file.LABELLED_BY] = rows[0][label_file.LABELLED_BY] | {"judges": ["x:gone"]}
    label_file.write(root, rows)
    with pytest.raises(InvalidInputError, match="not a judge of the latest"):
        ex.plan(root)
    o = ag.run(root, ON)  # the row's judges differ: decided again, not skipped
    assert o.skipped == 19 and _rows(root)[ids[0]][label_file.LABELLED_BY]["judges"] != ["x:gone"]


def test_a_fourth_judge_is_refused(make: Make, tmp_path: Path) -> None:
    root, _ = make({}, {})
    _judge(tmp_path, root, "judge-c")
    _judge(tmp_path, root, "judge-d")
    with pytest.raises(InvalidInputError, match="at most 3 judges"):
        ag.run(root, ON)


def test_a_judged_confirmation_never_survives_a_build_outside_a_generated_set(
    make: Make,
    tmp_path: Path,
) -> None:
    root, _ = make({}, {})
    old = label_file.read(root)
    fresh = [{k: v for k, v in r.items() if k not in ("confirmed", "labelled_by")} for r in old]
    kept = label_file.carry_over(old, fresh, models_ok=False)  # as `ecf eval build` writes rows
    assert not any("confirmed" in r or "labelled_by" in r for r in kept)
    assert all("labelled_by" in r for r in label_file.carry_over(old, fresh, models_ok=True))


def test_a_case_that_becomes_undecided_loses_its_flags(
    make: Make, env: Env, tmp_path: Path
) -> None:
    root, ids = make({}, {"0": {"sender_type": "regulator"}})
    path = root / "cases" / f"{ids[0]}.md"
    review.write_expected(path, "regulatory", {"must_escalate": True, "must_not_hide": True})
    assert not build_all(root).findings
    _expect(env, root)
    card = {c.id: c for c in load_cards(root / "cases")}[ids[0]]
    assert card.expected.rule is None and not card.expected.safety.must_escalate


def test_label_add_and_import_refuse_judged_rows(
    make: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    root, ids = make({}, {})
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    answers = tmp_path / "answers.json"
    one = {ids[0]: {"label": True, "how": "single"}}
    answers.write_text(json.dumps({"item": "phishing", "on": "2026-10-21", "answers": one}))
    for args in (["--review", "all", "--add"], ["--import-fixed", str(answers)]):
        r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), *args])
        assert isinstance(r.exception, InvalidInputError), args
        assert "ecf eval settle" in r.exception.detail


def test_the_report_states_what_expect_did_and_flags_other_rules(
    make: Make, env: Env, tmp_path: Path
) -> None:
    from ecf.eval import report as rep  # noqa: PLC0415

    root, _ = make({}, {"0": {"sender_type": "vendor"}})
    _expect(env, root)
    lines = rep._expect_lines(root, rep.confirmed(root), label_file.read(root), [])  # pyright: ignore[reportPrivateUsage]
    assert lines[0].endswith("1 sent to you because they decided the rule")
    assert lines[1] == "rule undecided: 0 (errors: 0)"  # the one sent to you isn't confirmed


def test_the_report_flags_runs_on_other_rules(make: Make, env: Env) -> None:
    from ecf.eval import report as rep  # noqa: PLC0415
    from ecf.eval.results import ResultFile  # noqa: PLC0415

    root, _ = make({}, {})
    _expect(env, root)
    run_ = ResultFile(run_id="r" * 32, pair="claude/claude", set_version="v", created_at="t",
                      cases=[], summary={"rules_digest": "other"})  # fmt: skip
    lines = rep._expect_lines(root, rep.confirmed(root), label_file.read(root), [run_])  # pyright: ignore[reportPrivateUsage]
    assert lines[-1].endswith("MISMATCH: run `ecf eval expect` again, or the run")


def test_label_review_with_cases_refuses_only_the_judged_rows_it_names(
    make: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    root, _ = make({}, {})
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "all",
                                 "--cases", "no-such-*"])  # fmt: skip
    assert "ecf eval settle" not in str(getattr(r.exception, "detail", r.exception))


def test_combinations_come_only_from_the_rows_own_judges(make: Make) -> None:
    root, ids = make({}, {"0": {"sender_type": "vendor"}})
    rows = label_file.read(root)
    lb = rows[0][label_file.LABELLED_BY]
    rows[0][label_file.LABELLED_BY] = lb | {"judges": [j for j in lb["judges"] if "judge-a" in j]}
    label_file.write(root, rows)
    assert ex.plan(root).options[ids[0]]["sender_type"] == ["automated"]  # judge-b's not used


def test_an_email_you_settled_whole_is_confirmed_when_decided_again(tmp_path: Path) -> None:
    from tests.test_eval_generate import LABELS  # noqa: PLC0415

    _s, root = run(tmp_path, mode="dev")
    first = _ids(root)[0]
    _judge(tmp_path, root, "judge-a", {first: {}})
    _judge(tmp_path, root, "judge-b", {first: {}})
    ag.run(root, ON)
    st.apply(root, {first: dict(LABELS)}, ON)  # every field of the whole case
    rows = label_file.read(root)
    i = next(n for n, r in enumerate(rows) if r["id"] == first)
    rows[i][label_file.LABELLED_BY] = rows[i][label_file.LABELLED_BY] | {"judges": ["x:old"]}
    label_file.write(root, rows)
    o = ag.run(root, ON)  # the judges changed: decided again, nothing left to ask
    assert (o.disputed, o.whole) == (0, 0)
    assert label_file.is_confirmed(_rows(root)[first], models_ok=True)
    assert first not in {d["id"] for d in ag.open_disputes(root)}


def test_a_hand_edit_before_settling_is_refused(make: Make) -> None:
    root, ids = make({}, {"0": {"phishing": True}})
    _hand_edit(root, ids[0], {"category": "invoice"})
    with pytest.raises(InvalidInputError, match="edited by hand"):
        st.apply(root, {ids[0]: {"phishing": False}}, ON)


def test_a_card_edited_without_a_rebuild_is_left_to_agree(make: Make) -> None:
    root, ids = make({}, {})
    path = root / "cases" / f"{ids[1]}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[ids[1]].expected.labels
    review.set_labels(path, labels | {"requires_reply": True})  # no `ecf eval build`
    assert ex.plan(root).edited == [ids[1]]


def test_a_case_the_service_skipped_is_undecided_not_reconfirmed(make: Make, env: Env) -> None:
    root, ids = make({}, {})
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    cases = [c for c in reply["cases"] if c["id"] != ids[2]]
    counts = ex.apply(root, cases, ON, plan=p)
    assert counts["undecided"] == 1 and _rule(root, ids[2]) is None
    assert gen_log(root)[-1]["errors"] == [{"id": ids[2], "error": "not evaluated by the service"}]


def test_a_crash_before_the_marks_leaves_the_dispute_open(
    make: Make, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids = make({}, {"0": {"phishing": True}})
    rows = label_file.read(root)
    rows = [{k: v for k, v in r.items() if k not in ("confirmed", "labelled_by")} for r in rows]
    label_file.write(root, rows)  # as before any agree run: no marks, no logs
    for log in ("disputes", "agreement"):
        (root / "work" / f"{log}.jsonl").unlink()

    real = ag._mark  # pyright: ignore[reportPrivateUsage]

    def crash(r: Path, provenance: dict[str, dict[str, Any]]) -> None:
        real(r, provenance)  # the row is marked; the old order had no dispute logged yet
        raise OSError("crash")

    monkeypatch.setattr(ag, "_mark", crash)
    with pytest.raises(OSError):
        ag.run(root, ON)
    monkeypatch.undo()
    assert ag.run(root, ON).skipped == 20  # the marked row is skipped: its dispute must exist
    assert ids[0] in {d["id"] for d in ag.open_disputes(root)}
    _expect(env, root)
    assert not label_file.is_confirmed(_rows(root)[ids[0]], models_ok=True)  # never laundered


def test_a_card_edited_before_the_first_agree_is_rebuilt_first(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    first = _ids(root)[0]
    path = root / "cases" / f"{first}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[first].expected.labels
    review.set_labels(path, labels | {"requires_reply": True})  # no `ecf eval build`
    every: Answers = {i: {} for i in _ids(root)}  # every case whole: agree writes no card
    _judge(tmp_path, root, "judge-a", every)
    _judge(tmp_path, root, "judge-b", every)
    ag.run(root, ON)
    from tests.test_eval_generate import LABELS  # noqa: PLC0415

    answer = dict(LABELS) | {"requires_reply": True}
    assert st.apply(root, {first: answer}, ON) == 1  # not refused as a hand edit


def test_agree_says_when_your_confirmed_email_was_edited(make: Make) -> None:
    root, ids = make({}, {})
    rows = [{k: v for k, v in r.items() if k not in ("confirmed", "labelled_by")}
            for r in label_file.read(root)]  # fmt: skip
    label_file.write(root, rows)
    label_file.confirm(root, ids[1], ON)  # two of yours
    label_file.confirm(root, ids[2], ON)
    path = root / "cases" / f"{ids[1]}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[ids[1]].expected.labels
    review.set_labels(path, labels | {"requires_reply": True})  # one edited, not rebuilt
    o = ag.run(root, ON)
    assert (o.yours_edited, o.skipped) == (1, 2)
    assert not label_file.is_confirmed(_rows(root)[ids[1]], models_ok=True)  # as the CLI says
    assert label_file.confirm(root, ids[1], ON)["confirmed"]  # `ecf eval label` can take it
    rows = label_file.read(root)
    label_file.write(root, [{k: v for k, v in r.items() if k != "confirmed"}
                            if r["id"] == ids[1] else r for r in rows])  # fmt: skip
    ag.run(root, ON)  # or the judges decide it next run
    assert label_file.judged(_rows(root)[ids[1]])


def test_settling_after_agree_stopped_before_marking_records_the_judges(
    make: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids = make({}, {"0": {"phishing": True}})
    rows = [{k: v for k, v in r.items() if k not in ("confirmed", "labelled_by")}
            for r in label_file.read(root)]  # fmt: skip
    label_file.write(root, rows)

    def stop(_root: Path, _provenance: dict[str, dict[str, Any]]) -> None:
        raise OSError("crash")  # after the logs and confirmations, before the marks

    monkeypatch.setattr(ag, "_mark", stop)
    with pytest.raises(OSError):
        ag.run(root, ON)
    monkeypatch.undo()
    st.apply(root, {ids[0]: {"phishing": False}}, ON)
    latest = [j["judge"] for j in gen_log_named(root, "agreement")[-1]["judges"]]
    assert _rows(root)[ids[0]][label_file.LABELLED_BY]["judges"] == latest and len(latest) == 2


def test_settling_with_no_agreement_entry_is_refused_before_writing(
    make: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ids = make({}, {"0": {"phishing": True}})
    rows = [{k: v for k, v in r.items() if k not in ("confirmed", "labelled_by")}
            for r in label_file.read(root)]  # fmt: skip
    label_file.write(root, rows)
    (root / "work" / "agreement.jsonl").unlink()
    card = (root / "cases" / f"{ids[0]}.md").read_text()
    with pytest.raises(InvalidInputError, match="no agreement entry"):
        st.apply(root, {ids[0]: {"phishing": False}}, ON)
    assert (root / "cases" / f"{ids[0]}.md").read_text() == card  # nothing written


def gen_log_named(root: Path, name: str) -> list[dict[str, Any]]:
    from ecf.eval import generate as gen  # noqa: PLC0415

    return gen.Work(root).read(name)


def test_agree_prints_what_it_left_and_why(make: Make, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:  # fmt: skip
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    root, ids = make({}, {"0": {"phishing": True}})  # one email waits for you
    rows = label_file.read(root)
    rows[1] = {k: v for k, v in rows[1].items() if k not in ("confirmed", "labelled_by")}
    label_file.write(root, rows)
    label_file.confirm(root, ids[1], ON)  # yours, then edited
    path = root / "cases" / f"{ids[1]}.md"
    labels = {c.id: c for c in load_cards(root / "cases")}[ids[1]].expected.labels
    review.set_labels(path, labels | {"requires_reply": True})
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    r = CliRunner().invoke(app, ["eval", "agree", "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "already confirmed: 18; still waiting for you: 1" in r.output
    assert "for you: 0 (0 whole" in r.output  # the waiting email isn't raised again
    assert "1 of your confirmed emails were edited" in r.output
