"""Unit tests for stats_extra helpers."""

from __future__ import annotations

import random

from sq_mcp.tools.stats_extra import (
    _autocorrelation,
    _irr,
    _jarque_bera,
    _kurtosis,
    _runs_test,
    _skewness,
    _summary,
    _time_weighted_return,
)


def test_skewness_symmetric_zero() -> None:
    series = [-1.0, 0.0, 1.0] * 30
    result = _skewness(series)
    assert abs(result["skewness"]) < 0.2


def test_skewness_positive_skew() -> None:
    rng = random.Random(42)
    series = [rng.expovariate(1.0) for _ in range(500)]
    result = _skewness(series)
    assert result["skewness"] > 1.0
    assert result["interpretation"] == "positive_skew"


def test_kurtosis_normal_close_to_zero() -> None:
    rng = random.Random(42)
    series = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    result = _kurtosis(series)
    assert abs(result["excess_kurtosis"]) < 0.5


def test_kurtosis_fat_tails() -> None:
    rng = random.Random(42)
    series = [rng.gauss(0.0, 1.0) for _ in range(500)]
    series += [10.0, -10.0, 12.0, -11.0]  # add fat tails
    result = _kurtosis(series)
    assert result["excess_kurtosis"] > 1.0


def test_jarque_bera_normal_high_p() -> None:
    rng = random.Random(42)
    series = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    result = _jarque_bera(series)
    assert result["p_value"] > 0.05


def test_jarque_bera_non_normal_low_p() -> None:
    rng = random.Random(42)
    series = [rng.expovariate(1.0) for _ in range(500)]
    result = _jarque_bera(series)
    assert result["p_value"] < 0.05


def test_autocorrelation_zero_for_iid() -> None:
    rng = random.Random(42)
    series = [rng.gauss(0.0, 1.0) for _ in range(500)]
    result = _autocorrelation(series, 1)
    assert abs(result["autocorrelation"]) < 0.15


def test_autocorrelation_high_for_trending() -> None:
    series = list(range(500))
    result = _autocorrelation(series, 1)
    assert result["autocorrelation"] > 0.9


def test_runs_test_alternating_dependent() -> None:
    series = [(-1) ** i for i in range(50)]
    result = _runs_test(series)
    assert result["verdict"] in {"dependent", "borderline"}


def test_runs_test_iid_independent() -> None:
    rng = random.Random(42)
    series = [rng.gauss(0.0, 1.0) for _ in range(500)]
    result = _runs_test(series)
    assert result["verdict"] in {"independent", "borderline"}


def test_summary_basic() -> None:
    series = list(range(1, 101))
    result = _summary(series)
    assert result["n"] == 100
    assert result["mean"] == 50.5
    assert result["median"] == 50.5
    assert result["min"] == 1
    assert result["max"] == 100


def test_irr_simple_case() -> None:
    # Pay 100 now, receive 110 in one period — IRR = 10%
    result = _irr([-100.0, 110.0], guess=0.1, max_iter=200, tol=1e-8)
    assert abs(result["irr_pct"] - 10.0) < 0.001


def test_irr_no_sign_change() -> None:
    result = _irr([100.0, 110.0], guess=0.1, max_iter=200, tol=1e-8)
    assert result["irr"] is None


def test_time_weighted_return_compounding() -> None:
    # +10%, +10%, +10% should give ~33.1%
    result = _time_weighted_return([10.0, 10.0, 10.0])
    assert abs(result["twr_pct"] - 33.1) < 0.01


def test_time_weighted_return_offset_zero() -> None:
    result = _time_weighted_return([5.0, -5.0])
    # (1.05)(0.95) = 0.9975 → -0.25%
    assert abs(result["twr_pct"] - (-0.25)) < 0.01
