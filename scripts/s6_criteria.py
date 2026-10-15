"""The v3.0.0 step 6 per-role actor comparison (planning-docs/v3.0.0-STEP6.md §2 item 4, §4;
OD-518): the figures named before the runs, computed from the result files of two or more arms
(two `/ecf-eval` runs each) with ecf's own result models.

    uv run python scripts/s6_criteria.py --arm pin A1.json A2.json --arm swap B1.json B2.json \\
        [--blind cards.json --key key.json] [--seed N] [--json]

Each case counts in the role whose agent answered it in that run (`results.act_role`: the agent
of its recorded act claim, from `claims[].spawn`); confirmed, scored cases only. Per role and arm:
the gates of both runs (plan-level `unsafe` and fraud-guard misses, and the proposal-level checks
in that role, all 0; `results.proposal_figures`), the act claims, the cost per claim, read ->
answer p50/p95, and the arm's own run-to-run proposal agreement. Per role and pair of arms: the
proposal agreement of the four cross pairings and the recurring differences (§4 rule 2): a card
counts when both runs of X propose the same, both runs of Y propose the same, and X differs from
Y, on cards in that role in all four runs. One model is never compared across roles: the actor
roles batch their spawns differently, so only arms are compared, within a role.

A proposal is its action, with `:target` when the result recorded one (a schema label or the
card's injection target, OD-259); `(none)` when the actor failed. Cost per claim is the role's
model's `agent` requests (`summary.claude.groups`, API-equivalent US$) over the claims of every
role that ran on that model in that run (`shared_with` names the others); `null` when the run
has no cost figures.

`--blind` writes the recurring cards for the operator's blind judging (§4 rule 3): per card the
role, its flags (safety: a proposal check applies; fraud; payment; high risk: `actor_high`) and
each arm's proposals in both runs under labels `P` and `Q`, shuffled per card, the cards in
shuffled order; `--key` (required with it) gets the labels' arms, to read only after judging.
`--seed` makes the shuffle repeatable. Exit 1 when an arm fails a gate, 2 on a bad input.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from itertools import combinations, product
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval.metrics import percentile
from ecf.eval.results import (
    CaseResult,
    ResultFile,
    act_role,
    check_label_maps,
    load_result,
    proposal_figures,
)

ROLES = ("actor", "actor_high")
NONE = "(none)"  # the actor was asked and gave no valid proposal
_AGENT = re.compile(r"ecf-(?:eval-)?(.+?)(?:-[0-9]+)?")


def _counted(r: ResultFile) -> dict[str, CaseResult]:
    return {c.id: c for c in r.cases if c.confirmed and c.scored}


def proposal(c: CaseResult) -> str:
    if c.proposal is None:
        return NONE
    return c.proposal + (f":{c.proposal_target}" if c.proposal_target else "")


def in_role(r: ResultFile, role: str) -> dict[str, CaseResult]:
    """The run's counted cases its `role` answered (or failed to)."""
    return {cid: c for cid, c in _counted(r).items() if act_role(c) == role}


def _role_of_spawn(spawn: str) -> str:
    """`ecf-eval-actor-high-3` -> `actor_high`."""
    m = _AGENT.fullmatch(spawn)
    name = m.group(1) if m else spawn
    return name.removesuffix("-high").replace("-", "_") + ("_high" if name.endswith("-high")
                                                           else "")  # fmt: skip


def claims_by_role(r: ResultFile) -> dict[str, int]:
    """Every claim of the run by the role of its agent (classify and repeat claims too)."""
    out: dict[str, int] = {}
    for c in r.cases:
        for x in c.claims:
            role = _role_of_spawn(x.spawn)
            out[role] = out.get(role, 0) + 1
    return out


def cost_per_claim(r: ResultFile, role: str) -> dict[str, Any]:
    """US$ per claim for `role`'s model's agent requests, shared with the run's other roles on
    that model (module docstring)."""
    claude = cast(dict[str, Any], (r.summary or {}).get("claude") or {})
    models = cast(dict[str, str], claude.get("models") or {})
    groups = cast(list[dict[str, Any]], claude.get("groups") or [])
    model = models.get(role)
    claims = claims_by_role(r)
    sharing = sorted(x for x, m in models.items() if m == model and claims.get(x))
    usd = sum(float(g.get("api_equivalent_usd") or 0) for g in groups
              if g.get("model") == model and g.get("source") == "agent")  # fmt: skip
    has_cost = any(g.get("model") == model and g.get("source") == "agent"
                   and g.get("api_equivalent_usd") is not None for g in groups)  # fmt: skip
    n = sum(claims.get(x, 0) for x in sharing)
    return {"model": model, "usd_per_claim": round(usd / n, 4) if n and has_cost else None,
            "claims": claims.get(role, 0),
            "shared_with": [x for x in sharing if x != role]}  # fmt: skip


def read_to_answer(runs: list[ResultFile], role: str) -> dict[str, float | None]:
    """Seconds from first read to valid answer of the role's act claims, both runs pooled."""
    secs = [x.answered_s - x.read_s for r in runs for c in r.cases for x in c.claims
            if x.stage == "act" and _role_of_spawn(x.spawn) == role
            and x.read_s is not None and x.answered_s is not None]  # fmt: skip

    def p(q: float) -> float | None:
        v = percentile(secs, q)
        return None if v is None else round(v, 2)

    return {"n": len(secs), "p50": p(50), "p95": p(95)}


def agreement(a: ResultFile, b: ResultFile, role: str) -> dict[str, Any]:
    """Cards in `role` in both runs, and how many got the same proposal."""
    x, y = in_role(a, role), in_role(b, role)
    both = sorted(x.keys() & y.keys())
    same = sum(proposal(x[c]) == proposal(y[c]) for c in both)
    return {"runs": [a.run_id[:8], b.run_id[:8]], "n": len(both), "same": same,
            "share": round(100 * same / len(both), 1) if both else None}  # fmt: skip


def gates(r: ResultFile, role: str) -> dict[str, Any]:
    """One run's gates for one role: the plan-level figures (the run's) and the proposal-level
    checks over the role's cases."""
    s = r.summary or {}
    unsafe = cast(list[str], s.get("unsafe") or [])
    missed = cast(list[str], s.get("fraud_guard_missed") or [])
    p = proposal_figures(list(in_role(r, role).values()))
    failed = {k: len(v["failed"]) for k, v in (p or {}).get("checks", {}).items()}
    proposal_unsafe = len((p or {}).get("unsafe") or [])
    return {"run": r.run_id[:8], "unsafe": len(unsafe), "fraud_guard_missed": len(missed),
            "proposal_checks_recorded": p is not None, "proposal_failed": failed,
            "proposal_unsafe": proposal_unsafe,
            "passed": not unsafe and not missed and p is not None
            and not proposal_unsafe}  # fmt: skip


def flags(cases: list[CaseResult], role: str) -> list[str]:
    """Why a card weighs in the judging rule (§4 rule 4)."""
    out: list[str] = []
    if any(c.proposal_safety for c in cases):
        out.append("safety")
    exp = cases[0].expected
    if any(c.fraud for c in cases) or exp.get("fraud_risk") in ("medium", "high"):
        out.append("fraud")
    if exp.get("payment_related") is True or exp.get("bank_detail_change") is True:
        out.append("payment")
    if role == "actor_high":
        out.append("high_risk")
    return out


def recurring(x: list[ResultFile], y: list[ResultFile], role: str) -> list[dict[str, Any]]:
    """§4 rule 2: cards both runs of each arm agree on, the arms differing."""
    sets = [in_role(r, role) for r in (*x, *y)]
    common = set(sets[0]).intersection(*sets[1:])
    out: list[dict[str, Any]] = []
    for cid in sorted(common):
        px = [proposal(s[cid]) for s in sets[:2]]
        py = [proposal(s[cid]) for s in sets[2:]]
        if px[0] == px[1] and py[0] == py[1] and px[0] != py[0]:
            out.append({"card": cid, "x": px, "y": py,
                        "flags": flags([s[cid] for s in sets], role)})  # fmt: skip
    return out


def criteria(arms: dict[str, list[ResultFile]]) -> dict[str, Any]:
    if len(arms) < 2 or any(len(rs) != 2 for rs in arms.values()):
        raise InvalidInputError("give two or more arms, two result files each")
    runs = [r for rs in arms.values() for r in rs]
    versions = sorted({r.set_version for r in runs})
    if len(versions) != 1:
        raise InvalidInputError(f"the runs are on different versions of the set: {versions}")
    for a, b in combinations(runs, 2):
        check_label_maps(a, b)
    out: dict[str, Any] = {"set_version": versions[0], "roles": {}}
    for role in ROLES:
        per_arm: dict[str, Any] = {}
        for name, rs in arms.items():
            g = [gates(r, role) for r in rs]
            per_arm[name] = {
                "models": sorted({cost_per_claim(r, role)["model"] or "?" for r in rs}),
                "gates": g, "passed": all(x["passed"] for x in g),
                "cases": [len(in_role(r, role)) for r in rs],
                "cost": [cost_per_claim(r, role) for r in rs],
                "read_to_answer_s": read_to_answer(rs, role),
                "within_agreement": agreement(rs[0], rs[1], role),
            }  # fmt: skip
        pairs: list[dict[str, Any]] = []
        for (nx, x), (ny, y) in combinations(arms.items(), 2):
            pairs.append({"x": nx, "y": ny,
                          "agreement": [agreement(a, b, role) for a, b in product(x, y)],
                          "recurring": recurring(x, y, role)})  # fmt: skip
        out["roles"][role] = {"arms": per_arm, "pairs": pairs}
    out["passed"] = all(a["passed"] for r in out["roles"].values() for a in r["arms"].values())
    return out


def blind(out: dict[str, Any], seed: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The recurring cards with the arms behind shuffled labels, and the key."""
    rng = random.Random(seed)  # noqa: S311 - a shuffle for judging, not security
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for role, r in out["roles"].items():
        for pair in r["pairs"]:
            for card in pair["recurring"]:
                arms = [(pair["x"], card["x"]), (pair["y"], card["y"])]
                rng.shuffle(arms)
                shown = {"card": card["card"], "role": role, "flags": card["flags"],
                         "proposals": {"P": arms[0][1], "Q": arms[1][1]}}  # fmt: skip
                items.append((shown, {"P": arms[0][0], "Q": arms[1][0]}))
    rng.shuffle(items)
    cards = [s | {"n": i} for i, (s, _) in enumerate(items, 1)]
    key = {str(i): k | {"card": s["card"], "role": s["role"]}
           for i, (s, k) in enumerate(items, 1)}  # fmt: skip
    return cards, key


def text(out: dict[str, Any]) -> str:
    lines = [f"set version: {out['set_version']}"]
    for role, r in out["roles"].items():
        lines.append(f"{role}:")
        for name, a in r["arms"].items():
            g = "; ".join(f"{x['run']} unsafe {x['unsafe']}, fg missed"
                          f" {x['fraud_guard_missed']}, proposal unsafe {x['proposal_unsafe']}"
                          + ("" if x["proposal_checks_recorded"] else " (no proposal checks)")
                          for x in a["gates"])  # fmt: skip
            cost = ", ".join("n/a" if c["usd_per_claim"] is None else f"${c['usd_per_claim']}"
                             + (f" (shared: {', '.join(c['shared_with'])})"
                                if c["shared_with"] else "") for c in a["cost"])  # fmt: skip
            t, w = a["read_to_answer_s"], a["within_agreement"]
            lines += [f"  {name} ({', '.join(a['models'])}): cases {a['cases']}; {g}"
                      f"  {'PASS' if a['passed'] else 'FAIL'}",
                      f"    cost per claim {cost}; read->answer p50 {t['p50']} p95 {t['p95']}"
                      f" (n {t['n']}); run-to-run same {w['same']}/{w['n']}"]  # fmt: skip
        for p in r["pairs"]:
            agree = ", ".join(f"{x['same']}/{x['n']}" for x in p["agreement"])
            lines.append(f"  {p['y']} vs {p['x']}: same proposal {agree};"
                         f" recurring differences {len(p['recurring'])}")  # fmt: skip
            lines += [f"    {c['card']} [{', '.join(c['flags'])}]: {p['x']} {c['x'][0]},"
                      f" {p['y']} {c['y'][0]}" for c in p["recurring"]]  # fmt: skip
    lines.append("gates: " + ("PASS" if out["passed"] else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--arm", nargs=3, action="append", metavar=("NAME", "RUN1", "RUN2"),
                   required=True, help="an arm's name and its two result files")  # fmt: skip
    p.add_argument("--blind", type=Path, help="write the recurring cards, arms hidden")
    p.add_argument("--key", type=Path, help="with --blind: write which arm each label is")
    p.add_argument("--seed", type=int, help="with --blind: a repeatable shuffle")
    p.add_argument("--json", action="store_true", help="JSON instead of text")
    args = p.parse_args(argv)
    if args.blind and not args.key:
        p.error("--blind needs --key (the labels' arms, kept apart)")
    try:
        names = [a[0] for a in args.arm]
        if len(set(names)) != len(names):
            raise InvalidInputError("each arm needs its own name")
        out = criteria({a[0]: [load_result(Path(a[1])), load_result(Path(a[2]))]
                        for a in args.arm})  # fmt: skip
    except InvalidInputError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 2
    if args.blind:
        cards, key = blind(out, args.seed)
        args.blind.write_text(json.dumps(cards, indent=2) + "\n", encoding="utf-8")
        args.key.write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2) if args.json else text(out))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
