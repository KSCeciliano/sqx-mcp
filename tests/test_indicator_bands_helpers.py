"""Unit tests for indicator_bands helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.indicator_bands import (
    _atr,
    _atr_stop,
    _bollinger,
    _chandelier,
    _donchian,
    _ema,
    _keltner,
)


def test_ema_seed_at_window() -> None:
    xs = [10.0] * 20
    e = _ema(xs, span=5)
    # First 4 are None, 5th is seed = mean of first 5 = 10
    assert e[4] == 10.0
    # All entries (constant input) should converge to 10
    valid = [v for v in e if v is not None]
    assert all(abs(v - 10.0) < 1e-9 for v in valid)


def test_atr_constant_bars_zero() -> None:
    """Constant H=L=C means TR=0; ATR should be 0."""
    n = 30
    atr = _atr([100.0] * n, [100.0] * n, [100.0] * n, window=14)
    valid = [v for v in atr if v is not None]
    assert all(v == 0.0 for v in valid)


def test_atr_increases_with_range() -> None:
    highs = [101.0] * 30
    lows = [99.0] * 30
    closes = [100.0] * 30
    atr = _atr(highs, lows, closes, window=14)
    valid = [v for v in atr if v is not None]
    # TR is at least 2 per bar (high - low), so ATR ≈ 2
    assert all(abs(v - 2.0) < 0.01 for v in valid)


def test_atr_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _atr(
            [float("nan")] + [101.0] * 20, [99.0] * 21, [100.0] * 21, window=14,
        )


def test_bollinger_constant_series_zero_width() -> None:
    closes = [100.0] * 30
    r = _bollinger(closes, window=20, n_std=2.0)
    assert r["current"]["upper"] == 100.0
    assert r["current"]["lower"] == 100.0


def test_bollinger_position_in_band() -> None:
    # 19 bars of 100, then jump to 110 — close is way above upper band
    closes = [100.0] * 19 + [110.0]
    r = _bollinger(closes, window=20, n_std=2.0)
    pib = r["current"]["position_in_band"]
    # Could be > 1.0 (above upper band)
    assert pib is not None
    assert pib > 0.99


def test_bollinger_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _bollinger([float("nan")] + [100.0] * 30, window=20, n_std=2.0)


def test_donchian_basic() -> None:
    highs = [100.0, 101.0, 102.0, 105.0, 103.0]
    lows = [98.0, 99.0, 97.0, 100.0, 99.0]
    r = _donchian(highs, lows, window=3)
    # 3rd bar onwards has values
    assert r["upper"][2] == 102.0
    assert r["lower"][2] == 97.0
    assert r["upper"][3] == 105.0
    assert r["lower"][3] == 97.0


def test_donchian_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _donchian([float("nan")] + [100.0] * 10, [99.0] * 11, window=3)


def test_atr_stop_long_below_entry() -> None:
    r = _atr_stop(entry=100.0, atr=2.0, side="long", n=2.0)
    assert r["stop_price"] == 96.0
    assert r["distance_abs"] == 4.0


def test_atr_stop_short_above_entry() -> None:
    r = _atr_stop(entry=100.0, atr=2.0, side="short", n=2.0)
    assert r["stop_price"] == 104.0


def test_keltner_includes_three_series() -> None:
    n = 50
    highs = [100.0 + i * 0.1 for i in range(n)]
    lows = [99.0 + i * 0.1 for i in range(n)]
    closes = [99.5 + i * 0.1 for i in range(n)]
    r = _keltner(highs, lows, closes, window=14, n_atr=2.0)
    assert "upper" in r and "middle" in r and "lower" in r
    assert r["current"]["middle"] is not None


def test_chandelier_long_below_high() -> None:
    n = 50
    highs = [100.0 + i for i in range(n)]
    lows = [99.0 + i for i in range(n)]
    closes = [99.5 + i for i in range(n)]
    r = _chandelier(highs, lows, closes, window=22, n_atr=3.0)
    long_stop = r["current"]["long_stop"]
    assert long_stop is not None
    # Long stop should be below the current high
    assert long_stop < highs[-1]


def test_chandelier_short_above_low() -> None:
    n = 50
    highs = [100.0 - i for i in range(n)]
    lows = [99.0 - i for i in range(n)]
    closes = [99.5 - i for i in range(n)]
    # Ensure values stay positive
    highs = [max(h, 1.0) for h in highs]
    lows = [max(low, 0.5) for low in lows]
    closes = [max(c, 0.7) for c in closes]
    r = _chandelier(highs, lows, closes, window=22, n_atr=3.0)
    short_stop = r["current"]["short_stop"]
    assert short_stop is not None
    assert math.isfinite(short_stop)
