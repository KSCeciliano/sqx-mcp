"""Unit tests for trade_replay helpers."""

from __future__ import annotations

from sq_mcp.tools.trade_replay import (
    _apply_costs,
    _compare,
    _filter_by_regime,
    _metrics_from_pnls,
    _position_sizing,
    _risk_caps,
)


def test_metrics_from_pnls_basic() -> None:
    result = _metrics_from_pnls([10.0, -5.0, 8.0, -3.0, 12.0])
    assert result["n_trades"] == 5
    assert result["total_pnl"] == 22.0
    assert result["win_rate"] == 0.6
    assert result["profit_factor"] is not None and result["profit_factor"] > 1.0


def test_metrics_empty_handled() -> None:
    result = _metrics_from_pnls([])
    assert result["n"] == 0


def test_apply_costs_reduces_pnl() -> None:
    original = [10.0, -5.0, 8.0]
    result = _apply_costs(original, 1.0, 0.5)
    assert result["replayed_metrics"]["total_pnl"] == 13.0 - 4.5  # original 13, cost 3 × 1.5
    assert result["cost_per_trade"] == 1.5


def test_position_sizing_fixed_lot() -> None:
    pnls_per_unit = [10.0, -5.0, 8.0]
    result = _position_sizing(
        pnls_per_unit, 10_000.0, "fixed_lot", 2.0, 0.01, 1.0, 100.0
    )
    assert result["final_equity"] == 10_000.0 + 2.0 * sum(pnls_per_unit)


def test_position_sizing_fixed_fractional_grows() -> None:
    pnls_per_unit = [10.0, 10.0, 10.0]
    result = _position_sizing(
        pnls_per_unit, 10_000.0, "fixed_fractional", 1.0, 0.1, 1.0, 100.0
    )
    assert result["final_equity"] > 10_000.0


def test_position_sizing_percent_risk() -> None:
    pnls_per_unit = [10.0, 10.0]
    result = _position_sizing(
        pnls_per_unit, 10_000.0, "percent_risk", 1.0, 0.01, 1.0, 100.0
    )
    # Size = 10k * 0.01 / 100 = 1, so pnl = 10 + 10 = 20, plus compounding
    assert 10_010 < result["final_equity"] < 10_050


def test_risk_caps_per_trade_loss() -> None:
    pnls = [10.0, -1000.0, 10.0]
    result = _risk_caps(pnls, 50.0, None, None, 1)
    assert result["n_capped"] == 1
    # Original total: -980; capped total: -30
    assert result["metrics"]["total_pnl"] == -30.0


def test_risk_caps_no_caps() -> None:
    pnls = [10.0, -5.0, 8.0]
    result = _risk_caps(pnls, None, None, None, 1)
    assert result["metrics"]["total_pnl"] == 13.0
    assert result["n_capped"] == 0


def test_filter_by_regime_skips_listed() -> None:
    pnls = [10.0, -5.0, 8.0, -3.0]
    regimes = ["bull", "bear", "bull", "bear"]
    result = _filter_by_regime(pnls, regimes, ["bear"])
    assert result["n_kept"] == 2
    assert result["metrics"]["total_pnl"] == 18.0


def test_compare_reports_delta() -> None:
    result = _compare([10.0, -5.0, 8.0], [12.0, -3.0, 10.0])
    assert "delta" in result
    assert result["delta"]["total_pnl"] == 6.0
