"""Unit tests for mean_reversion helpers."""

from __future__ import annotations

import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.mean_reversion import _cusum, _hurst_rs, _ou_half_life


def test_hurst_white_noise_near_half() -> None:
    """R/S applied to i.i.d. white-noise (returns of a random walk) should
    give H ≈ 0.5. Applied to the levels of a random walk, R/S gives H ≈ 1
    (correctly identifying long-range memory), but the textbook claim
    'random walk = 0.5' refers to applying R/S to the returns, not levels.
    """
    rng = random.Random(0)
    white_noise = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    r = _hurst_rs(white_noise)
    assert r["hurst"] is not None
    assert 0.35 < r["hurst"] < 0.65


def test_hurst_random_walk_levels_near_one() -> None:
    """Sanity check: applying R/S directly to random-walk levels gives
    H ≈ 1 (strong long-range dependence)."""
    rng = random.Random(0)
    walk = [0.0]
    for _ in range(2000):
        walk.append(walk[-1] + rng.gauss(0.0, 1.0))
    r = _hurst_rs(walk)
    assert r["hurst"] is not None
    assert r["hurst"] > 0.8  # close to 1
    assert r["interpretation"] == "strongly_trending"


def test_hurst_trending_series_above_half() -> None:
    # Strongly trending: monotonic increase with small noise
    rng = random.Random(0)
    series = [i + rng.gauss(0.0, 0.1) for i in range(1500)]
    r = _hurst_rs(series)
    assert r["hurst"] is not None
    assert r["hurst"] > 0.6
    assert r["interpretation"] in {"trending", "strongly_trending"}


def test_hurst_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _hurst_rs([1.0, float("nan")] + [0.5] * 60)


def test_ou_half_life_strong_mean_reversion() -> None:
    """Simulate strong mean-reverting AR(1): y_t = 0.7 · y_{t-1} + ε."""
    rng = random.Random(0)
    series = [0.0]
    for _ in range(2000):
        series.append(0.7 * series[-1] + rng.gauss(0.0, 1.0))
    r = _ou_half_life(series)
    assert r["half_life"] is not None
    # β=0.7, half_life = -log(2)/log(0.7) ≈ 1.94
    assert 1.0 < r["half_life"] < 3.0


def test_ou_half_life_random_walk_none() -> None:
    rng = random.Random(0)
    walk = [0.0]
    for _ in range(2000):
        walk.append(walk[-1] + rng.gauss(0.0, 1.0))
    r = _ou_half_life(walk)
    # Random walk has β ≈ 1 → half-life undefined or huge
    # We accept None or a very large value
    if r["half_life"] is not None:
        assert r["half_life"] > 100 or r["half_life"] < 0


def test_ou_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _ou_half_life([float("nan"), 1.0] + [0.5] * 40)


def test_cusum_detects_upward_break() -> None:
    # First 100 values around 0, next 100 around 5
    series = [0.0] * 100 + [5.0] * 100
    r = _cusum(series, threshold=10.0, reference=None)
    assert r["n_detections"] >= 1
    assert any(d["direction"] == "upward" for d in r["detections"])


def test_cusum_no_break_stable_series() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.0, 1.0) for _ in range(500)]
    r = _cusum(series, threshold=100.0, reference=0.0)
    # Threshold so high no break should be detected
    assert r["n_detections"] == 0
    assert r["verdict"] == "stable"


def test_cusum_explicit_reference() -> None:
    series = [10.0] * 100  # all above any reference of 0
    r = _cusum(series, threshold=50.0, reference=0.0)
    # Strong upward drift from reference → break detected
    assert r["n_detections"] >= 1
    assert all(d["direction"] == "upward" for d in r["detections"])


def test_cusum_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _cusum(
            [1.0, float("nan")] + [0.5] * 20, threshold=1.0, reference=0.0,
        )


def test_cusum_zero_input_no_break() -> None:
    series = [0.0] * 100
    r = _cusum(series, threshold=1.0, reference=0.0)
    assert r["n_detections"] == 0
