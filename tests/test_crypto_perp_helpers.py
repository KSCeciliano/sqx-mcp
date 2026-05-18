"""Unit tests for crypto_perp helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.crypto_perp import (
    _funding_adjusted_pnl,
    _funding_regime,
    _liquidation_price,
    _liquidation_stress,
)


def test_liquidation_price_long_10x() -> None:
    # 10x long: liq = entry × (1 - 0.1 + 0.005) = entry × 0.905
    r = _liquidation_price(10000.0, 10.0, "long", 0.5)
    assert abs(r["liquidation_price"] - 9050.0) < 1.0
    assert abs(r["distance_pct"] - 9.5) < 0.1


def test_liquidation_price_short_10x() -> None:
    r = _liquidation_price(10000.0, 10.0, "short", 0.5)
    assert abs(r["liquidation_price"] - 10950.0) < 1.0


def test_liquidation_price_higher_leverage_closer() -> None:
    a = _liquidation_price(10000.0, 5.0, "long", 0.5)
    b = _liquidation_price(10000.0, 20.0, "long", 0.5)
    assert a["distance_pct"] > b["distance_pct"]


def test_liquidation_stress_safe_position() -> None:
    # 2x leverage, small price moves — no liquidations
    entries = [100.0] * 10
    highs = [101.0] * 10
    lows = [99.0] * 10
    sides = ["long"] * 10
    r = _liquidation_stress(entries, highs, lows, sides, leverage=2.0, maintenance_pct=0.5)
    assert r["n_liquidated"] == 0
    assert r["verdict"] == "safe"


def test_liquidation_stress_high_leverage_crash() -> None:
    # 50x leverage with 5% drop — all longs liquidated
    entries = [100.0] * 10
    highs = [101.0] * 10
    lows = [95.0] * 10
    sides = ["long"] * 10
    r = _liquidation_stress(entries, highs, lows, sides, leverage=50.0, maintenance_pct=0.5)
    assert r["n_liquidated"] == 10
    assert r["verdict"] == "unfeasible"


def test_liquidation_stress_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _liquidation_stress(
            [100.0, float("nan")], [101.0, 101.0], [99.0, 99.0],
            ["long", "long"], leverage=10.0, maintenance_pct=0.5,
        )


def test_funding_adjusted_pnl_long_pays() -> None:
    pnls = [100.0]
    hours = [8.0]
    funding = [0.0001]  # 0.01% per 8h
    sides = ["long"]
    notional = [10000.0]
    r = _funding_adjusted_pnl(pnls, hours, funding, sides, notional)
    # Long pays 0.0001 × 1 × 10000 = 1.0; adjusted = 99.0
    assert abs(r["adjusted_pnls"][0] - 99.0) < 1e-6
    assert r["total_funding_impact"] < 0


def test_funding_adjusted_pnl_short_receives() -> None:
    pnls = [100.0]
    hours = [8.0]
    funding = [0.0001]
    sides = ["short"]
    notional = [10000.0]
    r = _funding_adjusted_pnl(pnls, hours, funding, sides, notional)
    # Short receives 1.0; adjusted = 101.0
    assert abs(r["adjusted_pnls"][0] - 101.0) < 1e-6
    assert r["total_funding_impact"] > 0


def test_funding_adjusted_pnl_warning_high_impact() -> None:
    pnls = [10.0]
    hours = [240.0]  # 10 days
    funding = [0.001]  # high funding
    sides = ["long"]
    notional = [10000.0]
    r = _funding_adjusted_pnl(pnls, hours, funding, sides, notional)
    # Funding cost is ~30 — much bigger than original PnL
    assert r["warning"] is not None


def test_funding_regime_contango() -> None:
    rates = [0.0005] * 100  # all positive funding
    r = _funding_regime(rates, contango=0.0001, backwardation=-0.0001)
    assert r["counts"]["contango"] == 100
    assert r["interpretation"] == "bullish"


def test_funding_regime_backwardation() -> None:
    rates = [-0.0005] * 100
    r = _funding_regime(rates, contango=0.0001, backwardation=-0.0001)
    assert r["counts"]["backwardation"] == 100
    assert r["interpretation"] == "bearish"


def test_funding_regime_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _funding_regime(
            [0.0001] * 10 + [float("nan")],
            contango=0.0001, backwardation=-0.0001,
        )
