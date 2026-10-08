"""Calibration and decision-model statistics (SPEC §16.4, §16.5, §7.8): Newcombe's paired score
interval, ECE with its bootstrap interval and reliability table, Brier, RPS, under-rating, and the
confirmatory endpoints with Holm. Known values are worked by hand in the comments."""

from __future__ import annotations

import pytest

from ecf.eval import metrics
from ecf.eval.results import CaseResult, ResultFile, compare, compare_endpoints


def test_brier_known_values() -> None:
    assert metrics.brier([1.0, 0.0], [True, False]) == 0.0
    assert metrics.brier([0.5], [True]) == 0.25  # (0.5 - 1)^2
    assert metrics.brier([0.8, 0.3], [True, True]) == pytest.approx((0.04 + 0.49) / 2)


def test_rps_known_values() -> None:
    assert metrics.rps([[1.0, 0.0, 0.0, 0.0]], [0]) == 0.0
    # uniform over 4 levels, gold the lowest: cumulative 0.25, 0.5, 0.75 against 1, 1, 1
    assert metrics.rps([[0.25] * 4], [0]) == pytest.approx((0.5625 + 0.25 + 0.0625) / 3)
    # RPS respects order: one level off costs less than three
    near = metrics.rps([[0.0, 1.0, 0.0, 0.0]], [0])
    far = metrics.rps([[0.0, 0.0, 0.0, 1.0]], [0])
    assert near < far


def test_ece_is_zero_when_confidence_matches_accuracy() -> None:
    assert metrics.ece([1.0] * 5, [True] * 5) == 0.0
    assert metrics.ece([0.8] * 10, [True] * 8 + [False] * 2, bins=1) == pytest.approx(0.0)
    # all at 0.9 but half right: a 0.4 gap
    assert metrics.ece([0.9] * 4, [True, False] * 2, bins=1) == pytest.approx(0.4)


def test_reliability_bins_hold_every_case() -> None:
    conf = [i / 20 for i in range(20)]
    table = metrics.reliability(conf, [c > 0.5 for c in conf], bins=5)
    assert len(table) == 5 and sum(b["n"] for b in table) == 20
    assert [b["confidence"] for b in table] == sorted(b["confidence"] for b in table)


def test_bootstrap_ece_is_repeatable_and_brackets_the_estimate() -> None:
    conf = [0.6, 0.7, 0.8, 0.9, 0.95, 0.55, 0.65, 0.85] * 5
    ok = [True, False, True, True, True, False, True, False] * 5
    lo, hi = metrics.bootstrap_ece(conf, ok, rounds=300)
    assert (lo, hi) == metrics.bootstrap_ece(conf, ok, rounds=300)
    assert lo <= metrics.ece(conf, ok) <= hi


def test_under_rated_counts_lower_and_failed_predictions() -> None:
    levels = ["none", "low", "medium", "high"]
    gold = ["high", "medium", "medium", "low", "high"]
    pred: list[str | None] = ["high", "low", None, "none", "medium"]
    # of the 4 gold medium/high: "low" < medium, None, "medium" < high are under; "low" gold skipped
    assert metrics.under_rated(gold, pred, levels, "medium") == (3, 4)


def test_newcombe_contains_the_estimate_and_is_antisymmetric() -> None:
    lo, hi = metrics.newcombe_paired(60, 15, 5, 20)
    assert lo < (15 - 5) / 100 < hi
    lo2, hi2 = metrics.newcombe_paired(60, 5, 15, 20)
    assert lo2 == pytest.approx(-hi) and hi2 == pytest.approx(-lo)


def test_newcombe_does_not_collapse_without_discordant_pairs() -> None:
    lo, hi = metrics.newcombe_paired(90, 0, 0, 10)
    assert lo < 0 < hi  # Wald would give [0, 0] and call any model non-inferior (R2)


def test_newcombe_is_wider_than_wald_at_small_counts() -> None:
    lo, hi = metrics.newcombe_paired(170, 2, 1, 11)
    n, d = 184, (2 - 1) / 184
    se = ((2 + 1) / n - d * d) ** 0.5 / n**0.5
    assert hi - lo > 2 * 1.96 * se


def _result(correct: list[bool], category: list[bool]) -> ResultFile:
    cases = [CaseResult(id=f"c{i}", correct=c, fields={"category": k})
             for i, (c, k) in enumerate(zip(correct, category, strict=True))]  # fmt: skip
    return ResultFile(run_id="r", pair="p", set_version="v", created_at="t", cases=cases)


def test_compare_reports_the_score_interval() -> None:
    a = _result([True] * 50 + [False] * 50, [True] * 100)
    b = _result([True] * 50 + [False] * 50, [True] * 100)
    c = compare(a, b)
    assert c.diff_ci == (0.0, 0.0)  # Wald, no discordant pairs
    assert c.diff_ci_score[0] < 0 < c.diff_ci_score[1]


def test_endpoints_are_holm_adjusted_over_two_tests() -> None:
    a = _result([False] * 20 + [True] * 80, [False] * 20 + [True] * 80)
    b = _result([True] * 20 + [True] * 80, [True] * 10 + [False] * 10 + [True] * 80)
    got = compare_endpoints(a, b)
    (p_e2e, h_e2e), (p_cat, h_cat) = got["end_to_end"], got["category"]
    assert p_e2e < p_cat
    assert h_e2e == pytest.approx(min(1.0, 2 * p_e2e))
    assert h_cat == pytest.approx(max(h_e2e, p_cat))
