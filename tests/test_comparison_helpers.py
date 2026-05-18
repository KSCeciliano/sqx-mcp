"""Unit tests for portfolio_ab_test / correlation matrix helpers."""

from __future__ import annotations

import math

from sq_mcp.tools.comparison import (
    _ab_summarize_side,
    _correlation_matrix,
    _curve_to_returns,
    _overlap_by_hash,
    _pearson,
    _verdict_on_metric,
)

# ---- _pearson --------------------------------------------------------------


def test_pearson_perfect_positive() -> None:
    assert math.isclose(_pearson([1, 2, 3, 4], [10, 20, 30, 40]) or 0.0, 1.0, abs_tol=1e-9)


def test_pearson_perfect_negative() -> None:
    assert math.isclose(_pearson([1, 2, 3, 4], [40, 30, 20, 10]) or 0.0, -1.0, abs_tol=1e-9)


def test_pearson_too_short_returns_none() -> None:
    assert _pearson([1.0], [2.0]) is None
    assert _pearson([1.0, 2.0], [2.0, 4.0]) is None  # n=2


def test_pearson_constant_series_returns_none() -> None:
    assert _pearson([1.0, 1.0, 1.0, 1.0], [2.0, 3.0, 4.0, 5.0]) is None
    assert _pearson([1.0, 2.0, 3.0, 4.0], [5.0, 5.0, 5.0, 5.0]) is None


# ---- _curve_to_returns -----------------------------------------------------


def test_curve_to_returns_basic() -> None:
    out = _curve_to_returns([100.0, 110.0, 121.0])
    assert len(out) == 2
    assert math.isclose(out[0], 0.10, abs_tol=1e-9)
    assert math.isclose(out[1], 0.10, abs_tol=1e-9)


def test_curve_to_returns_handles_zero_baseline() -> None:
    # If prev=0 we substitute curr verbatim — avoids div-by-zero blowing up the matrix
    out = _curve_to_returns([0.0, 5.0, 10.0])
    assert out[0] == 5.0


def test_curve_to_returns_skips_nonfinite() -> None:
    out = _curve_to_returns([1.0, float("nan"), 2.0, 4.0])
    # only the 2->4 step survives (1->nan and nan->2 are dropped)
    assert out == [1.0]


# ---- _verdict_on_metric ----------------------------------------------------


def test_verdict_higher_is_better() -> None:
    assert _verdict_on_metric(1.0, 0.5, higher_is_better=True) == "A"
    assert _verdict_on_metric(0.5, 1.0, higher_is_better=True) == "B"
    assert _verdict_on_metric(1.0, 1.0, higher_is_better=True) == "tie"


def test_verdict_lower_is_better() -> None:
    assert _verdict_on_metric(10.0, 20.0, higher_is_better=False) == "A"
    assert _verdict_on_metric(20.0, 10.0, higher_is_better=False) == "B"


def test_verdict_handles_none() -> None:
    assert _verdict_on_metric(None, 1.0, higher_is_better=True) == "B"
    assert _verdict_on_metric(1.0, None, higher_is_better=True) == "A"
    assert _verdict_on_metric(None, None, higher_is_better=True) == "unknown"


# ---- _overlap_by_hash ------------------------------------------------------


def test_overlap_by_hash_jaccard() -> None:
    a = [{"trades_hash": "H1"}, {"trades_hash": "H2"}, {"trades_hash": "H3"}]
    b = [{"trades_hash": "H2"}, {"trades_hash": "H3"}, {"trades_hash": "H4"}]
    out = _overlap_by_hash(a, b)
    assert out["in_both_count"] == 2
    assert out["only_a_count"] == 1
    assert out["only_b_count"] == 1
    # Union={H1,H2,H3,H4}, both={H2,H3} → 2/4 = 0.5
    assert out["jaccard"] == 0.5


def test_overlap_by_hash_empty_sides() -> None:
    out = _overlap_by_hash([], [])
    assert out["in_both_count"] == 0
    assert out["jaccard"] is None


def test_overlap_by_hash_ignores_missing_hash() -> None:
    a = [{"trades_hash": "H1"}, {"trades_hash": None}, {}]
    b = [{"trades_hash": "H1"}]
    out = _overlap_by_hash(a, b)
    assert out["in_both_count"] == 1


# ---- _ab_summarize_side ----------------------------------------------------


def test_ab_summarize_side_empty() -> None:
    s = _ab_summarize_side([])
    assert s["strategies"] == 0
    assert s["unique_trades_hashes"] == 0


def test_ab_summarize_side_basic() -> None:
    rows = [
        {
            "trades_hash": "H1",
            "net_profit": 500.0,
            "fitness_oos": 0.5,
            "drawdown_pct": 10.0,
            "profit_to_dd_ratio": 5.0,
        },
        {
            "trades_hash": "H1",  # duplicate hash
            "net_profit": -100.0,
            "fitness_oos": 0.3,
            "drawdown_pct": 20.0,
            "profit_to_dd_ratio": 1.5,
        },
        {
            "trades_hash": "H2",
            "net_profit": 1000.0,
            "fitness_oos": 0.7,
            "drawdown_pct": 5.0,
            "profit_to_dd_ratio": 10.0,
        },
    ]
    s = _ab_summarize_side(rows)
    assert s["strategies"] == 3
    assert s["profitable_count"] == 2
    assert s["unique_trades_hashes"] == 2  # H1 and H2
    assert s["duplicate_rate_trades_hash"] == round(1 - 2 / 3, 4)
    # fitness_oos values: 0.5, 0.3, 0.7  → mean 0.5
    assert math.isclose(s["fitness_oos"]["mean"], 0.5, abs_tol=1e-6)
    assert math.isclose(s["drawdown_pct"]["mean"], (10 + 20 + 5) / 3, abs_tol=1e-3)


# ---- _correlation_matrix --------------------------------------------------


def test_correlation_matrix_identifies_clones() -> None:
    # Two perfectly identical curves -> corr 1.0; third is anti-correlated
    curves = {
        "a": [100.0, 105.0, 110.0, 120.0, 125.0],
        "b": [200.0, 210.0, 220.0, 240.0, 250.0],  # 2x scaling, same returns
        "c": [100.0, 95.0, 90.0, 80.0, 75.0],
    }
    out = _correlation_matrix(curves, threshold=0.95)
    # a/b should be flagged
    flagged = {(p["a"], p["b"]) for p in out["highly_correlated_pairs"]}
    assert ("a", "b") in flagged or ("b", "a") in flagged
    # Matrix is symmetric
    assert out["matrix"]["a"]["b"] == out["matrix"]["b"]["a"]
    # Diagonal is 1.0
    assert out["matrix"]["a"]["a"] == 1.0


def test_correlation_matrix_threshold_filters() -> None:
    # Two weakly correlated curves should NOT be flagged with a high threshold
    curves = {
        "a": [100.0, 105.0, 110.0, 100.0, 95.0, 110.0, 115.0],
        "b": [100.0, 110.0, 90.0, 95.0, 120.0, 80.0, 100.0],
    }
    out = _correlation_matrix(curves, threshold=0.99)
    assert out["highly_correlated_pairs"] == []


def test_correlation_matrix_empty() -> None:
    out = _correlation_matrix({}, threshold=0.8)
    assert out["names"] == []
    assert out["matrix"] == {}
    assert out["highly_correlated_pairs"] == []
    assert out["avg_abs_off_diagonal"] is None
