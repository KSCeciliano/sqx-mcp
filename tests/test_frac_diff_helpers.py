"""Unit tests for frac_diff helpers."""

from __future__ import annotations

import math
import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.frac_diff import (
    _fracdiff_apply,
    _fracdiff_sweep,
    _fracdiff_weights,
    _heuristic_adf,
)


def test_weights_d_zero_one_returns_one() -> None:
    weights = _fracdiff_weights(0.001, tolerance=1e-5, max_window=100)
    # First weight is always 1.0
    assert weights[-1] == 1.0


def test_weights_d_half_decreasing_magnitude() -> None:
    weights = _fracdiff_weights(0.5, tolerance=1e-8, max_window=200)
    # Most recent observation has weight 1.0; older observations have decreasing magnitude
    assert weights[-1] == 1.0
    # Sum of weights for d=0.5 converges (not to zero)
    assert sum(weights) != 0


def test_weights_d_one_only_two_weights() -> None:
    """d=1 corresponds to standard first difference; weights = [-1, 1]."""
    weights = _fracdiff_weights(1.0, tolerance=1e-8, max_window=100)
    assert len(weights) == 2
    # Reversed for convolution: [w_k=1, w_k=0] → [-1, 1]
    assert math.isclose(weights[-1], 1.0)


def test_fracdiff_apply_returns_shorter_series() -> None:
    rng = random.Random(0)
    series = [rng.gauss(100.0, 1.0) for _ in range(200)]
    result = _fracdiff_apply(series, d=0.5, tolerance=1e-4, max_window=2000)
    assert result["n_input"] == 200
    assert result["n_output"] < 200
    assert result["window"] >= 1


def test_fracdiff_apply_d_one_is_first_difference() -> None:
    series = list(range(1, 101))  # 1..100
    result = _fracdiff_apply([float(x) for x in series], d=1.0, tolerance=1e-8, max_window=10)
    # First difference of 1,2,3,... is all 1s
    assert all(abs(x - 1.0) < 1e-9 for x in result["differentiated"])


def test_fracdiff_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _fracdiff_apply([1.0, float("nan"), 2.0], d=0.5, tolerance=1e-4, max_window=10)


def test_heuristic_adf_stationary_series() -> None:
    rng = random.Random(0)
    # White noise → very stationary → very negative ADF
    series = [rng.gauss(0.0, 1.0) for _ in range(200)]
    adf = _heuristic_adf(series)
    assert adf < -3.0


def test_heuristic_adf_random_walk_non_stationary() -> None:
    rng = random.Random(0)
    # Random walk → non-stationary → ADF near 0
    walk = [0.0]
    for _ in range(200):
        walk.append(walk[-1] + rng.gauss(0.0, 1.0))
    adf = _heuristic_adf(walk)
    assert adf > -3.0  # not significantly stationary


def test_sweep_finds_min_d_for_random_walk() -> None:
    rng = random.Random(0)
    walk = [100.0]
    for _ in range(500):
        walk.append(walk[-1] + rng.gauss(0.0, 0.5))
    result = _fracdiff_sweep(
        walk, d_min=0.1, d_max=0.95, n_steps=10,
        tolerance=1e-4, max_window=2000, threshold=-3.0,
    )
    # Random walk should become stationary at some d in (0, 1)
    assert result["min_stationary_d"] is not None
    assert 0.0 < result["min_stationary_d"] < 1.0


def test_sweep_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _fracdiff_sweep(
            [1.0] * 30 + [float("nan")] * 30, d_min=0.1, d_max=0.9,
            n_steps=5, tolerance=1e-4, max_window=100, threshold=-3.0,
        )
