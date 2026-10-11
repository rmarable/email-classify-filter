"""`ecf eval routes` (pre-classifier plan step 7b, §3a Check (a), §11): what the OD-490 routes do
on the synthetic set and the held-out batches, without running a model.

Per set, each confirmed card's facts are computed as a run computes them (the scratch, then the
card's expected facts), and the routes are taken three ways:
- `baseline`: no credential, gift-card-code or crypto route (before step 4);
- `draft2`: each of the three as a fraud reason with no second signal (plan §3, draft 2): a
  credential request from outside unless the text says such a request is never made (from
  inside, weak), gift-card codes from any origin, a crypto payment from outside unless the
  same veto holds; over today's keyword lists;
- `draft4`: as built (`triggers.routes`).
The known-sender DMARC reason (`known_sender_auth_regressed`) is the same in all three. For each
it reports the cards a route makes fraud or weak, the rule the facts alone reach (every field at
its least risky value) and `model_only_fraud_cases`; for draft 4 also each fraud route's second
signals, the cards whose only signal is a sender with no history, and a sensitivity pass with
`sender_seen_before` forced true (a compromised known sender, R3-P10).

On a corpus (§3a Check (b)), each stored message's routes are recomputed from its stored facts
with its keywords taken again: the fetch-time ones (the whole message, the lists of the day)
joined with a scan of its stored unredacted excerpts under today's lists (truncated, so a phrase
past the excerpt is missed). It reports how many messages a route makes fraud (would escalate),
how many of those nothing else already escalated, the share whose signals include a sender with
no history, and the labelled-harmless ones among them; message indexes and counts only.

Per recorded result (on the first set): each case is planned again as `ecf eval rescore
--through-policy` does, and every case whose rule or actions differ from the recorded ones is
attributed by taking one mechanism out at a time (the routes, the DMARC reason, the risky
attachment) and seeing whether the outcome moves. A checksum-valid IBAN is only marked present:
it feeds trigger 1 through the keywords, as do the plan's Unicode changes, and neither can be
taken out after the facts are computed; a change no mechanism moves is reported unexplained.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval.results import CaseResult, load_result
from ecf.schema import load_schema
from ecf_server import corpus_session, evalrun, policy, rules, ruletest, triggers
from ecf_server.clock import Clock
from ecf_server.facts import PUBLIC_DOMAINS

PUBLIC = PUBLIC_DOMAINS | {ruletest.FREEMAIL}
VARIANTS = ("baseline", "draft2", "draft4")
ROUTE_WHATS = triggers.ROUTE_WHATS


def _is_route(reason: str) -> bool:
    return reason.startswith(ROUTE_WHATS)


def without(found: dict[str, Any], drop: Callable[[str], bool],
            facts: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """`found` with the trigger reasons `drop` picks taken out of `fraud` and `fraud_weak`, and
    `facts` laid over."""
    t: dict[str, Any] = dict(found.get("triggers") or {})
    for k in ("fraud", "fraud_weak"):
        t[k] = [r for r in t.get(k, []) if not drop(r)]
    return found | (facts or {}) | {"triggers": t}


def draft2_routes(found: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The three routes as draft 2 had them: fraud reasons with no second signal."""
    if found.get("self_sent"):
        return [], []
    kw: dict[str, list[str]] = found.get("keywords") or {}
    inside = bool(found.get("from_org_domain") or found.get("from_org_address"))
    never = bool(kw.get("credential_never"))
    fraud: list[str] = []
    weak: list[str] = []
    if kw.get("credential") and kw.get("credential_ask") and not never:
        (weak if inside else fraud).append(f"{triggers.CREDENTIAL} (draft 2)")
    if triggers.gift_card_codes_request(kw):
        fraud.append(f"{triggers.GIFT_CODES} (draft 2)")
    if kw.get("crypto_wallet") and kw.get("crypto_pay") and not never and not inside:
        fraud.append(f"{triggers.CRYPTO} (draft 2)")
    return fraud, weak


def variant(found: dict[str, Any], name: str) -> dict[str, Any]:
    """`found` (draft 4, as computed) with its routes taken as `name` has them."""
    if name == "draft4":
        return found
    out = without(found, _is_route)
    fraud, weak = draft2_routes(found) if name == "draft2" else ([], [])
    t: dict[str, Any] = out["triggers"]
    t["fraud"] = t["fraud"] + fraud
    t["fraud_weak"] = t["fraud_weak"] + weak
    return out | {"routes": {"fraud": fraud, "fraud_weak": weak}}


def _route_reasons(found: dict[str, Any]) -> tuple[list[str], list[str]]:
    t: dict[str, Any] = found.get("triggers") or {}
    return ([r for r in t.get("fraud", []) if _is_route(r)],
            [r for r in t.get("fraud_weak", []) if _is_route(r)])  # fmt: skip


def scam(case: evalrun.Case) -> bool:
    safety: dict[str, Any] = case.expected.get("safety") or {}
    return case.expected.get("rule") in evalrun.FRAUD_RULES or bool(safety.get("must_escalate"))


class _Env:
    def __init__(self, clock: Clock) -> None:
        self.schema = load_schema()
        self.rules = rules.load_starter_rules(self.schema)
        self.known = policy.labels(self.schema, self.rules)
        self.scratch = ruletest.Scratch(clock)

    def facts(self, case: evalrun.Case, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = self.scratch.analyze(ruletest.read_case(case.path.read_bytes()), case.profile)
        expected: dict[str, Any] = case.expected.get("facts") or {}
        return ruletest.with_expected(raw, expected | (extra or {}))

    def alone(self, case: evalrun.Case, facts: dict[str, Any]) -> str:
        return evalrun.rules_alone(case, facts, self.rules, self.known, self.schema)


def _ids(xs: list[str]) -> list[str]:
    return sorted(xs)


def set_report(env: _Env, root: Path) -> dict[str, Any]:
    """One set's routes under the three variants (module docstring)."""
    cases, version = evalrun.load(root, fraud_only=False)
    counted = [c for c in cases if c.confirmed]
    per: dict[str, dict[str, Any]] = {v: {"fraud": {"scam": [], "genuine": []},
                                          "weak": {"scam": [], "genuine": []},
                                          "alone_fraud_guard": {"scam": [], "genuine": []},
                                          "alone_fraud_weak": {"scam": [], "genuine": []},
                                          "model_only_fraud": []} for v in VARIANTS}  # fmt: skip
    signals: dict[str, int] = {}
    first_time_only: list[str] = []
    seen: dict[str, Any] = {"lost_route": [], "model_only_fraud": [], "weak": []}
    for case in counted:
        side = "scam" if scam(case) else "genuine"
        found = env.facts(case)
        for v in VARIANTS:
            f = variant(found, v)
            fraud, weak = _route_reasons(f)
            if fraud:
                per[v]["fraud"][side].append(case.id)
            elif weak:
                per[v]["weak"][side].append(case.id)
            alone = env.alone(case, f)
            if alone in evalrun.FRAUD_RULES:
                per[v][f"alone_{alone}"][side].append(case.id)
            if case.expected.get("rule") == "fraud_guard" and alone != "fraud_guard":
                per[v]["model_only_fraud"].append(case.id)
        hits = [h for h in triggers.route_hits(found, public_domains=PUBLIC) if h.signals]
        for h in hits:
            for s in h.signals:
                signals[s] = signals.get(s, 0) + 1
        if hits and all(h.signals == (triggers.NO_HISTORY,) for h in hits):
            first_time_only.append(case.id)
        forced = env.facts(case, {"sender_seen_before": True})
        fraud_f, weak_f = _route_reasons(forced)
        if _route_reasons(found)[0] and not fraud_f:
            seen["lost_route"].append(case.id)
        if weak_f and not fraud_f:
            seen["weak"].append(case.id)
        if case.expected.get("rule") == "fraud_guard" and env.alone(case, forced) != "fraud_guard":
            seen["model_only_fraud"].append(case.id)
    return {
        "set": str(root), "set_version": version, "confirmed": len(counted),
        "scam": sum(scam(c) for c in counted), "unconfirmed": len(cases) - len(counted),
        "variants": per, "draft4_signals": dict(sorted(signals.items())),
        "first_time_only": _ids(first_time_only), "seen_forced": seen,
    }  # fmt: skip


MECHANISMS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "routes": lambda f: without(f, _is_route),
    "known_sender_auth_regressed": lambda f: without(
        f, lambda r: r == triggers.AUTH_REGRESSED, {"known_sender_auth_regressed": False}),
    "risky_attachment": lambda f: without(
        f, lambda r: r == triggers.RISKY_ATTACHMENT, {"risky_attachment": False}),
}  # fmt: skip


def result_report(env: _Env, root: Path, path: Path) -> dict[str, Any]:
    """One recorded result planned again on today's facts, each changed case attributed."""
    old = load_result(path)
    cases, _ = evalrun.load(root, fraud_only=False)
    by_id = {c.id: c for c in cases}
    counts: dict[str, int] = {"recorded": 0, "inferred": 0, "partial": 0, "nothing": 0,
                              "not_on_record": 0}  # fmt: skip
    changed: list[dict[str, Any]] = []
    out: list[CaseResult] = []
    tally: dict[str, int] = {m: 0 for m in (*MECHANISMS, "unexplained")}

    def outcome(c: evalrun.Case, r: CaseResult, facts: dict[str, Any]) -> tuple[Any, list[str]]:
        one, _ = evalrun.replan(c, r, facts, env.rules, env.known, env.schema, dict(counts))
        return one.got.get("rule"), sorted(one.actions)

    for r in old.cases:
        if (case := by_id.get(r.id)) is None:
            continue
        facts = env.facts(case)
        one, _ = evalrun.replan(case, r, facts, env.rules, env.known, env.schema, counts)
        out.append(one)
        now = (one.got.get("rule"), sorted(one.actions))
        if now == (r.got.get("rule"), sorted(r.actions)):
            continue
        moved = [m for m, take in MECHANISMS.items() if outcome(case, r, take(facts)) != now]
        for m in moved or ["unexplained"]:
            tally[m] += 1
        kw: dict[str, list[str]] = facts.get("keywords") or {}
        changed.append({"id": r.id, "rule": [r.got.get("rule"), now[0]],
                        "actions": [sorted(r.actions), now[1]], "mechanisms": moved,
                        "iban_bank": triggers.IBAN_HIT in (kw.get("bank") or [])})  # fmt: skip
    before = (old.summary or {}).get("model_only_fraud_cases")
    genuine = evalrun.genuine_metrics(out, cases, env.rules)
    return {
        "result": path.name, "run_id": old.run_id, "changed": changed, "by_mechanism": tally,
        "actor": counts,
        "model_only_fraud_cases": [before, sum(c.model_only_fraud for c in out if c.confirmed)],
        "genuine_hide_dropped": genuine["genuine_hide_dropped"],
        "unsafe": sorted(c.id for c in out if c.confirmed and c.scored and not c.safety),
    }  # fmt: skip


def corpus_facts(row: dict[str, Any]) -> dict[str, Any]:
    """A stored message's facts with today's routes: its keywords joined with a scan of its
    stored unredacted excerpts, the routes computed again (`triggers.with_routes`)."""
    stored: dict[str, Any] = dict(row.get("facts") or {})
    plain: dict[str, str] = row.get("unredacted") or {}
    now = triggers.scan([plain.get("classifier") or "", plain.get("actor") or ""])
    was: dict[str, list[str]] = stored.get("keywords") or {}
    kw = {g: sorted(set(was.get(g) or []) | set(now.get(g) or [])) for g in was.keys() | now.keys()}
    base: dict[str, Any] = {"auth_result": "none", "recipient_mismatch": False,
                            "reply_to_mismatch": False, "triggers": {}}  # fmt: skip
    found = base | stored | {"keywords": kw}
    prior: dict[str, Any] = found["triggers"] or {}
    t: dict[str, Any] = dict(prior)
    t.setdefault("fraud", [])
    t.setdefault("fraud_weak", [])
    return triggers.with_routes(found | {"triggers": t}, public_domains=PUBLIC_DOMAINS)


def corpus_report(session: corpus_session.Session) -> dict[str, Any]:
    """The routes on a corpus's stored messages (module docstring)."""
    run = evalrun.corpus_run(session)
    per: dict[str, dict[str, list[str]]] = {w: {"fraud": [], "weak": []} for w in ROUTE_WHATS}
    escalate: list[str] = []
    only_route: list[str] = []
    first_time: list[str] = []
    first_time_only: list[str] = []
    harmless: list[str] = []
    for case, row in zip(run.cases, session.rows, strict=True):
        found = corpus_facts(row)
        hits = triggers.route_hits(found, public_domains=PUBLIC_DOMAINS)
        for h in hits:
            if h.signals:
                per[h.what]["fraud"].append(case.id)
            elif h.weak_too:
                per[h.what]["weak"].append(case.id)
        fired = [h for h in hits if h.signals]
        if not fired:
            continue
        escalate.append(case.id)
        if all(triggers.OTHER_FRAUD not in h.signals for h in fired):
            only_route.append(case.id)
        if any(triggers.NO_HISTORY in h.signals for h in fired):
            first_time.append(case.id)
        if all(h.signals == (triggers.NO_HISTORY,) for h in fired):
            first_time_only.append(case.id)
        lab: dict[str, Any] = case.expected.get("labels") or {}
        if case.confirmed and lab.get("fraud_risk") == "none" and not lab.get("payment_related"):
            harmless.append(case.id)
    n = len(run.cases)

    def share(xs: list[str], of: int) -> float | None:
        return round(100 * len(xs) / of, 1) if of else None

    return {
        "corpus_id": run.corpus_id, "messages": n,
        "labelled": sum(c.confirmed for c in run.cases),
        "routes": per, "would_escalate": escalate,
        "would_escalate_pct": share(escalate, n),
        "escalated_only_by_a_route": only_route,
        "escalated_only_by_a_route_pct": share(only_route, n),
        "first_time": first_time, "first_time_pct": share(first_time, len(escalate)),
        "first_time_only": first_time_only, "labelled_harmless": harmless,
    }  # fmt: skip


def report(roots: list[Path], results: list[Path], clock: Clock,
           corpus: corpus_session.Session | None = None) -> dict[str, Any]:  # fmt: skip
    """`ecf eval routes`: each set's routes, then each result's attribution on the first set,
    then a corpus's routes."""
    if not roots and corpus is None:
        raise InvalidInputError("give a synthetic set or a corpus")
    if results and not roots:
        raise InvalidInputError("--result needs the synthetic set it ran on")
    env = _Env(clock)
    try:
        return {"sets": [set_report(env, r) for r in roots],
                "results": [result_report(env, roots[0], p) for p in results],
                "corpus": corpus_report(corpus) if corpus is not None else None}  # fmt: skip
    finally:
        env.scratch.close()
