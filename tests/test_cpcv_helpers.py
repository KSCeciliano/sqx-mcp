"""Unit tests for cpcv helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.cpcv import (
    _generate_splits,
    _group_boundaries,
    _path_count,
    _summarize_paths,
)


def test_path_count_basic() -> None:
    # C(6,2) = 15
    result = _path_count(6, 2)
    assert result["n_paths"] == 15
    assert result["n_test_groups_per_path"] == 2


def test_path_count_k_equals_n_returns_zero() -> None:
    result = _path_count(5, 5)
    assert result["n_paths"] == 0


def test_group_boundaries_even_split() -> None:
    bounds = _group_boundaries(100, 5)
    assert bounds == [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]


def test_group_boundaries_uneven_remainder_distributed() -> None:
    bounds = _group_boundaries(103, 5)
    # 103 / 5 = 20 base + 3 extra → first three get +1
    sizes = [hi - lo for lo, hi in bounds]
    assert sizes == [21, 21, 21, 20, 20]
    assert sum(sizes) == 103


def test_generate_splits_count_matches_combinations() -> None:
    result = _generate_splits(n_samples=100, n_groups=5, test_groups=2, embargo_pct=0.0)
    # C(5, 2) = 10 paths
    assert result["n_paths"] == 10


def test_generate_splits_no_train_test_overlap() -> None:
    result = _generate_splits(n_samples=100, n_groups=5, test_groups=2, embargo_pct=0.0)
    for s in result["splits"]:
        train_set = set(s["train_indices"])
        test_set = set(s["test_indices"])
        assert train_set.isdisjoint(test_set)


def test_generate_splits_embargo_excludes_post_test() -> None:
    result = _generate_splits(n_samples=100, n_groups=5, test_groups=1, embargo_pct=0.05)
    # 5% embargo on 100 samples = 5 samples
    s = result["splits"][0]
    test_end = max(s["test_indices"]) + 1
    embargo_zone = set(range(test_end, test_end + 5))
    train_set = set(s["train_indices"])
    assert train_set.isdisjoint(embargo_zone)


def test_summarize_paths_robust_verdict() -> None:
    # All sharpes well above benchmark
    sharpes = [1.5] * 100
    result = _summarize_paths(sharpes, benchmark=0.0)
    assert result["verdict"] == "robust"
    assert result["frac_better_than_benchmark"] == 1.0


def test_summarize_paths_fragile_verdict() -> None:
    sharpes = [-0.5] * 100
    result = _summarize_paths(sharpes, benchmark=0.0)
    assert result["verdict"] == "fragile"
    assert result["frac_better_than_benchmark"] == 0.0


def test_summarize_paths_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _summarize_paths([1.0, float("nan"), 0.5, 0.3], benchmark=0.0)
