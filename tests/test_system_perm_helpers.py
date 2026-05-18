"""Unit tests for system_perm helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.system_perm import (
    _compare_baseline,
    _permutation_grid,
    _recommend_params,
    _summarize_distribution,
)


def test_grid_full_enumeration() -> None:
    r = _permutation_grid(
        {"a": 10.0, "b": 20.0}, perturbation_pct=10.0,
        n_steps_per_param=3, n_random_samples=None, seed=None,
    )
    # 3 × 3 = 9 combinations
    assert r["n_combinations"] == 9
    assert r["full_grid_size"] == 9


def test_grid_random_subset() -> None:
    r = _permutation_grid(
        {"a": 10.0, "b": 20.0, "c": 5.0}, perturbation_pct=10.0,
        n_steps_per_param=3, n_random_samples=5, seed=42,
    )
    # 27 full grid, ask for 5 random samples
    assert r["n_combinations"] == 5
    assert r["full_grid_size"] == 27


def test_grid_perturbation_within_bounds() -> None:
    r = _permutation_grid(
        {"x": 100.0}, perturbation_pct=10.0,
        n_steps_per_param=3, n_random_samples=None, seed=None,
    )
    values = sorted({c["x"] for c in r["combinations"]})
    assert values == [90.0, 100.0, 110.0]


def test_summarize_robust_verdict() -> None:
    metrics = [1.0 + i * 0.01 for i in range(50)]  # all positive, tight
    r = _summarize_distribution(metrics, baseline=1.0)
    assert r["verdict"] in {"very_robust", "robust"}
    assert r["profitable_share_pct"] == 100.0


def test_summarize_fragile_verdict() -> None:
    metrics = [-2.0, -1.5, -1.0] * 20  # all negative
    r = _summarize_distribution(metrics, baseline=None)
    assert r["verdict"] == "fragile"
    assert r["profitable_share_pct"] == 0.0


def test_summarize_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _summarize_distribution([1.0, float("nan")] + [0.5] * 8, baseline=None)


def test_compare_baseline_top_decile() -> None:
    dist = list(range(100))
    r = _compare_baseline(baseline=95, distribution=dist)
    assert r["in_top_decile"] is True


def test_compare_baseline_overfit_warning() -> None:
    dist = list(range(100))
    r = _compare_baseline(baseline=100, distribution=dist)
    assert "overfit" in r["interpretation"].lower()


def test_recommend_params_median() -> None:
    perms = [{"params": {"a": i}, "metric": float(i)} for i in range(10)]
    r = _recommend_params(perms)
    # Median of 0..9 → index 5 → metric=5.0
    assert r["recommended_metric"] == 5.0
    assert r["recommended_params"]["a"] == 5
    assert r["best_in_sample_metric"] == 9.0
