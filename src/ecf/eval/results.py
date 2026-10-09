"""Eval result files (metrics, per-case correctness and the model's field values; never message
text) and the paired comparison `ecf eval compare` prints (SPEC §16.2, §16.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from ecf.errors import InvalidInputError
from ecf.eval.metrics import holm, mcnemar_exact, newcombe_paired, wilson
from ecf.schema import load_schema

_STRICT = ConfigDict(extra="forbid", frozen=True)
NON_INFERIORITY_POINTS = 3.0
HOLM_ALPHA = 0.05  # family-wise, over the per-field tests (SPEC §16.5)


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
    diff_ci: tuple[float, float]
    # Newcombe's paired score interval (§16.5, R2): for the decision-model experiment's
    # non-inferiority; unlike Wald it doesn't collapse to [0, 0] without discordant pairs
    diff_ci_score: tuple[float, float] = (0.0, 0.0)

    @property
    def b_non_inferior(self) -> bool:
        return self.diff_ci[0] > -NON_INFERIORITY_POINTS


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
    # 95% CI for a paired difference in proportions (Wald on discordant pairs)
    se = ((b_only + a_only) / n - diff * diff) ** 0.5 / n**0.5
    ci = (100 * (diff - 1.96 * se), 100 * (diff + 1.96 * se))
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
        diff_ci=ci,
        diff_ci_score=(100 * lo, 100 * hi),
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
