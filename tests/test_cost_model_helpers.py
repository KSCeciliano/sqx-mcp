"""Unit tests for cost_model helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.cost_model import (
    CostBreakEvenArgs,
    CostModelArgs,
    CostSensitivityArgs,
    _apply_costs,
    _break_even_search,
    _per_trade_cost,
    _recompute_metrics,
    _sensitivity_grid,
    _trade_metrics,
)

# ---- _per_trade_cost -------------------------------------------------------


def test_per_trade_cost_only_commission() -> None:
    args = CostModelArgs(
        trades=[1.0] * 10, commission_per_trade=2.0, slippage_points=0.0
    )
    assert _per_trade_cost(args) == 2.0


def test_per_trade_cost_only_slippage() -> None:
    args = CostModelArgs(
        trades=[1.0] * 10,
        commission_per_trade=0.0,
        slippage_points=5.0,
        point_value=0.5,
    )
    assert _per_trade_cost(args) == 2.5


def test_per_trade_cost_both() -> None:
    args = CostModelArgs(
        trades=[1.0] * 10,
        commission_per_trade=3.0,
        slippage_points=4.0,
        point_value=1.0,
    )
    assert _per_trade_cost(args) == 7.0


# ---- _apply_costs ----------------------------------------------------------


def test_apply_costs_subtracts_from_each() -> None:
    out = _apply_costs([10.0, 20.0, 30.0], 5.0)
    assert out == [5.0, 15.0, 25.0]


def test_apply_costs_zero_cost_no_change() -> None:
    trades = [1.0, 2.0, 3.0]
    out = _apply_costs(trades, 0.0)
    assert out == trades


# ---- _trade_metrics --------------------------------------------------------


def test_trade_metrics_basic() -> None:
    trades = [10.0, -5.0, 10.0, -5.0]
    out = _trade_metrics(trades)
    assert out["net_profit"] == 10.0
    assert out["win_rate"] == 0.5
    assert out["profit_factor"] == 2.0
    assert out["average_trade"] == 2.5


def test_trade_metrics_drawdown() -> None:
    # Cumulative: 10, 0, 10, 0, 10 → peak 10 vs trough 0 → DD 10
    trades = [10.0, -10.0, 10.0, -10.0, 10.0]
    out = _trade_metrics(trades)
    assert out["max_drawdown_abs"] == 10.0


def test_trade_metrics_all_wins_no_pf() -> None:
    out = _trade_metrics([5.0, 5.0, 5.0])
    # gross_loss=0 → pf undefined
    assert out["profit_factor"] is None


def test_trade_metrics_empty() -> None:
    out = _trade_metrics([])
    assert out["n_trades"] == 0


# ---- _recompute_metrics ----------------------------------------------------


def test_recompute_metrics_survives_low_costs() -> None:
    args = CostModelArgs(
        trades=[10.0] * 100,
        commission_per_trade=0.5,
        slippage_points=0.0,
    )
    out = _recompute_metrics(args)
    assert out["survives_costs"] is True
    assert out["verdict"] == "viable"
    # 0.5 cost × 100 trades = 50 reduction
    assert out["net_profit_reduction"] == 50.0


def test_recompute_metrics_killed_by_costs() -> None:
    args = CostModelArgs(
        trades=[1.0] * 100,
        commission_per_trade=2.0,  # cost > avg trade
        slippage_points=0.0,
    )
    out = _recompute_metrics(args)
    assert out["survives_costs"] is False
    assert out["verdict"] == "not_viable_after_costs"


def test_recompute_metrics_marginal_strategy() -> None:
    args = CostModelArgs(
        trades=[10.0, -5.0] * 50,
        commission_per_trade=1.0,
    )
    out = _recompute_metrics(args)
    # Original net = 5*100 = 250; degraded net = 250 - 100 = 150
    assert out["original"]["net_profit"] == 250.0
    assert out["degraded"]["net_profit"] == 150.0


# ---- _break_even_search ----------------------------------------------------


def test_break_even_positive_avg() -> None:
    out = _break_even_search(CostBreakEvenArgs(average_trade=5.0))
    assert out["break_even_cost_per_trade"] == 5.0


def test_break_even_negative_avg() -> None:
    out = _break_even_search(CostBreakEvenArgs(average_trade=-2.0))
    assert "non-positive" in out["note"]
    assert out["break_even_cost_per_trade"] == 0.0


def test_break_even_zero_avg() -> None:
    out = _break_even_search(CostBreakEvenArgs(average_trade=0.0))
    assert "non-positive" in out["note"]


# ---- _sensitivity_grid -----------------------------------------------------


def test_sensitivity_grid_smoke() -> None:
    args = CostSensitivityArgs(
        trades=[10.0] * 100,
        commission_grid=[0.0, 1.0, 2.0],
        slippage_grid=[0.0, 1.0],
    )
    out = _sensitivity_grid(args)
    # 3 × 2 = 6 combinations
    assert out["n_combinations"] == 6
    # Best (commission=0, slip=0) should come first
    best = out["grid"][0]
    assert best["commission_per_trade"] == 0.0
    assert best["slippage_points"] == 0.0


def test_sensitivity_grid_survives_count() -> None:
    args = CostSensitivityArgs(
        trades=[1.0] * 100,
        commission_grid=[0.5, 5.0],  # 0.5 survives, 5.0 kills it
        slippage_grid=[0.0],
    )
    out = _sensitivity_grid(args)
    assert out["n_surviving_combinations"] == 1


def test_sensitivity_grid_includes_original() -> None:
    args = CostSensitivityArgs(
        trades=[10.0] * 10,
        commission_grid=[0.0],
        slippage_grid=[0.0],
    )
    out = _sensitivity_grid(args)
    assert out["original_net_profit"] == 100.0


# ---- adversarial NaN / Inf inputs ------------------------------------------


def test_apply_costs_rejects_nan_trade() -> None:
    with pytest.raises(NumericValidationError):
        _apply_costs([10.0, float("nan"), 30.0], 1.0)


def test_apply_costs_rejects_inf_cost() -> None:
    with pytest.raises(NumericValidationError):
        _apply_costs([10.0, 20.0], math.inf)


def test_break_even_rejects_nan_average_trade() -> None:
    with pytest.raises(NumericValidationError):
        _break_even_search(CostBreakEvenArgs(average_trade=float("nan")))


def test_sensitivity_grid_rejects_nan_in_grid() -> None:
    args = CostSensitivityArgs(
        trades=[10.0] * 100,
        commission_grid=[0.0, float("nan")],
        slippage_grid=[0.0],
    )
    with pytest.raises(NumericValidationError):
        _sensitivity_grid(args)


def test_sensitivity_grid_rejects_inf_in_trades() -> None:
    args = CostSensitivityArgs(
        trades=[1.0] * 5 + [float("inf")] + [1.0] * 5,
        commission_grid=[0.0],
        slippage_grid=[0.0],
    )
    with pytest.raises(NumericValidationError):
        _sensitivity_grid(args)
