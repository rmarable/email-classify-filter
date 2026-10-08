"""Eval statistics (SPEC §16.4-16.5). Plain Python, tested against known values."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

Z95 = 1.959963984540054


def accuracy(gold: Sequence[object], pred: Sequence[object]) -> float:
    if len(gold) != len(pred) or not gold:
        raise ValueError("gold and pred must be non-empty and the same length")
    return sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold)


def confusion(
    gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    m = {g: dict.fromkeys(labels, 0) for g in labels}
    for g, p in zip(gold, pred, strict=True):
        m[g][p] += 1
    return m


def macro_f1(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> float:
    """Mean F1 over labels that appear in gold or pred (absent labels are skipped)."""
    scores: list[float] = []
    for lab in labels:
        tp = sum(g == lab and p == lab for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lab and p == lab for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lab and p != lab for g, p in zip(gold, pred, strict=True))
        if tp + fp + fn == 0:
            continue
        scores.append(2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores) if scores else 0.0


def ordinal_mae(gold: Sequence[str], pred: Sequence[str], levels: Sequence[str]) -> float:
    idx = {v: i for i, v in enumerate(levels)}
    return sum(abs(idx[g] - idx[p]) for g, p in zip(gold, pred, strict=True)) / len(gold)


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(p_values: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values (family-wise error); compare each with alpha."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    out: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * p))
        out[name] = running
    return out


def newcombe_paired(both: int, b_only: int, a_only: int, neither: int,
                    z: float = Z95) -> tuple[float, float]:  # fmt: skip
    """95% interval for a paired difference in proportions, B minus A, in Newcombe's (1998) score
    form: Wilson limits for each proportion, combined through the correlation phi. phi gets a
    continuity correction, max(ad - bc - n/2, 0) / sqrt(...), a conservative choice: with the
    plain phi the interval still collapses to [0, 0] when two runs agree on every case at 50%.
    Unlike Wald it never collapses without discordant pairs (§16.5, R2)."""
    n = both + b_only + a_only + neither
    if n == 0:
        return (-1.0, 1.0)
    p1, p2 = (both + b_only) / n, (both + a_only) / n
    l1, u1 = wilson(both + b_only, n, z)
    l2, u2 = wilson(both + a_only, n, z)
    denom = (both + b_only) * (a_only + neither) * (both + a_only) * (b_only + neither)
    phi = max(both * neither - b_only * a_only - n / 2, 0.0) / math.sqrt(denom) if denom else 0.0
    d = p1 - p2
    lo = d - math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    hi = d + math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    return (max(-1.0, lo), min(1.0, hi))


def ece(confidence: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error with equal-mass bins: the count-weighted mean gap between each
    bin's mean confidence and its accuracy (§16.4)."""
    if len(confidence) != len(correct) or not confidence:
        raise ValueError("confidence and correct must be non-empty and the same length")
    gaps = sum(len(b) * abs(c - a) for b, c, a in _bins(confidence, correct, bins))
    return gaps / len(confidence)


def _bins(confidence: Sequence[float], correct: Sequence[bool],
          bins: int) -> list[tuple[list[int], float, float]]:  # fmt: skip
    order = sorted(range(len(confidence)), key=lambda i: confidence[i])
    k = max(1, min(bins, len(order)))
    out: list[tuple[list[int], float, float]] = []
    for j in range(k):
        idx = order[j * len(order) // k : (j + 1) * len(order) // k]
        if idx:
            out.append((idx, sum(confidence[i] for i in idx) / len(idx),
                        sum(correct[i] for i in idx) / len(idx)))  # fmt: skip
    return out


def reliability(confidence: Sequence[float], correct: Sequence[bool],
                bins: int = 10) -> list[dict[str, float]]:  # fmt: skip
    """Per equal-mass bin: cases, mean confidence and accuracy."""
    return [{"n": len(idx), "confidence": round(c, 4), "accuracy": round(a, 4)}
            for idx, c, a in _bins(confidence, correct, bins)]  # fmt: skip


def bootstrap_ece(confidence: Sequence[float], correct: Sequence[bool], bins: int = 10,
                  rounds: int = 1000, seed: int = 0) -> tuple[float, float]:  # fmt: skip
    """A 95% percentile-bootstrap interval for `ece` (fixed seed, so a report is repeatable)."""
    rng = random.Random(seed)  # noqa: S311 - resampling for a statistic, not security
    n = len(confidence)
    vals: list[float] = []
    for _ in range(rounds):
        pick = [rng.randrange(n) for _ in range(n)]
        vals.append(ece([confidence[i] for i in pick], [correct[i] for i in pick], bins))
    vals.sort()
    return (vals[int(0.025 * rounds)], vals[min(rounds - 1, int(0.975 * rounds))])


def brier(p_true: Sequence[float], outcome: Sequence[bool]) -> float:
    """Mean squared error of a probability for a yes/no field."""
    if len(p_true) != len(outcome) or not p_true:
        raise ValueError("p_true and outcome must be non-empty and the same length")
    return sum((p - float(o)) ** 2 for p, o in zip(p_true, outcome, strict=True)) / len(p_true)


def rps(probs: Sequence[Sequence[float]], gold: Sequence[int]) -> float:
    """Mean ranked probability score for an ordinal field (0 is perfect; it respects the order
    of the levels, unlike Brier)."""
    if len(probs) != len(gold) or not probs:
        raise ValueError("probs and gold must be non-empty and the same length")
    total = 0.0
    for p, g in zip(probs, gold, strict=True):
        k = len(p)
        cum_p = cum_o = 0.0
        s = 0.0
        for i in range(k - 1):
            cum_p += p[i]
            cum_o += 1.0 if i == g else 0.0
            s += (cum_p - cum_o) ** 2
        total += s / (k - 1)
    return total / len(probs)


def under_rated(gold: Sequence[str], pred: Sequence[str | None], levels: Sequence[str],
                at_least: str) -> tuple[int, int]:  # fmt: skip
    """Of the cases whose gold level is `at_least` or higher: how many the prediction put lower
    (a failed prediction counts as lower), and how many there were (§7.8, R5)."""
    idx = {v: i for i, v in enumerate(levels)}
    floor = idx[at_least]
    n = under = 0
    for g, p in zip(gold, pred, strict=True):
        if idx[g] < floor:
            continue
        n += 1
        under += p is None or idx[p] < idx[g]
    return under, n
