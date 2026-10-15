"""The step 6 per-role actor comparison script (scripts/s6_criteria.py; plan §2 item 4, §4; P10):
cases split by the role that answered, the recurrence filter, gates per role, cost per claim,
read -> answer, and the blind list with its separate key, on small synthetic result files built
with ecf's own models."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ecf.eval.results import CaseResult, ClaimRecord, ResultFile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "s6_criteria.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s6_criteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sc: Any = load()


def _case(cid: str, action: str | None, high: bool, *, read: float = 1.0,
          checks: dict[str, bool] | None = None, payment: bool = False) -> CaseResult:  # fmt: skip
    agent = "ecf-eval-actor-high-1" if high else "ecf-eval-actor"
    claim = ClaimRecord(stage="act", spawn=agent, round=1, session=1, preparing=False,
                        claimed_s=0.0, read_s=1.0, answered_s=1.0 + read, outcome="recorded",
                        high_risk=["first_time_sender"] if high else [])  # fmt: skip
    return CaseResult(id=cid, correct=True, proposal=action, claims=[claim],
                      proposal_safety=checks if checks is not None else {},
                      expected={"payment_related": payment})  # fmt: skip


def _run(rid: str, proposals: dict[str, str | None], *, usd: float = 1.0,
         actor: str = "claude-sonnet-5-5", high: str = "claude-opus-5-5",
         unsafe_card: str | None = None) -> ResultFile:  # fmt: skip
    cases = [_case(cid, act, cid.startswith("h"), payment=cid == "h2",
                   checks={"no_hide_or_send": False} if cid == unsafe_card else None)
             for cid, act in proposals.items()]  # fmt: skip
    summary: dict[str, Any] = {
        "unsafe": [], "fraud_guard_missed": [], "label_map": 2,
        "claude": {"models": {"actor": actor, "actor_high": high},
                   "groups": [{"model": actor, "source": "agent", "api_equivalent_usd": usd},
                              {"model": high, "source": "agent", "api_equivalent_usd": 2 * usd},
                              {"model": "claude-haiku-5-5", "source": "main",
                               "api_equivalent_usd": 9.0}]},
    }  # fmt: skip
    return ResultFile(
        run_id=rid * 4,
        pair="claude/claude",
        set_version="v1",
        created_at="2026-10-15T00:00:00Z",
        cases=cases,
        summary=summary,
    )


BASE = {"a1": "flag", "a2": "label:invoice", "a3": "flag", "h1": "escalate", "h2": "flag",
        "h3": None}  # fmt: skip


def test_roles_are_split_and_only_recurring_differences_count() -> None:
    pin = [_run("p1", BASE), _run("p2", BASE | {"a3": "leave"})]
    # swap: a1 differs in both runs (recurring); a3 differs only once; h1 differs in both
    swap = [_run("s1", BASE | {"a1": "leave", "h1": "flag"}),
            _run("s2", BASE | {"a1": "leave", "h1": "flag", "a3": "escalate"})]  # fmt: skip
    out = sc.criteria({"pin": pin, "swap": swap})
    actor, high = out["roles"]["actor"], out["roles"]["actor_high"]
    assert actor["arms"]["pin"]["cases"] == [3, 3] and high["arms"]["pin"]["cases"] == [3, 3]
    assert [c["card"] for c in actor["pairs"][0]["recurring"]] == ["a1"]
    rec = high["pairs"][0]["recurring"]
    assert [c["card"] for c in rec] == ["h1"] and rec[0]["x"] == ["escalate", "escalate"]
    assert rec[0]["flags"] == ["high_risk"]
    assert actor["arms"]["pin"]["within_agreement"] == {
        "runs": ["p1p1p1p1", "p2p2p2p2"],
        "n": 3,
        "same": 2,
        "share": 66.7,
    }
    assert [a["same"] for a in actor["pairs"][0]["agreement"]] == [2, 1, 1, 1]
    assert high["arms"]["pin"]["within_agreement"]["same"] == 3  # (none) agrees with (none)
    assert out["passed"] is True


def test_gates_cost_and_time_per_role() -> None:
    pin = [_run("p1", BASE, usd=0.6), _run("p2", BASE, usd=0.6)]
    bad = [_run("b1", BASE, unsafe_card="h2"), _run("b2", BASE)]
    out = sc.criteria({"pin": pin, "bad": bad})
    high = out["roles"]["actor_high"]["arms"]
    assert high["bad"]["passed"] is False and high["bad"]["gates"][0]["proposal_unsafe"] == 1
    assert out["roles"]["actor"]["arms"]["bad"]["passed"] is True  # the role it happened in
    assert out["passed"] is False
    cost = out["roles"]["actor"]["arms"]["pin"]["cost"][0]
    assert cost == {
        "model": "claude-sonnet-5-5",
        "usd_per_claim": 0.2,
        "claims": 3,
        "shared_with": [],
    }  # the main session's cost is never a role's
    assert out["roles"]["actor"]["arms"]["pin"]["read_to_answer_s"] == {
        "n": 6,
        "p50": 1.0,
        "p95": 1.0,
    }
    # one model in both roles: its cost is shared over both roles' claims, and named so
    haiku = "claude-haiku-5-5"
    same = [_run("h1", BASE, actor=haiku, high=haiku), _run("h2", BASE, actor=haiku, high=haiku)]
    c = sc.criteria({"pin": pin, "haiku": same})["roles"]["actor"]["arms"]["haiku"]["cost"][0]
    assert c["shared_with"] == ["actor_high"] and c["usd_per_claim"] == pytest.approx(0.5)


def test_a_run_without_proposal_checks_fails_its_gate_and_inputs_are_checked() -> None:
    old = _run("o1", BASE)
    old = old.model_copy(update={"cases": [c.model_copy(update={"proposal_safety": None})
                                           for c in old.cases]})  # fmt: skip
    out = sc.criteria({"old": [old, _run("o2", BASE)], "pin": [_run("p1", BASE), _run("p2", BASE)]})
    assert out["roles"]["actor"]["arms"]["old"]["passed"] is False
    with pytest.raises(sc.InvalidInputError, match="two or more arms"):
        sc.criteria({"pin": [_run("p1", BASE), _run("p2", BASE)]})
    other = _run("x1", BASE).model_copy(update={"set_version": "v2"})
    with pytest.raises(sc.InvalidInputError, match="different versions"):
        sc.criteria({"a": [_run("p1", BASE), _run("p2", BASE)], "b": [other, _run("x2", BASE)]})


def test_the_blind_list_hides_the_arms_and_the_key_names_them(tmp_path: Path) -> None:
    pin = [_run("p1", BASE), _run("p2", BASE)]
    swap = [_run("s1", BASE | {"a1": "leave", "h1": "flag"}),
            _run("s2", BASE | {"a1": "leave", "h1": "flag"})]  # fmt: skip
    files: list[str] = []
    for r in (*pin, *swap):
        path = tmp_path / f"{r.run_id}.json"
        path.write_text(r.model_dump_json())
        files.append(str(path))
    blind, key = tmp_path / "cards.json", tmp_path / "key.json"
    code = sc.main(["--arm", "pin", *files[:2], "--arm", "swap", *files[2:], "--blind",
                    str(blind), "--key", str(key), "--seed", "7", "--json"])  # fmt: skip
    assert code == 0
    cards, names = json.loads(blind.read_text()), json.loads(key.read_text())
    assert sorted(c["card"] for c in cards) == ["a1", "h1"]
    assert "pin" not in blind.read_text() and "swap" not in blind.read_text()
    for c in cards:
        k = names[str(c["n"])]
        assert {k["P"], k["Q"]} == {"pin", "swap"} and k["card"] == c["card"]
        theirs = {"a1": ("flag", "leave"), "h1": ("escalate", "flag")}[c["card"]]
        mine = c["proposals"]["P" if k["P"] == "pin" else "Q"]
        assert mine == [theirs[0], theirs[0]]
    again = tmp_path / "again.json"
    sc.main(["--arm", "pin", *files[:2], "--arm", "swap", *files[2:], "--blind", str(again),
             "--key", str(tmp_path / "k2.json"), "--seed", "7"])  # fmt: skip
    assert again.read_text() == blind.read_text()  # repeatable with a seed
    assert sc.main(["--arm", "pin", *files[:2], "--arm", "pin", *files[2:]]) == 2
