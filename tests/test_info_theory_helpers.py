"""Unit tests for info_theory helpers."""

from __future__ import annotations

import math
import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.info_theory import (
    _diversity_index,
    _kl_divergence,
    _mutual_information,
    _shannon_entropy,
    _transfer_entropy,
)


def test_entropy_uniform_distribution_high() -> None:
    rng = random.Random(0)
    series = [rng.uniform(0, 1) for _ in range(2000)]
    r = _shannon_entropy(series, n_bins=20, base=2.0)
    assert r["normalized_entropy"] > 0.9
    assert r["interpretation"] == "uniform"


def test_entropy_concentrated_low() -> None:
    series = [1.0] * 1000 + [1.001] * 5
    r = _shannon_entropy(series, n_bins=20, base=2.0)
    assert r["normalized_entropy"] < 0.5
    assert r["interpretation"] in {"concentrated", "moderately_concentrated"}


def test_entropy_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _shannon_entropy([1.0, float("nan")] + [0.0] * 10, n_bins=10, base=2.0)


def test_kl_divergence_zero_for_identical() -> None:
    rng = random.Random(0)
    s = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    r = _kl_divergence(s, s, n_bins=20)
    assert r["kl_divergence"] < 0.05


def test_kl_divergence_positive_for_different() -> None:
    rng = random.Random(0)
    a = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    b = [rng.gauss(5.0, 1.0) for _ in range(2000)]
    r = _kl_divergence(a, b, n_bins=20)
    assert r["kl_divergence"] > 1.0
    assert r["interpretation"] == "very_different"


def test_mutual_information_identical_high() -> None:
    rng = random.Random(0)
    s = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    r = _mutual_information(s, s, n_bins=20)
    # MI(X, X) should be high (equal to entropy of X)
    assert r["mutual_information"] > 1.0


def test_mutual_information_independent_near_zero() -> None:
    rng_a = random.Random(0)
    rng_b = random.Random(1)
    a = [rng_a.gauss(0.0, 1.0) for _ in range(2000)]
    b = [rng_b.gauss(0.0, 1.0) for _ in range(2000)]
    r = _mutual_information(a, b, n_bins=10)
    # Independent series: MI close to 0 (small finite-sample bias OK)
    assert r["mutual_information"] < 0.3


def test_transfer_entropy_source_drives_target() -> None:
    """Construct: target[t] = source[t-1] + noise."""
    rng = random.Random(0)
    source = [rng.gauss(0.0, 1.0) for _ in range(500)]
    target = [0.0] + [source[i] + rng.gauss(0.0, 0.1) for i in range(499)]
    r = _transfer_entropy(source, target, lag=1, n_bins=6)
    # Source should have substantial information flow to target
    assert r["transfer_entropy"] is not None
    assert r["transfer_entropy"] > 0.0


def test_diversity_index_high_for_different_strategies() -> None:
    rng = random.Random(0)
    strategies = {
        "long_winner": [rng.gauss(0.5, 1.0) for _ in range(500)],
        "short_winner": [rng.gauss(-0.5, 1.0) for _ in range(500)],
        "neutral": [rng.gauss(0.0, 0.5) for _ in range(500)],
    }
    r = _diversity_index(strategies, n_bins=15)
    assert r["mean_pairwise_distance"] > 0.1


def test_diversity_index_low_for_clones() -> None:
    rng = random.Random(0)
    base = [rng.gauss(0.0, 1.0) for _ in range(500)]
    strategies = {f"clone_{i}": list(base) for i in range(3)}
    r = _diversity_index(strategies, n_bins=15)
    assert r["mean_pairwise_distance"] < 0.05
    assert r["verdict"] == "near_duplicates"


def test_entropy_handles_constant_series() -> None:
    r = _shannon_entropy([5.0] * 100, n_bins=10, base=2.0)
    # All mass in one bin → entropy = 0
    assert r["entropy"] == 0.0 or r["normalized_entropy"] < 0.1


def test_entropy_normalized_zero_one() -> None:
    rng = random.Random(0)
    series = [rng.uniform(0, 1) for _ in range(500)]
    r = _shannon_entropy(series, n_bins=10, base=2.0)
    assert 0.0 <= r["normalized_entropy"] <= 1.0 + 1e-9
    assert math.isfinite(r["entropy"])
