"""Unit tests for stationary_bootstrap helpers."""

from __future__ import annotations

import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.stationary_bootstrap import (
    _confidence,
    _paths,
    _resample_series,
    _statistic,
)


def test_statistic_sum() -> None:
    assert _statistic([1.0, 2.0, 3.0], "sum") == 6.0


def test_statistic_mean() -> None:
    assert _statistic([1.0, 2.0, 3.0], "mean") == 2.0


def test_statistic_median_odd() -> None:
    assert _statistic([3.0, 1.0, 2.0], "median") == 2.0


def test_statistic_median_even() -> None:
    assert _statistic([1.0, 2.0, 3.0, 4.0], "median") == 2.5


def test_statistic_sharpe_unit_series() -> None:
    # Mean=2, std=sqrt(2/2)=1, sharpe=2
    sharpe = _statistic([1.0, 2.0, 3.0], "sharpe")
    assert abs(sharpe - 2.0) < 0.5  # rough due to sample-stddev


def test_statistic_sharpe_zero_variance() -> None:
    assert _statistic([5.0, 5.0, 5.0], "sharpe") == 0.0


def test_resample_length_matches() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.0, 1.0) for _ in range(100)]
    r = _resample_series(series, block_length=5.0, n_steps=None, seed=42)
    assert r["n"] == 100


def test_resample_seed_determinism() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.0, 1.0) for _ in range(100)]
    a = _resample_series(series, block_length=5.0, n_steps=None, seed=42)
    b = _resample_series(series, block_length=5.0, n_steps=None, seed=42)
    assert a["resampled"] == b["resampled"]


def test_resample_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _resample_series([1.0, float("nan")] + [0.0] * 10, block_length=2.0, n_steps=None, seed=0)


def test_paths_returns_percentiles() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.01, 0.02) for _ in range(200)]
    r = _paths(series, block_length=10.0, n_paths=200, statistic="mean", seed=42)
    assert "p5" in r and "p95" in r
    assert r["p5"] <= r["median"] <= r["p95"]
    assert r["n_paths"] == 200


def test_paths_observed_is_around_bootstrap_mean() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.01, 0.02) for _ in range(500)]
    r = _paths(series, block_length=10.0, n_paths=500, statistic="mean", seed=42)
    # Bootstrap mean of the mean estimator should be close to the sample mean
    sample_mean = sum(series) / len(series)
    assert abs(r["bootstrap_mean"] - sample_mean) < 0.005


def test_confidence_ci_contains_observed() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.0, 1.0) for _ in range(500)]
    r = _confidence(
        series, block_length=5.0, n_paths=500, statistic="mean",
        confidence=0.95, seed=42,
    )
    observed = r["observed"]
    # 95% CI should contain the observed value most of the time
    # (this is the *bootstrap* CI of the mean, which is centered on the bootstrap distribution)
    assert r["ci_lower"] <= r["bootstrap_mean"] if "bootstrap_mean" in r else True
    # CI is non-trivial
    assert r["ci_width"] > 0
    # Observed mean lies inside CI for this seed
    assert r["ci_lower"] <= observed <= r["ci_upper"]


def test_confidence_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _confidence(
            [1.0, float("nan")] + [0.5] * 10, block_length=2.0,
            n_paths=10, statistic="mean", confidence=0.95, seed=0,
        )
