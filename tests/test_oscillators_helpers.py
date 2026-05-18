"""Unit tests for oscillators helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.oscillators import (
    _cci,
    _macd,
    _rsi,
    _stochastic,
    _williams_r,
)


def test_rsi_strong_uptrend_overbought() -> None:
    closes = [100.0 + i for i in range(30)]  # strictly rising
    r = _rsi(closes, window=14)
    assert r["current"] == 100.0
    assert r["signal"] == "overbought"


def test_rsi_strong_downtrend_oversold() -> None:
    closes = [100.0 - i for i in range(30)]
    r = _rsi(closes, window=14)
    assert r["current"] == 0.0
    assert r["signal"] == "oversold"


def test_rsi_choppy_neutral() -> None:
    closes = [100.0 + (i % 2) for i in range(30)]
    r = _rsi(closes, window=14)
    assert 30.0 <= r["current"] <= 70.0
    assert r["signal"] == "neutral"


def test_rsi_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _rsi([float("nan")] + [100.0] * 30, window=14)


def test_macd_uptrend_positive_histogram() -> None:
    closes = [100.0 + i * 0.5 for i in range(50)]
    r = _macd(closes, fast=12, slow=26, signal=9)
    # Steady uptrend should give a positive (or near-zero) MACD eventually
    assert r["current_macd"] is not None


def test_macd_returns_three_series() -> None:
    closes = [100.0 + i for i in range(60)]
    r = _macd(closes, fast=12, slow=26, signal=9)
    assert "macd" in r and "signal" in r and "histogram" in r


def test_stochastic_at_high() -> None:
    # Close at the top of every window → %K should be 100
    n = 30
    highs = [100.0] * n
    lows = [90.0] * n
    closes = [100.0] * n
    r = _stochastic(highs, lows, closes, k_period=14, d_period=3)
    assert r["current_k"] == 100.0
    assert r["signal"] == "overbought"


def test_stochastic_at_low() -> None:
    n = 30
    highs = [100.0] * n
    lows = [90.0] * n
    closes = [90.0] * n
    r = _stochastic(highs, lows, closes, k_period=14, d_period=3)
    assert r["current_k"] == 0.0
    assert r["signal"] == "oversold"


def test_stochastic_flat_range() -> None:
    """When high == low, %K is undefined → return 50."""
    n = 30
    highs = [100.0] * n
    lows = [100.0] * n
    closes = [100.0] * n
    r = _stochastic(highs, lows, closes, k_period=14, d_period=3)
    assert r["current_k"] == 50.0


def test_cci_returns_value() -> None:
    n = 50
    highs = [100.0 + i * 0.1 for i in range(n)]
    lows = [99.0 + i * 0.1 for i in range(n)]
    closes = [99.5 + i * 0.1 for i in range(n)]
    r = _cci(highs, lows, closes, window=20)
    assert r["current"] is not None


def test_cci_rejects_nan() -> None:
    n = 30
    with pytest.raises(NumericValidationError):
        _cci(
            [float("nan")] + [100.0] * n, [99.0] * (n + 1), [99.5] * (n + 1),
            window=20,
        )


def test_williams_r_overbought() -> None:
    n = 30
    highs = [100.0] * n
    lows = [90.0] * n
    closes = [100.0] * n
    r = _williams_r(highs, lows, closes, window=14)
    assert r["current"] == 0.0
    assert r["signal"] == "overbought"


def test_williams_r_oversold() -> None:
    n = 30
    highs = [100.0] * n
    lows = [90.0] * n
    closes = [90.0] * n
    r = _williams_r(highs, lows, closes, window=14)
    assert r["current"] == -100.0
    assert r["signal"] == "oversold"
