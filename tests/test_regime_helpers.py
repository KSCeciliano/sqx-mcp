"""Unit tests for regime helpers."""

from __future__ import annotations

import math

from sq_mcp.tools.regime import (
    _classify_trend,
    _classify_volatility,
    _pnl_split,
    _recommend_filter,
    _rolling_slope,
    _rolling_std,
)


def test_rolling_std_constant_zero() -> None:
    stds = _rolling_std([1.0] * 30, 5)
    # First 4 entries are None, rest should be 0 (or near-zero)
    assert stds[:4] == [None, None, None, None]
    assert all(s is not None and s < 1e-9 for s in stds[4:])


def test_rolling_std_grows_with_variance() -> None:
    series = [0.0, 1.0] * 50
    stds = _rolling_std(series, 10)
    assert stds[10] is not None
    assert stds[10] > 0.3  # alternating series has nontrivial std


def test_rolling_slope_upward_trend() -> None:
    series = list(range(50))  # perfectly linear up
    slopes = _rolling_slope(series, 10)
    assert slopes[20] is not None
    assert abs(slopes[20] - 1.0) < 1e-6  # slope of i=range(10) on x=range(10) is 1


def test_rolling_slope_downward_trend() -> None:
    series = list(range(50, 0, -1))
    slopes = _rolling_slope(series, 10)
    assert slopes[20] is not None
    assert abs(slopes[20] - (-1.0)) < 1e-6


def test_classify_volatility_buckets() -> None:
    # Build a series with growing volatility
    series: list[float] = []
    for stage in range(3):
        amp = (stage + 1) * 5.0
        for i in range(30):
            series.append(amp * math.sin(i / 5.0))
    result = _classify_volatility(series, 10)
    labels = result["labels"]
    assert len(labels) == 90
    counts = result["counts"]
    # Should have all 3 vol buckets represented
    assert "low" in counts and "high" in counts


def test_classify_trend_buckets() -> None:
    series = list(range(20))            # bull
    series += [20.0] * 20                # neutral
    series += list(range(20, 0, -1))     # bear
    result = _classify_trend(series, 5)
    counts = result["counts"]
    assert "bull" in counts
    assert "bear" in counts


def test_pnl_split_basic() -> None:
    pnls = [10.0, -5.0, 8.0, -3.0, 12.0]
    regimes = ["bull", "bear", "bull", "bear", "bull"]
    result = _pnl_split(pnls, regimes)
    assert result["n_samples"] == 5
    by = result["by_regime"]
    assert by["bull"]["n"] == 3
    assert by["bear"]["n"] == 2
    assert by["bull"]["total_pnl"] == 30.0
    assert by["bear"]["total_pnl"] == -8.0


def test_recommend_filter_keeps_positive() -> None:
    regime_pnl = {
        "bull": {"mean_pnl": 1.5, "win_rate": 0.6, "total_pnl": 100.0},
        "bear": {"mean_pnl": -0.8, "win_rate": 0.3, "total_pnl": -50.0},
        "neutral": {"mean_pnl": 0.2, "win_rate": 0.5, "total_pnl": 10.0},
    }
    result = _recommend_filter(regime_pnl, "mean_pnl")
    assert "bull" in result["recommend_trade"]
    assert "bear" in result["recommend_skip"]
    assert result["best_regime"] == "bull"


def test_pnl_split_empty_regimes() -> None:
    result = _pnl_split([], [])
    assert result["n_samples"] == 0
