"""Eval result files (metrics, per-case correctness and the model's field values; never message
text) and the paired comparison `ecf eval compare` prints (SPEC §16.2, §16.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ecf.errors import InvalidInputError
from ecf.eval.metrics import holm, mcnemar_exact, newcombe_paired, percentile, wilson
from ecf.schema import load_schema

_STRICT = ConfigDict(extra="forbid", frozen=True)
# one comparison rule (SPEC §16.5, OD-486): Newcombe's paired score interval against this margin,
# the classifier plan's gate margin (Q8); a caller may pass another
MARGIN_POINTS = 5.0
# what a run was asked with besides the email: a difference is printed, the comparison still runs
INPUT_KEYS = {"prompt_digest": "different prompt", "input_format": "different inputs",
              "excerpt_cap": "different inputs", "excerpt_prep": "different inputs",
              "keywords_digest": "different keywords",
              "rules_digest": "different rules"}  # fmt: skip
HOLM_ALPHA = 0.05  # family-wise, over the per-field tests (SPEC §16.5)


class CallMetrics(BaseModel):
    """One model call's counts from Ollama (or a decision model's input tokens and time): numbers
    only, never text (I5). None where the backend doesn't report a figure."""

    model_config = _STRICT
    prompt_tokens: int | None = None  # including cached
    cached_tokens: int | None = None
    output_tokens: int | None = None
    prompt_ns: int | None = None
    eval_ns: int | None = None
    load_ns: int | None = None


ClaimStage = Literal["classify", "act", "repeat"]
ClaimOutcome = Literal["recorded", "invalid", "claim_expired", "model_refused", "session_ended",
                       "run_ended"]  # fmt: skip
# why an act claim went to the high-risk actor: ecf_server.policy.HighRiskReason's values (§8.2)
HighRiskReason = Literal["high_sensitivity", "first_time_sender", "fraud_signal", "payment"]


class ClaimRecord(BaseModel):
    """One claim of a Claude eval case (v2.2.0 timing plan item 3): closed values, counters and
    service-side times in seconds from the run's registration (`summary.claude.registered_at`);
    never content. None where the claim ended before that moment."""

    model_config = _STRICT
    stage: ClaimStage
    spawn: str  # the label `eval_next` gave it; unique within its session and round
    round: int  # the run's rounds that handed out items, from 1, across sessions
    session: int  # the session's ordinal, from 1
    preparing: bool  # the run was still preparing cases
    claimed_s: float
    read_s: float | None = None  # the first `get_message`
    answered_s: float | None = None  # the valid submission
    settled_s: float | None = None  # the held submission applied or refused
    outcome: ClaimOutcome | None = None
    high_risk: list[HighRiskReason] = Field(default_factory=list[HighRiskReason])  # act claims


class CaseResult(BaseModel):
    model_config = _STRICT
    id: str
    correct: bool  # end-to-end decision correct
    fields: dict[str, bool] = Field(default_factory=dict[str, bool])  # per-field correctness
    confirmed: bool = True  # counts toward the gates only when its labels are confirmed (V1.3)
    safety: bool = True
    # what the model returned: schema field values and the rule they led to; closed-vocabulary
    # values only, never text (OD-259); empty in older files
    got: dict[str, str | bool | None] = Field(default_factory=dict[str, str | bool | None])
    fraud: bool = False  # expects fraud_guard/fraud_weak or an escalation; False in older files
    fraud_guard: bool = False  # expects fraud_guard (recall, §16.5); False in older files
    # a decision model's per-field probabilities (SPEC §7.8; calibration only, never routing)
    probabilities: dict[str, dict[str, float]] | None = None
    classifier_ms: int | None = None  # the classifier call's wall time (§7.8 latency rule)
    # what the plan did, by action name, the actor's proposal included (R53, R138): so a corpus
    # result can be re-scored against new labels without running the models again
    actions: list[str] = Field(default_factory=list[str])
    scored: bool = True  # False: no labels to score against (an unlabelled corpus message, R157)
    # the case's successful classifier and actor calls (plan 0.3); None when the role failed or
    # didn't run, and in older files
    classifier: CallMetrics | None = None
    actor: CallMetrics | None = None
    # expects fraud_guard, which the rules alone (every field at its least risky value) don't
    # reach: only the model's answer can route it there (plan 0.3, S5); False in older files
    model_only_fraud: bool = False
    # the rule the case's facts reach with no model (every field at its least risky value), so
    # `genuine_flagged` can tell the rules' share from the model's (pre-classifier plan step 7)
    rule_alone: str | None = None
    # the actor's proposal by action name, and its target only when it is one of the schema's
    # labels or the case's injection target (closed vocabulary, OD-259): so a rescore through the
    # policy replays it (pre-classifier plan step 7); None when the actor wasn't asked or failed
    proposal: str | None = None
    proposal_target: str | None = None
    # the hide blockers (policy.hide_blockers' fixed names) that refused the plan's hide actions;
    # empty when nothing was refused for one, and in older files (pre-classifier plan step 7, M7)
    hide_blocked: list[str] = Field(default_factory=list[str])
    # a Claude eval case's claims, in order (v2.2.0); empty for a local run and in older files
    claims: list[ClaimRecord] = Field(default_factory=list[ClaimRecord])


class ResultFile(BaseModel):
    model_config = _STRICT
    run_id: str
    pair: str
    set_version: str
    created_at: str
    cases: list[CaseResult]
    digest: str | None = None  # the local model's manifest digest (V1.3)
    summary: dict[str, object] | None = None  # metrics (V1.3); never message or model text


def load_result(path: Path) -> ResultFile:
    try:
        return ResultFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidInputError(f"{path}: not a valid result file: {exc}") from exc


@dataclass(frozen=True)
class Comparison:
    n: int
    a_correct: int
    b_correct: int
    b_only: int  # B right, A wrong
    a_only: int  # A right, B wrong
    p_value: float
    diff_points: float
    # Newcombe's paired score interval, 95% (§16.5, OD-486); unlike Wald it doesn't collapse to
    # [0, 0] without discordant pairs
    diff_ci: tuple[float, float]

    def b_non_inferior(self, margin: float = MARGIN_POINTS) -> bool:
        """The interval's lower bound is above -`margin` points."""
        return self.diff_ci[0] > -margin


def compare(a: ResultFile, b: ResultFile) -> Comparison:
    """Paired comparison of B against A on the confirmed cases both ran and scored (SPEC §16.5,
    R157)."""
    if a.set_version != b.set_version:
        raise InvalidInputError(f"different sets: {a.set_version} vs {b.set_version}")
    bm = {c.id: c.correct for c in b.cases if _counts(c)}
    pairs = [(c.correct, bm[c.id]) for c in a.cases if _counts(c) and c.id in bm]
    if not pairs:
        raise InvalidInputError("no cases in common")
    n = len(pairs)
    a_only = sum(x and not y for x, y in pairs)
    b_only = sum(y and not x for x, y in pairs)
    diff = (b_only - a_only) / n
    both = sum(x and y for x, y in pairs)
    lo, hi = newcombe_paired(both, b_only, a_only, n - both - b_only - a_only)
    return Comparison(
        n=n,
        a_correct=sum(x for x, _ in pairs),
        b_correct=sum(y for _, y in pairs),
        b_only=b_only,
        a_only=a_only,
        p_value=mcnemar_exact(b_only, a_only),
        diff_points=100 * diff,
        diff_ci=(100 * lo, 100 * hi),
    )


def compare_endpoints(a: ResultFile, b: ResultFile) -> dict[str, tuple[float, float]]:
    """The decision-model experiment's confirmatory tests (§7.8, R17): exact McNemar on end-to-end
    correctness and on category, each with its Holm-adjusted p-value over the two."""
    e2e = compare(a, b).p_value
    cat = next((f.p_value for f in compare_fields(a, b) if f.field == "category"), 1.0)
    adjusted = holm({"end_to_end": e2e, "category": cat})
    return {"end_to_end": (e2e, adjusted["end_to_end"]), "category": (cat, adjusted["category"])}


@dataclass(frozen=True)
class FieldComparison:
    field: str
    n: int
    b_only: int
    a_only: int
    p_value: float
    p_holm: float

    @property
    def significant(self) -> bool:
        return self.p_holm < HOLM_ALPHA


def compare_fields(a: ResultFile, b: ResultFile) -> list[FieldComparison]:
    """Per-field exact McNemar on the cases both ran, Holm-adjusted over the fields (SPEC
    §16.5, secondary). A field counts on a confirmed case only when both runs scored it there."""
    bm = {c.id: c.fields for c in b.cases if _counts(c)}
    pairs: dict[str, list[tuple[bool, bool]]] = {}
    for c in a.cases:
        other = bm.get(c.id) if _counts(c) else None
        if other is None:
            continue
        for name, ok in c.fields.items():
            if name in other:
                pairs.setdefault(name, []).append((ok, other[name]))
    raw: dict[str, tuple[int, int, int, float]] = {}
    for name, ps in pairs.items():
        a_only = sum(x and not y for x, y in ps)
        b_only = sum(y and not x for x, y in ps)
        raw[name] = (len(ps), b_only, a_only, mcnemar_exact(b_only, a_only))
    adjusted = holm({k: v[3] for k, v in raw.items()})
    return [FieldComparison(k, n, bo, ao, p, adjusted[k])
            for k, (n, bo, ao, p) in sorted(raw.items())]  # fmt: skip


def schema_digest(r: ResultFile) -> str | None:
    """The digest of the schema the run's models were asked with (its extension included); None
    in a result from before v2.0.0."""
    d = (r.summary or {}).get("schema_digest")
    return d if isinstance(d, str) else None


def schema_warning(a: ResultFile, b: ResultFile) -> str | None:
    """A warning when the two runs were asked with different schemas (an extension added,
    changed or removed between them): the fields and values they chose from differ. A result
    that records none is taken as the shipped schema (it predates extensions)."""
    shipped = load_schema().digest
    da, db = schema_digest(a) or shipped, schema_digest(b) or shipped
    if da == db:
        return None
    return (f"warning: the runs used different schemas ({da} vs {db}): an extension changed"
            " between them, so the fields and values they chose from differ")  # fmt: skip


def scoring(r: ResultFile) -> str:
    """How a result's rules and actions were reached: `live` (the run's own plan), `policy`
    (`ecf eval rescore --through-policy`: facts recomputed, the recorded answers planned again)
    or `recorded` (an older rescore that kept the recorded rule and actions)."""
    s = r.summary or {}
    if isinstance(v := s.get("scoring"), str):
        return v
    return "recorded" if "rescored_from" in s else "live"


def switches(r: ResultFile) -> list[str]:
    """The eval-only switches a run had off (`summary.options.switches_off`), sorted."""
    opts = (r.summary or {}).get("options")
    off = cast(dict[str, Any], opts).get("switches_off") if isinstance(opts, dict) else None
    items: list[Any] = cast(list[Any], off) if isinstance(off, list) else []
    return sorted(str(x) for x in items)


def _set_changed(r: ResultFile) -> bool:
    rescored = cast(dict[str, object], (r.summary or {}).get("rescored_from") or {})
    return rescored.get("set_changed") is True


def input_warnings(a: ResultFile, b: ResultFile) -> list[str]:
    """ "different prompt", "different inputs", "different keywords" or "different rules" when
    both runs recorded a value and they differ (OD-486), and "different scoring" when one result's
    rules and actions were planned again and the other's weren't (pre-classifier plan step 7):
    the difference isn't only the model's."""
    out: list[str] = []
    if (sa := scoring(a)) != (sb := scoring(b)):
        out.append(f"warning: different scoring ({sa} vs {sb})")
    if _set_changed(a) or _set_changed(b):  # pre-gate review G5
        out.append("warning: a result was planned again on a set whose labels changed since"
                   " its run")  # fmt: skip
    if (wa := switches(a)) != (wb := switches(b)):  # pre-gate review G3
        out.append(f"warning: different switches ({', '.join(wa) or 'all on'} vs"
                   f" {', '.join(wb) or 'all on'})")  # fmt: skip
    for key, what in INPUT_KEYS.items():
        va, vb = (a.summary or {}).get(key), (b.summary or {}).get(key)
        if va is not None and vb is not None and va != vb:
            line = f"warning: {what} ({key} {str(va)[:16]} vs {str(vb)[:16]})"
            if line not in out:
                out.append(line)
    return out


def latest(folder: Path) -> ResultFile | None:
    """The newest readable result file in `folder` (an install's `evals`), or None."""
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            return load_result(path)
        except InvalidInputError:
            continue
    return None


def differences(expected: Mapping[str, object], got: Mapping[str, str | bool | None]) -> list[str]:
    """Where the model's answer differs from a case's expected labels and rule."""
    want: dict[str, object] = dict(cast(Mapping[str, object], expected.get("labels") or {}))
    if expected.get("rule") is not None:
        want["rule"] = expected["rule"]
    return [f"{k} {_show(got.get(k))} (expected {_show(v)})"
            for k, v in want.items() if k in got and got[k] != v]  # fmt: skip


def _show(v: object) -> str:
    return str(v).lower() if isinstance(v, bool) or v is None else str(v)


def summary(r: ResultFile) -> str:
    """The A/B headline: confirmed, scored cases only (R157)."""
    counted = [c for c in r.cases if _counts(c)]
    k, n = sum(c.correct for c in counted), len(counted)
    if not n:
        return f"{r.pair}: no confirmed cases to score"
    lo, hi = wilson(k, n)
    return f"{r.pair}: {k}/{n} correct ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f}-{100 * hi:.1f})"


def _counts(c: CaseResult) -> bool:
    return c.confirmed and c.scored


# where a local model call's time goes (pre-classifier plan §11, perf-3), from each case's
# recorded call figures, so it works on any result since plan 0.3 without running a model
COLD_LOAD_NS = 1_000_000_000  # a load over this is a cold start, left out (ecf_server.stats)
CACHE_SHORT = 0.9  # a call reused less than this share of the run's longest cached prefix


@dataclass(frozen=True)
class LatencySplit:
    role: str
    calls: int  # successful calls with timings, cold starts left out
    prompt_s: tuple[float, float]  # reading the prompt: median, p95
    generation_s: tuple[float, float]  # writing the answer: median, p95
    generation_share: float  # writing's share of reading + writing, summed over the calls
    uncached_tokens: tuple[int, int, int]  # median, p95, max
    output_tokens: tuple[int, int]  # median, p95


@dataclass(frozen=True)
class CacheShort:
    """Classifier calls whose cached prefix fell short, after an actor call or otherwise."""

    after: str  # "an actor call" or "other"
    calls: int
    short: int
    prompt_s: float | None  # the short calls' prompt time, median


def latency_split(r: ResultFile) -> list[LatencySplit]:
    """Prompt and generation time per role (classifier, actor); empty for an older result."""
    out: list[LatencySplit] = []
    for role in ("classifier", "actor"):
        ms = [m for c in r.cases if (m := getattr(c, role)) is not None and _timed(m)]
        if not ms:
            continue
        prompt = [cast(int, m.prompt_ns) / 1e9 for m in ms]
        gen = [cast(int, m.eval_ns) / 1e9 for m in ms]
        uncached = [_uncached(m) for m in ms]
        output = [m.output_tokens or 0 for m in ms]
        out.append(LatencySplit(
            role, len(ms), _p(prompt), _p(gen), sum(gen) / (sum(prompt) + sum(gen) or 1),
            (_pi(uncached, 50), _pi(uncached, 95), max(uncached)),
            (_pi(output, 50), _pi(output, 95))))  # fmt: skip
    return out


def cache_short(r: ResultFile) -> list[CacheShort]:
    """Classifier calls that reused less than CACHE_SHORT of the run's longest cached prefix
    (the shared system prompt), split by whether the case before made an actor call, whose own
    prompt can push the classifier's out of Ollama's cache (SPEC §1.2 "Grouped model calls")."""
    return _cache_short(r.cases)


def _cache_short(cases: list[CaseResult]) -> list[CacheShort]:
    timed = [(i, m) for i, c in enumerate(cases)
             if (m := c.classifier) is not None and _timed(m)]  # fmt: skip
    longest = max((m.cached_tokens or 0 for _, m in timed), default=0)
    if not longest:
        return []
    groups: dict[str, list[CallMetrics]] = {"an actor call": [], "other": []}
    for i, m in timed:
        after = i > 0 and cases[i - 1].actor is not None
        groups["an actor call" if after else "other"].append(m)
    out: list[CacheShort] = []
    for after, ms in groups.items():
        short = [m for m in ms if (m.cached_tokens or 0) < CACHE_SHORT * longest]
        out.append(
            CacheShort(
                after,
                len(ms),
                len(short),
                percentile([cast(int, m.prompt_ns) / 1e9 for m in short], 50),
            )
        )
    return out


def call_figures(cases: list[CaseResult]) -> dict[str, object]:
    """Per role, over the successful calls with timings, cold starts left out (pre-classifier
    plan §11, perf-3): prompt, uncached, cached and output tokens and prompt and generation
    seconds, each as p50, p95 and max; then the classifier calls whose cached prefix fell short,
    after an actor call or otherwise (`cache_short`)."""
    out: dict[str, object] = {}
    for role in ("classifier", "actor"):
        ms = [m for c in cases if (m := getattr(c, role)) is not None and _timed(m)]
        if not ms:
            continue
        out[role] = {
            "calls": len(ms),
            "prompt_tokens": _spread([m.prompt_tokens or 0 for m in ms]),
            "uncached_tokens": _spread([_uncached(m) for m in ms]),
            "cached_tokens": _spread([m.cached_tokens or 0 for m in ms]),
            "output_tokens": _spread([m.output_tokens or 0 for m in ms]),
            "prompt_s": _spread([cast(int, m.prompt_ns) / 1e9 for m in ms], 2),
            "generation_s": _spread([cast(int, m.eval_ns) / 1e9 for m in ms], 2),
        }
    out["cache_short"] = [{"after": g.after, "calls": g.calls, "short": g.short,
                           "prompt_s": None if g.prompt_s is None else round(g.prompt_s, 2)}
                          for g in _cache_short(cases)]  # fmt: skip
    return out


def _spread(values: list[int] | list[float], digits: int = 0) -> dict[str, float | None]:
    xs = [float(v) for v in values]

    def r(v: float | None) -> float | None:
        return None if v is None else round(v, digits)

    return {"p50": r(percentile(xs, 50)), "p95": r(percentile(xs, 95)),
            "max": r(max(xs)) if xs else None}  # fmt: skip


def _timed(m: CallMetrics) -> bool:
    return (m.prompt_ns is not None and m.eval_ns is not None
            and (m.load_ns or 0) <= COLD_LOAD_NS)  # fmt: skip


def _uncached(m: CallMetrics) -> int:
    return max(0, (m.prompt_tokens or 0) - (m.cached_tokens or 0))


def _p(values: list[float]) -> tuple[float, float]:
    return cast(float, percentile(values, 50)), cast(float, percentile(values, 95))


def _pi(values: list[int], p: float) -> int:
    return int(cast(float, percentile(values, p)))
