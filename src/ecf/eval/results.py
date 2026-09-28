"""Eval result files (metrics and per-case correctness only; never message text) and the paired
comparison `ecf eval compare` prints (SPEC §16.2, §16.5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ecf.errors import InvalidInputError
from ecf.eval.metrics import mcnemar_exact, wilson

_STRICT = ConfigDict(extra="forbid", frozen=True)
NON_INFERIORITY_POINTS = 3.0


class CaseResult(BaseModel):
    model_config = _STRICT
    id: str
    correct: bool  # end-to-end decision correct
    fields: dict[str, bool] = Field(default_factory=dict[str, bool])  # per-field correctness


class ResultFile(BaseModel):
    model_config = _STRICT
    run_id: str
    pair: str
    set_version: str
    created_at: str
    cases: list[CaseResult]


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

    @property
    def b_non_inferior(self) -> bool:
        return self.diff_ci[0] > -NON_INFERIORITY_POINTS


def compare(a: ResultFile, b: ResultFile) -> Comparison:
    """Paired comparison of B against A on the cases both ran (SPEC §16.5)."""
    if a.set_version != b.set_version:
        raise InvalidInputError(f"different sets: {a.set_version} vs {b.set_version}")
    bm = {c.id: c.correct for c in b.cases}
    pairs = [(c.correct, bm[c.id]) for c in a.cases if c.id in bm]
    if not pairs:
        raise InvalidInputError("no cases in common")
    n = len(pairs)
    a_only = sum(x and not y for x, y in pairs)
    b_only = sum(y and not x for x, y in pairs)
    diff = (b_only - a_only) / n
    # 95% CI for a paired difference in proportions (Wald on discordant pairs)
    se = ((b_only + a_only) / n - diff * diff) ** 0.5 / n**0.5
    ci = (100 * (diff - 1.96 * se), 100 * (diff + 1.96 * se))
    return Comparison(
        n=n,
        a_correct=sum(x for x, _ in pairs),
        b_correct=sum(y for _, y in pairs),
        b_only=b_only,
        a_only=a_only,
        p_value=mcnemar_exact(b_only, a_only),
        diff_points=100 * diff,
        diff_ci=ci,
    )


def summary(r: ResultFile) -> str:
    k, n = sum(c.correct for c in r.cases), len(r.cases)
    lo, hi = wilson(k, n)
    return f"{r.pair}: {k}/{n} correct ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f}-{100 * hi:.1f})"
