"""Unit tests for cointegration helpers."""

from __future__ import annotations

import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.cointegration import (
    _engle_granger,
    _ols_slope_intercept,
    _pair_scan,
    _spread,
    _zscore,
)


def test_ols_slope_recovers_known_relationship() -> None:
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [3.0, 5.0, 7.0, 9.0, 11.0]  # y = 2x + 1
    slope, intercept = _ols_slope_intercept(y, x)
    assert abs(slope - 2.0) < 1e-9
    assert abs(intercept - 1.0) < 1e-9


def test_engle_granger_cointegrated_pair() -> None:
    """y = 2x + noise (stationary residuals)."""
    rng = random.Random(0)
    x = [100.0]
    for _ in range(500):
        x.append(x[-1] + rng.gauss(0.0, 0.5))
    y = [2.0 * xi + rng.gauss(0.0, 0.1) for xi in x]
    r = _engle_granger(y, x)
    assert r["hedge_ratio"] is not None
    assert abs(r["hedge_ratio"] - 2.0) < 0.1
    assert r["verdict"] in {"cointegrated_1pct", "cointegrated_5pct", "cointegrated_10pct"}


def test_engle_granger_independent_random_walks_not_cointegrated() -> None:
    rng1 = random.Random(0)
    rng2 = random.Random(1)
    x = [100.0]
    y = [100.0]
    for _ in range(500):
        x.append(x[-1] + rng1.gauss(0.0, 1.0))
        y.append(y[-1] + rng2.gauss(0.0, 1.0))
    r = _engle_granger(y, x)
    # Independent random walks should NOT be cointegrated
    assert r["verdict"] == "not_cointegrated"


def test_engle_granger_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _engle_granger([float("nan")] + [1.0] * 30, [1.0] * 31)


def test_spread_basic() -> None:
    y = [10.0, 20.0, 30.0]
    x = [1.0, 2.0, 3.0]
    r = _spread(y, x, hedge=10.0)
    # spread = y - 10·x = 0 for each
    assert r["spread"] == [0.0, 0.0, 0.0]


def test_zscore_no_signal_when_at_mean() -> None:
    """If the latest spread is at the rolling mean, z-score = 0."""
    y = list(range(50))
    x = list(range(50))
    r = _zscore([float(v) for v in y], [float(v) for v in x], hedge=1.0, window=10)
    # spread = y - x = 0 → mean = 0, std = 0 → undefined
    assert r["zscore"] is None or abs(r["zscore"]) < 1e-9


def test_zscore_detects_extreme_deviation() -> None:
    # Spread that's near zero for 49 bars, then jumps to 100
    rng = random.Random(0)
    spread_values = [rng.gauss(0.0, 1.0) for _ in range(49)] + [100.0]
    y = spread_values  # treat as y; x = 0 with hedge=1 gives spread = y
    x = [0.0] * 50
    r = _zscore(y, x, hedge=1.0, window=20)
    assert r["zscore"] is not None
    assert r["zscore"] > 2.0
    assert r["signal"] == "short_y_long_x"


def test_pair_scan_finds_cointegrated_pair() -> None:
    rng = random.Random(0)
    # Cointegrated pair: y = 2x + noise
    x_base = [100.0]
    for _ in range(300):
        x_base.append(x_base[-1] + rng.gauss(0.0, 0.5))
    y_coint = [2.0 * xi + rng.gauss(0.0, 0.1) for xi in x_base]
    # Independent random walks
    y_indep = [100.0]
    for _ in range(300):
        y_indep.append(y_indep[-1] + rng.gauss(0.0, 1.0))
    series = {"A": x_base, "B": y_coint, "C": y_indep}
    r = _pair_scan(series, top_n=3)
    # Best pair should be (A, B)
    best = r["best_pairs"][0]
    pair = set(best["pair"])
    assert pair == {"A", "B"}
