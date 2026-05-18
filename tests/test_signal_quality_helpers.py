"""Unit tests for signal_quality helpers."""

from __future__ import annotations

import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.signal_quality import (
    _hit_rate_by_quantile,
    _ic_decay,
    _information_coefficient,
    _ranks,
    _snr,
    _spearman,
    _turnover,
)


def test_ranks_no_ties() -> None:
    r = _ranks([3.0, 1.0, 2.0])
    # 1.0→1, 2.0→2, 3.0→3
    assert r == [3.0, 1.0, 2.0]


def test_ranks_with_ties_get_average() -> None:
    r = _ranks([1.0, 2.0, 2.0, 4.0])
    # Two 2.0's tied for ranks 2 and 3 → average 2.5
    assert r == [1.0, 2.5, 2.5, 4.0]


def test_spearman_perfect_correlation() -> None:
    a = [1.0, 2.0, 3.0, 4.0]
    b = [10.0, 20.0, 30.0, 40.0]
    assert _spearman(a, b) == 1.0


def test_spearman_perfect_anti() -> None:
    a = [1.0, 2.0, 3.0, 4.0]
    b = [40.0, 30.0, 20.0, 10.0]
    assert _spearman(a, b) == -1.0


def test_ic_strong_signal() -> None:
    """Signal that's a noisy version of forward return."""
    rng = random.Random(0)
    forward = [rng.gauss(0.0, 1.0) for _ in range(200)]
    signal = [f + rng.gauss(0.0, 0.3) for f in forward]
    r = _information_coefficient(signal, forward)
    assert r["information_coefficient"] > 0.5
    assert r["verdict"] == "very_strong_signal"


def test_ic_noise_signal() -> None:
    rng_a = random.Random(0)
    rng_b = random.Random(1)
    signal = [rng_a.gauss(0.0, 1.0) for _ in range(500)]
    forward = [rng_b.gauss(0.0, 1.0) for _ in range(500)]
    r = _information_coefficient(signal, forward)
    assert abs(r["information_coefficient"]) < 0.15
    assert r["verdict"] in {"noise", "weak_signal"}


def test_ic_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _information_coefficient([float("nan")] + [0.5] * 30, [0.1] * 31)


def test_ic_decay_returns_per_horizon() -> None:
    rng = random.Random(0)
    prices = [100.0]
    for _ in range(200):
        prices.append(prices[-1] * (1 + rng.gauss(0.001, 0.01)))
    signal = [rng.gauss(0.0, 1.0) for _ in range(200)]
    r = _ic_decay(signal, prices, [1, 2, 5, 10, 20])
    assert len(r["by_horizon"]) == 5
    assert all("ic" in h for h in r["by_horizon"])


def test_quantile_clean_signal() -> None:
    rng = random.Random(0)
    forward = [rng.gauss(0.0, 1.0) for _ in range(500)]
    signal = [f + rng.gauss(0.0, 0.2) for f in forward]  # near-perfect signal
    r = _hit_rate_by_quantile(signal, forward, 5)
    assert r["top_bottom_spread"] > 0
    assert r["verdict"] == "clean_signal"


def test_quantile_no_signal() -> None:
    rng_a = random.Random(0)
    rng_b = random.Random(1)
    signal = [rng_a.gauss(0.0, 1.0) for _ in range(500)]
    forward = [rng_b.gauss(0.0, 1.0) for _ in range(500)]
    r = _hit_rate_by_quantile(signal, forward, 5)
    assert abs(r["top_bottom_spread"]) < 0.3


def test_snr_excellent_for_dominant_signal() -> None:
    rng = random.Random(0)
    signal = [rng.gauss(0.0, 10.0) for _ in range(500)]  # high variance
    noise = [rng.gauss(0.0, 0.5) for _ in range(500)]   # low variance
    r = _snr(signal, noise)
    assert r["snr_db"] > 10
    assert r["verdict"] == "excellent"


def test_snr_noise_dominated() -> None:
    rng = random.Random(0)
    signal = [rng.gauss(0.0, 0.1) for _ in range(500)]
    noise = [rng.gauss(0.0, 10.0) for _ in range(500)]
    r = _snr(signal, noise)
    assert r["snr_db"] < 0
    assert r["verdict"] == "noise_dominated"


def test_turnover_zero_for_constant() -> None:
    r = _turnover([1.0] * 100, normalize=False)
    assert r["turnover"] == 0.0


def test_turnover_high_for_alternating() -> None:
    signal = [(-1.0) ** i for i in range(100)]
    r = _turnover(signal, normalize=False)
    assert r["turnover"] == 2.0  # alternates by 2 each step
    assert r["verdict"] == "extreme_turnover"


def test_turnover_normalized_relative_to_level() -> None:
    # Constant amplitude 1, flipping sign — normalized turnover = 2
    signal = [(-1.0) ** i for i in range(100)]
    r = _turnover(signal, normalize=True)
    assert r["turnover"] == 2.0
