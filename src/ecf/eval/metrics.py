"""Eval statistics (SPEC §16.4-16.5). Plain Python, tested against known values."""

from __future__ import annotations

import math
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
