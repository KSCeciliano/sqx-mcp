"""Unit tests for brittle helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.brittle import (
    _brittle_curve_concentration,
    _brittle_score_from_trades,
    _consecutive_loss_streak,
    _curve_buckets,
    _gini,
    _top_n_share,
)

# ---- _gini -----------------------------------------------------------------


def test_gini_equal_distribution_zero() -> None:
    assert _gini([1.0] * 10) == 0.0


def test_gini_extreme_concentration() -> None:
    # All zero except one
    g = _gini([1.0] + [0.0] * 9)
    assert g > 0.85


def test_gini_empty_returns_zero() -> None:
    assert _gini([]) == 0.0


def test_gini_zero_total_returns_zero() -> None:
    # All zeros → no inequality
    assert _gini([0.0] * 5) == 0.0


def test_gini_handles_negative_values_via_abs() -> None:
    g1 = _gini([1.0, 2.0, 3.0])
    g2 = _gini([-1.0, -2.0, -3.0])
    assert g1 == g2


# ---- _top_n_share ----------------------------------------------------------


def test_top_n_share_normal_distribution() -> None:
    trades = [1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0]
    out = _top_n_share(trades, n=2)
    assert out["top_n_sum"] == 9.0  # 5 + 4
    assert out["total_positive_pnl"] == 15.0
    assert out["share_of_total_pct"] == 60.0


def test_top_n_share_all_negative() -> None:
    out = _top_n_share([-1.0, -2.0, -3.0], n=2)
    assert "no positive" in out["note"]
    assert out["share_of_total_pct"] == 0.0


def test_top_n_share_top_n_greater_than_count() -> None:
    out = _top_n_share([5.0, 3.0], n=10)
    # Should not crash; uses all 2 trades
    assert out["share_of_total_pct"] == 100.0


# ---- _brittle_score_from_trades --------------------------------------------


def test_brittle_score_uniform_robust() -> None:
    # 20 equal trades
    trades = [1.0] * 20
    out = _brittle_score_from_trades(trades)
    assert out["verdict"] == "robust"
    assert out["gini"] == 0.0


def test_brittle_score_one_big_winner() -> None:
    # 19 small wins of 1.0 plus one huge winner of 100.0
    trades = [1.0] * 19 + [100.0]
    out = _brittle_score_from_trades(trades)
    # Top 10% (=2 trades) capture > 60% of P&L
    assert out["verdict"] in {"very_brittle", "brittle"}
    assert out["top_10pct_share_pct"] >= 60.0


def test_brittle_score_uses_n_floor_for_pct_buckets() -> None:
    # Small n: top 5% rounds to 1 trade
    out = _brittle_score_from_trades([5.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert out["top_5pct_trade_count"] == 1
    assert out["top_10pct_trade_count"] == 1


# ---- _curve_buckets / _brittle_curve_concentration -------------------------


def test_curve_buckets_monotonic_up() -> None:
    curve = [float(i) for i in range(20)]
    deltas = _curve_buckets(curve, 4)
    assert all(d > 0 for d in deltas)
    assert len(deltas) == 4


def test_curve_buckets_flat_curve_zero_deltas() -> None:
    curve = [100.0] * 20
    deltas = _curve_buckets(curve, 4)
    assert all(d == 0 for d in deltas)


def test_brittle_curve_no_gain() -> None:
    out = _brittle_curve_concentration([100.0] * 20, 4)
    assert out["verdict"] == "no_gain"


def test_brittle_curve_smooth_growth_robust() -> None:
    curve = [100.0 + i * 0.5 for i in range(100)]
    out = _brittle_curve_concentration(curve, 20)
    assert out["verdict"] == "robust"


def test_brittle_curve_one_jump_brittle() -> None:
    # Flat for 90 bars, then jumps 100 in last 10 bars
    curve = [100.0] * 90 + [100.0 + i * 10 for i in range(10)]
    out = _brittle_curve_concentration(curve, 10)
    # Top 20% (2 buckets) capture all the gain
    assert out["verdict"] in {"brittle", "very_brittle"}


# ---- _consecutive_loss_streak ----------------------------------------------


def test_loss_streak_simple_alternating() -> None:
    trades = [-1, 1, -1, 1, -1, 1, -1, 1, -1, 1]
    out = _consecutive_loss_streak(trades)
    # Longest streak is 1
    assert out["longest_loss_streak"] == 1


def test_loss_streak_long_losing_run() -> None:
    trades = [1, 1, -1, -1, -1, -1, -1, 1, 1, 1]
    out = _consecutive_loss_streak(trades)
    assert out["longest_loss_streak"] == 5
    assert out["longest_win_streak"] == 3
    assert out["verdict"] == "brittle"  # 5/10 > 0.1 threshold


def test_loss_streak_all_wins_zero() -> None:
    trades = [1.0] * 10
    out = _consecutive_loss_streak(trades)
    assert out["longest_loss_streak"] == 0
    assert out["verdict"] == "robust"


def test_loss_streak_zero_pnl_breaks_streak() -> None:
    trades = [-1, -1, 0, -1, -1]
    out = _consecutive_loss_streak(trades)
    # Zero breaks both streaks → longest is 2
    assert out["longest_loss_streak"] == 2


# ---- adversarial NaN / Inf inputs ------------------------------------------


def test_brittle_score_rejects_nan_trade() -> None:
    with pytest.raises(NumericValidationError):
        _brittle_score_from_trades([1.0, 2.0, float("nan"), 3.0])


def test_top_n_share_rejects_inf_trade() -> None:
    with pytest.raises(NumericValidationError):
        _top_n_share([1.0, math.inf, 3.0], n=2)


def test_brittle_curve_rejects_nan() -> None:
    curve = [100.0 + i for i in range(20)]
    curve[10] = float("nan")
    with pytest.raises(NumericValidationError):
        _brittle_curve_concentration(curve, 4)


def test_loss_streak_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _consecutive_loss_streak([1, -1, float("nan"), -1, -1])
