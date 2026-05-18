"""Unit tests for benchmark helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.benchmark import (
    BenchmarkCurvesArgs,
    _alpha_beta,
    _compare_curves,
    _curve_to_returns,
    _excess_returns,
    _max_dd,
    _outperformance_streaks,
)

# ---- _curve_to_returns -----------------------------------------------------


def test_curve_to_returns_simple() -> None:
    out = _curve_to_returns([100.0, 110.0, 121.0])
    assert abs(out[0] - 0.1) < 1e-9
    assert abs(out[1] - 0.1) < 1e-9


def test_curve_to_returns_handles_zero_prev() -> None:
    out = _curve_to_returns([0.0, 100.0])
    assert out[0] == 0.0


# ---- _max_dd ---------------------------------------------------------------


def test_max_dd_monotonic_up_zero() -> None:
    assert _max_dd([1.0, 2.0, 3.0]) == 0.0


def test_max_dd_simple_dip() -> None:
    assert _max_dd([1.0, 2.0, 1.0]) == 0.5


# ---- _compare_curves -------------------------------------------------------


def test_compare_curves_strategy_outperforms() -> None:
    strategy = [100.0 + i for i in range(50)]  # +50%
    benchmark = [100.0 + i * 0.5 for i in range(50)]  # +25%
    out = _compare_curves(
        BenchmarkCurvesArgs(strategy_curve=strategy, benchmark_curve=benchmark)
    )
    assert out["verdict"] == "outperforms_benchmark"
    assert out["excess_return_pct"] > 0


def test_compare_curves_strategy_underperforms() -> None:
    strategy = [100.0 + i * 0.25 for i in range(50)]
    benchmark = [100.0 + i for i in range(50)]
    out = _compare_curves(
        BenchmarkCurvesArgs(strategy_curve=strategy, benchmark_curve=benchmark)
    )
    assert out["verdict"] == "underperforms_benchmark"


def test_compare_curves_truncates_to_common_length() -> None:
    strategy = [100.0 + i for i in range(50)]
    benchmark = [100.0 + i for i in range(30)]
    out = _compare_curves(
        BenchmarkCurvesArgs(strategy_curve=strategy, benchmark_curve=benchmark)
    )
    assert out["n_samples"] == 30


# ---- _alpha_beta -----------------------------------------------------------


def test_alpha_beta_identity_returns_beta_one_alpha_zero() -> None:
    rs = [0.01, 0.02, -0.01, 0.03, 0.02] * 5
    out = _alpha_beta(rs, rs)
    assert abs(out["beta"] - 1.0) < 1e-6
    assert abs(out["alpha"]) < 1e-6
    assert abs(out["correlation"] - 1.0) < 1e-6


def test_alpha_beta_doubled_returns_beta_two() -> None:
    rs = [0.01, 0.02, -0.01, 0.03, 0.02] * 5
    doubled = [2 * r for r in rs]
    out = _alpha_beta(doubled, rs)
    assert abs(out["beta"] - 2.0) < 1e-6


def test_alpha_beta_zero_benchmark_variance_returns_none() -> None:
    rs = [0.01] * 20
    out = _alpha_beta([1.0, 2.0, 3.0] * 10, rs)
    assert out["alpha"] is None
    assert out["beta"] is None


def test_alpha_beta_inverse_correlation() -> None:
    rs = [0.01, 0.02, -0.01, 0.03, 0.02] * 5
    inverted = [-r for r in rs]
    out = _alpha_beta(inverted, rs)
    assert out["correlation"] < 0
    assert out["beta"] < 0


# ---- _excess_returns -------------------------------------------------------


def test_excess_returns_difference_series() -> None:
    s = [0.05, 0.03, 0.02] * 5
    b = [0.02, 0.01, 0.0] * 5
    out = _excess_returns(s, b)
    assert out["mean_excess_per_period"] > 0


def test_excess_returns_truncates_to_common_length() -> None:
    out = _excess_returns([0.01] * 30, [0.02] * 15)
    assert out["n_periods"] == 15


# ---- _outperformance_streaks -----------------------------------------------


def test_outperformance_streaks_strategy_better() -> None:
    s = [0.02] * 20
    b = [0.01] * 20
    out = _outperformance_streaks(s, b)
    assert out["win_rate_vs_benchmark"] == 1.0
    assert out["longest_outperformance_streak"] == 20


def test_outperformance_streaks_strategy_worse() -> None:
    s = [0.01] * 20
    b = [0.02] * 20
    out = _outperformance_streaks(s, b)
    assert out["win_rate_vs_benchmark"] == 0.0
    assert out["longest_underperformance_streak"] == 20


def test_outperformance_streaks_mixed() -> None:
    # Alternating: 5 win, 5 lose, 5 win
    s = [0.05] * 5 + [0.0] * 5 + [0.05] * 5
    b = [0.01] * 5 + [0.05] * 5 + [0.01] * 5
    out = _outperformance_streaks(s, b)
    assert out["longest_outperformance_streak"] == 5
    assert out["longest_underperformance_streak"] == 5


# ---- adversarial NaN / Inf inputs ------------------------------------------


def test_compare_curves_rejects_nan_in_strategy() -> None:
    strategy = [100.0 + i for i in range(50)]
    strategy[10] = float("nan")
    benchmark = [100.0 + i for i in range(50)]
    with pytest.raises(NumericValidationError):
        _compare_curves(
            BenchmarkCurvesArgs(strategy_curve=strategy, benchmark_curve=benchmark)
        )


def test_compare_curves_rejects_inf_in_benchmark() -> None:
    strategy = [100.0 + i for i in range(50)]
    benchmark = [100.0 + i for i in range(50)]
    benchmark[20] = math.inf
    with pytest.raises(NumericValidationError):
        _compare_curves(
            BenchmarkCurvesArgs(strategy_curve=strategy, benchmark_curve=benchmark)
        )


def test_alpha_beta_rejects_nan_in_returns() -> None:
    rs = [0.01, 0.02, -0.01, 0.03, 0.02] * 5
    bad = rs.copy()
    bad[5] = float("nan")
    with pytest.raises(NumericValidationError):
        _alpha_beta(bad, rs)


def test_excess_returns_rejects_inf_in_benchmark() -> None:
    s = [0.05] * 20
    b = [0.02] * 20
    b[10] = -math.inf
    with pytest.raises(NumericValidationError):
        _excess_returns(s, b)


def test_outperformance_rejects_nan() -> None:
    s = [0.02] * 20
    b = [0.01] * 19 + [float("nan")]
    with pytest.raises(NumericValidationError):
        _outperformance_streaks(s, b)
