"""Unit tests for vwap helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.vwap import (
    _participation,
    _slippage,
    _twap,
    _vwap,
    _vwap_per_session,
)


def test_vwap_basic() -> None:
    prices = [100.0, 101.0, 102.0]
    volumes = [10.0, 10.0, 10.0]
    r = _vwap(prices, volumes)
    assert r["vwap"] == 101.0  # equal-weighted mean


def test_vwap_volume_weighted() -> None:
    prices = [100.0, 200.0]
    volumes = [9.0, 1.0]  # heavy on the cheap side
    r = _vwap(prices, volumes)
    # (100*9 + 200*1) / 10 = 110
    assert r["vwap"] == 110.0


def test_vwap_zero_volume() -> None:
    r = _vwap([100.0] * 5, [0.0] * 5)
    assert r["vwap"] is None


def test_vwap_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _vwap([100.0, float("nan"), 102.0], [10.0, 10.0, 10.0])


def test_twap_equal_weights_equals_mean() -> None:
    r = _twap([10.0, 20.0, 30.0], [1.0, 1.0, 1.0])
    assert r["twap"] == 20.0


def test_twap_zero_weights() -> None:
    r = _twap([10.0, 20.0], [0.0, 0.0])
    assert r["twap"] is None


def test_slippage_buy_overpay() -> None:
    r = _slippage(fill=101.0, benchmark=100.0, side="buy", size=10.0)
    assert r["slippage_abs"] == 1.0
    assert r["slippage_bps"] == 100.0
    assert r["slippage_cost"] == 10.0
    assert r["verdict"] == "poor"


def test_slippage_buy_good_fill() -> None:
    r = _slippage(fill=99.95, benchmark=100.0, side="buy", size=1.0)
    assert r["slippage_abs"] < 0
    assert r["verdict"] in {"excellent", "good"}


def test_slippage_sell_inverse_sign() -> None:
    r = _slippage(fill=99.0, benchmark=100.0, side="sell", size=10.0)
    assert r["slippage_abs"] == 1.0  # sold for 1 less than benchmark


def test_participation_high_impact() -> None:
    r = _participation(own=10.0, market=50.0)
    assert r["participation_pct"] == 20.0
    assert r["verdict"] == "high_impact"


def test_participation_negligible() -> None:
    r = _participation(own=1.0, market=1000.0)
    assert r["verdict"] == "negligible_impact"


def test_vwap_per_session_splits_by_hour() -> None:
    # Hours 0..23 with prices 100..123, all volume = 1
    hours = list(range(24))
    prices = [100.0 + h for h in hours]
    volumes = [1.0] * 24
    sessions = {
        "early": [0, 12],   # hours 0..11
        "late": [12, 24],   # hours 12..23
    }
    r = _vwap_per_session(hours, prices, volumes, sessions)
    assert r["by_session"]["early"]["vwap"] is not None
    # Early VWAP = mean of 100..111 = 105.5
    assert r["by_session"]["early"]["vwap"] == 105.5


def test_vwap_per_session_wraps_midnight() -> None:
    hours = list(range(24))
    prices = [100.0] * 24
    volumes = [1.0] * 24
    sessions = {"asia": [22, 8]}  # 22, 23, 0-7
    r = _vwap_per_session(hours, prices, volumes, sessions)
    assert r["by_session"]["asia"]["vwap"] == 100.0
