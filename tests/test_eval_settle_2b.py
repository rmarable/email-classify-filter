"""Lighter settling (OD-563): on a judge-labelled set a disputed category or deadline_mentioned
opens no dispute in `ecf eval agree`; `ecf eval expect` asks it only when it decides the expected
rule or safety, and otherwise leaves it off the card, unscored; the safety fields and
requires_action and requires_reply are always asked. With the judged sets of
test_eval_expect_judged (every case a notification from an automated sender)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.eval import agree as ag
from ecf.eval import expect as ex
from ecf.eval import labels as label_file
from ecf.eval import report
from ecf.eval import settle as st
from ecf.eval.cards import load_cards
from ecf.eval.results import (
    CaseResult,
    ResultFile,
    compare_endpoints,
    compare_fields,
    input_warnings,
    subset_lines,
)
from ecf_server import evalrun
from tests.test_api_keys import Env
from tests.test_eval_agree import _ids, _judge  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_expect_judged import (
    ON,
    Answers,
    Make,
    _expect,  # pyright: ignore[reportPrivateUsage]
    _fresh,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
    _rule,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_eval_generate import LABELS, run

NOTE = {"category": "notification", "sender_type": "automated"}

DEFERRED = ["category", "deadline_mentioned"]
FRAUD = {"phishing": True, "fraud_risk": "high", "payment_related": True}
_BUILT: dict[str, Path] = {}


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> Env:
    del conn  # migrated
    return Env(db_path)


@pytest.fixture
def make(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Make:
    """As test_eval_expect_judged's: the judged set for these answers, built once, copied."""

    def build(a: Answers, b: Answers) -> tuple[Path, list[str]]:
        key = repr((sorted(a.items()), sorted(b.items())))
        if key not in _BUILT:
            _BUILT[key] = _fresh(tmp_path_factory.mktemp("judged"), a, b)
        copy = tmp_path / "copy"
        shutil.copytree(_BUILT[key], copy)
        root = (copy / "set").resolve()
        return root, _ids(root)

    return build


def _card(root: Path, cid: str) -> dict[str, Any]:
    return dict({c.id: c for c in load_cards(root / "cases")}[cid].expected.labels)


def test_a_category_that_decides_the_rule_goes_to_settle(make: Make, env: Env) -> None:
    root, ids = make({}, {"0": {"category": "invoice"}})
    a = ids[0]
    assert not ag.open_disputes(root)  # agree opened nothing for it
    assert "category" not in _card(root, a)
    assert label_file.deferred(_rows(root)[a]) == DEFERRED
    counts = _expect(env, root)
    assert counts["to_settle"] == 1 and _rule(root, a) is None
    [q] = st.questions(root)
    assert [f["id"] for f in q["fields"]] == ["category"] and q["note"] == st.NOTE
    assert sorted(q["judges"]["category"]) == ["invoice", "notification"]
    st.apply(root, {a: {"category": "notification"}}, ON)
    row = _rows(root)[a]
    assert label_file.deferred(row) == DEFERRED  # settle keeps the policy
    assert label_file.source(row) == "models"  # a deciding category leaves it the judges'
    _expect(env, root)
    assert _rule(root, a) == "automated_notification"
    assert label_file.is_confirmed(_rows(root)[a], models_ok=True)


def test_a_disagreement_that_decides_nothing_stays_off_the_card(make: Make, env: Env) -> None:
    root, ids = make({"0": {"deadline_mentioned": False}}, {"0": {"deadline_mentioned": True}})
    a = ids[0]
    counts = _expect(env, root)
    assert counts["to_settle"] == 0 and counts["undecided"] == 0
    assert "deadline_mentioned" not in _card(root, a)
    assert _rule(root, a) == "automated_notification"
    assert label_file.is_confirmed(_rows(root)[a], models_ok=True)


def test_a_fraud_email_with_only_a_category_dispute_gets_its_rule(make: Make, env: Env) -> None:
    root, ids = make({"0": FRAUD}, {"0": FRAUD | {"category": "invoice"}})
    a = ids[0]
    assert label_file.is_confirmed(_rows(root)[a], models_ok=True)  # agreed at agree
    _expect(env, root)
    rows = {c.id: c for c in load_cards(root / "cases")}
    assert rows[a].expected.rule == "fraud_guard"
    assert rows[a].expected.safety.must_escalate is True
    assert "category" not in rows[a].expected.labels


def test_requires_action_is_always_asked(make: Make) -> None:
    root, ids = make({}, {"0": {"requires_action": True}})
    open_ = {d["id"]: d for d in ag.open_disputes(root)}
    assert open_[ids[0]]["fields"] == ["requires_action"]


def test_a_judge_category_with_no_valid_value_is_asked(make: Make, env: Env) -> None:
    root, _ = make({}, {"0": {"category": "martian"}})
    assert not ag.open_disputes(root)
    counts = _expect(env, root)
    assert counts["to_settle"] == 1
    [q] = st.questions(root)
    assert [f["id"] for f in q["fields"]] == ["category"]


def test_a_redecided_row_drops_an_old_deferred_value(make: Make, env: Env) -> None:
    root, ids = make({"0": {"deadline_mentioned": False}}, {"0": {"deadline_mentioned": True}})
    a = ids[0]
    _expect(env, root)
    path = root / "cases" / f"{a}.md"
    text = path.read_text(encoding="utf-8")
    edited = text.replace("requires_reply: false",
                          "requires_reply: false, deadline_mentioned: true", 1)  # fmt: skip
    assert edited != text
    path.write_text(edited, encoding="utf-8")
    ag.run(root, ON)
    assert "deadline_mentioned" not in _card(root, a)


def test_an_agree_rerun_leaves_a_row_from_before_the_policy_alone(make: Make, env: Env) -> None:
    root, _ = make({}, {})
    _expect(env, root)
    rows = label_file.read(root)
    for r in rows:  # as an older agree wrote them: no `deferred`
        r[label_file.LABELLED_BY] = {k: v for k, v in r[label_file.LABELLED_BY].items()
                                     if k != "deferred"}  # fmt: skip
    label_file.write(root, rows)
    before = {r["id"]: r["expected"] for r in label_file.read(root)}
    o = ag.run(root, ON)
    assert o.skipped == 20
    assert {r["id"]: r["expected"] for r in label_file.read(root)} == before


def test_a_rules_error_leaves_the_row_unconfirmed(make: Make, env: Env) -> None:
    root, ids = make({}, {"0": {"deadline_mentioned": True}})
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    for c in reply["cases"]:
        if c["id"] == ids[0]:
            c["variants"][0]["error"] = "boom"
    counts = ex.apply(root, reply["cases"], ON, plan=p, digest=reply["rules_digest"])
    assert counts["undecided"] == 1
    assert not label_file.is_confirmed(_rows(root)[ids[0]], models_ok=True)
    assert ex.plan(root).options.get(ids[0])  # the next expect takes it up again


def test_the_route_takes_a_thousand_cases_from_three_judges(make: Make, env: Env) -> None:
    root, _ = make({}, {})
    many = {f"none-{n:04d}": {"sender_type": ["automated", "vendor", "company"],
                              "priority": ["low", "medium", "high"],
                              "category": ["invoice", "other", "notification"],
                              "deadline_mentioned": [True, False]}
            for n in range(1000)}  # fmt: skip
    r = env.post("/v1/eval/expect", {"root": str(root), "variants": many})
    assert r.status_code == 200, r.text[:200]


def test_the_bound_and_who_settled() -> None:
    assert label_file.max_variants() == 54
    old = {"labelled_by": {"judges": ["x"], "settled": ["category"]}, "confirmed": {"on": "d"}}
    new = {"labelled_by": {"judges": ["x"], "settled": ["category"], "deferred": DEFERRED},
           "confirmed": {"on": "d"}}  # fmt: skip
    assert label_file.source(old) == "operator" and label_file.source(new) == "models"
    assert label_file.unscored(old) == ("sender_type", "priority")
    assert label_file.unscored(new) == ("sender_type", "priority", *DEFERRED)
    assert label_file.unscored({}) == ()


def _case(**kw: Any) -> evalrun.Case:
    exp = {"labels": {"category": "invoice", "deadline_mentioned": False, "phishing": False,
                      "sender_type": "vendor"}, "rule": "invoice",
           "safety": {"must_escalate": False, "must_not_hide": False}}  # fmt: skip
    return evalrun.Case("c1", Path("x.eml"), exp, True, "generated", judged=True, **kw)


def test_deferred_fields_are_scored_apart() -> None:
    got = {"category": "other", "deadline_mentioned": False, "phishing": False,
           "sender_type": "company"}  # fmt: skip
    case = _case(unscored=("sender_type", "priority", *DEFERRED), deferred=tuple(DEFERRED))
    r = evalrun.score_parts(case, got, "invoice", {"label"}, None)
    assert "category" not in r.fields and "deadline_mentioned" not in r.fields
    assert r.subset == {"category": False, "deadline_mentioned": True}
    assert set(r.expected) == {"phishing"}  # neither unscored nor deferred labels leak
    s = evalrun.summarize([r], 0)
    assert "category" not in s["per_field"] and s["category_labelled"] == 0
    assert s["label_policy"] == {"deferred": DEFERRED}
    assert s["deferred_fields"]["category"] == {"subset_n": 1, "unlabelled": 0,
                                                "subset_right": 0.0}  # fmt: skip
    assert subset_lines(s)[0].startswith("category: judges-agreed or asked subset")
    old = evalrun.score_parts(_case(), got, "invoice", {"label"}, None)
    assert "category" in old.fields and "sender_type" not in old.expected  # the leak is closed
    assert "label_policy" not in evalrun.summarize([old], 0)


def _result(run_id: str, deferred: list[str] | None, cat: bool) -> ResultFile:
    cases = [CaseResult(id=f"c{n}", correct=True, fields={"rule": True, "category": cat})
             for n in range(3)]  # fmt: skip
    summary: dict[str, object] = {"label_map": evalrun.LABEL_MAP}
    if deferred:
        summary["label_policy"] = {"deferred": deferred}
    return ResultFile(run_id=run_id, pair="p", set_version="v", created_at="t", cases=cases,
                      summary=summary)  # fmt: skip


def test_compare_leaves_deferred_fields_out_and_warns_on_mixed_policies() -> None:
    a, b = _result("a" * 32, DEFERRED, True), _result("b" * 32, None, False)
    assert [f.field for f in compare_fields(a, b)] == ["rule"]
    assert set(compare_endpoints(a, b)) == {"end_to_end"}
    assert any("different label policy" in w for w in input_warnings(a, b))
    same = _result("c" * 32, None, True)
    assert "category" in {f.field for f in compare_fields(same, b)}
    assert not any("label policy" in w for w in input_warnings(same, b))


def _refusing(base: Path, a: list[int], b: list[int]) -> tuple[Path, list[str]]:
    """A judged set where judge-a refuses the cases at `a` and judge-b those at `b`."""
    _s, root = run(base, mode="dev")
    ids = _ids(root)
    _judge(base, root, "judge-a", {i: {} if n in a else NOTE for n, i in enumerate(ids)})
    _judge(base, root, "judge-b", {i: {} if n in b else NOTE for n, i in enumerate(ids)})
    ag.run(root, ON)
    return root, ids


def _settle_all(root: Path) -> None:
    """Answer every open question with the card's notification labels."""
    want = LABELS | NOTE
    st.apply(root, {q["case"]: {f["id"]: want[f["id"]] for f in q["fields"]}
                    for q in st.questions(root)}, ON)  # fmt: skip


def test_one_judge_refusing_asks_you_category_not_the_other_judge(tmp_path: Path, env: Env) -> None:
    root, ids = _refusing(tmp_path, [], [0])
    [d] = ag.open_disputes(root)
    assert d["whole"] and set(DEFERRED) <= set(d["fields"])
    assert not set(d["fields"]) & set(label_file.UNASKED)
    assert label_file.deferred(_rows(root)[ids[0]]) == []
    _settle_all(root)
    p = ex.plan(root)
    assert ids[0] not in p.missing
    assert p.options[ids[0]]["category"] == ["notification"]  # your answer, from the card
    _expect(env, root)
    row = _rows(root)[ids[0]]
    assert _rule(root, ids[0]) == "automated_notification"
    assert "category" not in label_file.unscored(row) and label_file.source(row) == "operator"


def test_a_missing_judge_value_asks_a_deferred_field_but_not_sender_type(
    make: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge with no answer on a row agree didn't send whole (a regrade, say): a deferred field
    is asked, never set from the other judge's lone value; sender_type takes the judge that
    answered (OD-549)."""
    root, ids = make({"0": {"deadline_mentioned": True}}, {})
    named, values = ex.judge_values(root)
    del values[ids[0]][next(iter(values[ids[0]]))]  # as if it had refused this email
    monkeypatch.setattr(ex, "judge_values", lambda _root: (named, values))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    p = ex.plan(root)
    assert p.missing == {ids[0]: ["deadline_mentioned"]}  # sender_type not asked


def test_an_email_every_judge_refused_defers_nothing(tmp_path: Path, env: Env) -> None:
    root, ids = _refusing(tmp_path, [0], [0])
    assert label_file.deferred(_rows(root)[ids[0]]) == []
    assert label_file.deferred(_rows(root)[ids[1]]) == DEFERRED
    _settle_all(root)
    _expect(env, root)
    row = _rows(root)[ids[0]]
    assert "category" not in label_file.unscored(row)  # your category is scored in full
    assert label_file.source(row) == "operator"


def test_a_judge_one_for_true_is_never_a_value(make: Make, monkeypatch: pytest.MonkeyPatch) -> None:
    """A grade file's 1 is read as true (pydantic), so this guards values from anywhere else."""
    assert not label_file.valid("deadline_mentioned", 1)
    assert label_file.valid("deadline_mentioned", False)
    assert not label_file.valid("category", ["invoice"])  # unhashable: no TypeError
    root, ids = make({"0": {"deadline_mentioned": True}}, {})  # off the card: from the judges
    named, values = ex.judge_values(root)
    for v in values[ids[0]].values():
        if v["deadline_mentioned"] is True:
            v["deadline_mentioned"] = 1
    monkeypatch.setattr(ex, "judge_values", lambda _root: (named, values))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    assert ex.plan(root).missing == {ids[0]: ["deadline_mentioned"]}  # asked, never sent
    assert ag.decide([1, True], "deadline_mentioned", ()) == (False, None)


def test_a_case_the_service_did_not_evaluate_stays_confirmed(make: Make, env: Env) -> None:
    root, ids = make({}, {})
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    cases = [c for c in reply["cases"] if c["id"] != ids[2]]
    counts = ex.apply(root, cases, ON, plan=p, digest=reply["rules_digest"])
    assert counts["undecided"] == 1 and _rule(root, ids[2]) is None
    assert label_file.is_confirmed(_rows(root)[ids[2]], models_ok=True)  # as before OD-563


def _with_error(env: Env, root: Path, *cids: str) -> None:
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    for c in reply["cases"]:
        if c["id"] in cids:
            c["variants"][0]["error"] = "boom"
    ex.apply(root, reply["cases"], ON, plan=p, digest=reply["rules_digest"])


def test_a_rules_error_unconfirms_a_row_from_before_the_policy(make: Make, env: Env) -> None:
    root, ids = make({}, {})
    rows = label_file.read(root)
    for r in rows:  # as an older agree wrote them: no `deferred`
        r[label_file.LABELLED_BY] = {k: v for k, v in r[label_file.LABELLED_BY].items()
                                     if k != "deferred"}  # fmt: skip
    label_file.write(root, rows)
    _with_error(env, root, ids[0])
    assert not label_file.is_confirmed(_rows(root)[ids[0]], models_ok=True)
    assert label_file.is_confirmed(_rows(root)[ids[1]], models_ok=True)


def test_the_report_names_an_undecided_fraud_email(make: Make, env: Env) -> None:
    root, ids = make({"0": FRAUD}, {"0": FRAUD})
    kinds = report.assignments(root)
    plain = next(i for i in ids[1:] if kinds[i]["kind"] != "scam")
    _with_error(env, root, ids[0], plain)  # the ordinary one is not named
    rows = label_file.read(root)
    lines = report._expect_lines(root, report.confirmed(root), rows, [],  # pyright: ignore[reportPrivateUsage]
                                 report.assignments(root))  # fmt: skip
    [line] = [x for x in lines if x.startswith("undecided scam or regulator emails")]
    assert line.endswith(f": {ids[0]}")
    assert ", 1 in all (" in line
    given = {"r": {"regulator_claim": True}, "s": {}, "t": {"private_individual": True},
             "u": {"claims_internal": True}, "v": {"phishing": True}}  # fmt: skip
    reg: list[dict[str, Any]] = [{"id": c, "expected": {"labels": lab}} for c, lab in given.items()]
    last = {"errors": [{"id": "r", "error": "boom"}, {"id": "s", "error": "boom"},
                       {"id": "t", "error": "boom"}, {"id": "u", "error": "boom"},
                       {"id": "v", "error": ex.NOT_EVALUATED}]}  # fmt: skip
    kinds = {"s": {"kind": "scam"}, "u": {"kind": "ordinary"}}
    assert report._undecided_risky(reg, last, kinds) == ["r", "s", "u"]  # pyright: ignore[reportPrivateUsage]
    assert report._undecided_risky(reg, last, kinds, not_evaluated=True) == ["v"]  # pyright: ignore[reportPrivateUsage]


def test_a_fraud_email_the_service_skipped_is_named_apart(make: Make, env: Env) -> None:
    root, ids = make({"0": FRAUD}, {"0": FRAUD})
    p = ex.plan(root)
    reply = env.post("/v1/eval/expect", {"root": str(root), "variants": p.options}).json()
    cases = [c for c in reply["cases"] if c["id"] != ids[0]]
    counts = ex.apply(root, cases, ON, plan=p, digest=reply["rules_digest"])
    assert (counts["undecided"], counts["not_evaluated"]) == (1, 1)
    lines = report._expect_lines(root, report.confirmed(root), label_file.read(root), [])  # pyright: ignore[reportPrivateUsage]
    assert not any(x.startswith("undecided scam") for x in lines)
    [line] = [x for x in lines if x.startswith("scam or regulator emails the service didn't")]
    assert line.endswith(f": {ids[0]}")


def test_the_report_marks_subset_fields_and_counts_shares(make: Make, env: Env) -> None:
    root, ids = make({}, {"0": {"category": "invoice"}})
    _expect(env, root)
    st.apply(root, {ids[0]: {"category": "notification"}}, ON)
    _expect(env, root)
    shares = report._shares(root)  # pyright: ignore[reportPrivateUsage]
    assert shares[0].startswith("label shares on the 20 emails under the label policy")
    cat = next(x for x in shares if x.strip().startswith("category"))
    assert cat.split()[-5:] == ["19", "/", "1", "/", "0"]
    lines = report._field_lines("t", {"category": (1, 2), "phishing": (3, 4)},  # pyright: ignore[reportPrivateUsage]
                                frozenset({"category"}))  # fmt: skip
    assert lines[1].endswith("(judges-agreed or asked subset, not an accuracy)")
    assert lines[-1].split()[-1] != "" and "4" in lines[-1] and "6" not in lines[-1]
    policy = report._expect_lines(root, report.confirmed(root), label_file.read(root), [],  # pyright: ignore[reportPrivateUsage]
                                  report.assignments(root))  # fmt: skip
    assert any(x.startswith("label policy (OD-563): category, deadline_mentioned deferred on 20")
               for x in policy)  # fmt: skip


def test_calibration_leaves_out_unscored_labels() -> None:
    case = _case(unscored=("sender_type", "priority", *DEFERRED), deferred=tuple(DEFERRED))
    r = CaseResult(id="c1", correct=True)
    [(_r, lab)] = evalrun._labelled([r], [case])  # pyright: ignore[reportPrivateUsage]
    assert lab == {"phishing": False}


def test_a_rescore_and_a_claude_run_carry_the_policy_and_its_counts() -> None:
    got = {"category": "other", "deadline_mentioned": False, "phishing": False}
    case = _case(unscored=("sender_type", "priority", *DEFERRED), deferred=tuple(DEFERRED))
    under = evalrun.score_parts(case, got, "invoice", {"label"}, None)
    older = evalrun.score_parts(_case(), got, "invoice", {"label"}, None)
    s = evalrun._rescored_summary({}, [under, older])  # pyright: ignore[reportPrivateUsage]
    assert s["label_policy"] == {"deferred": DEFERRED}
    assert s["label_policy_cases"] == {"deferring": 1, "other": 1}
    assert subset_lines(s)[-1] == ("label policy: 1 cases under it, 1 not (older judged rows,"
                                   " emails a judge refused, or your own labels)")  # fmt: skip
    claude = evalrun.summarize([under], 0, complete=True, classifier=True, gate_eligible=False,
                               for_gate=False)  # as claude_eval calls it  # fmt: skip
    assert claude["label_policy"] == {"deferred": DEFERRED}
