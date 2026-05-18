"""Unit tests for spa_test helpers."""

from __future__ import annotations

import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.spa_test import (
    _hansen_spa,
    _reality_check,
    _spa_consistency,
    _validate_matrix,
)


def test_validate_matrix_rejects_length_mismatch() -> None:
    with pytest.raises(NumericValidationError, match="length"):
        _validate_matrix({"A": [1.0, 2.0, 3.0]}, [1.0, 2.0])


def test_validate_matrix_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _validate_matrix({"A": [1.0, float("nan"), 3.0]}, [0.0, 0.0, 0.0])


def test_reality_check_true_winner_significant() -> None:
    rng = random.Random(0)
    benchmark = [rng.gauss(0.0, 0.01) for _ in range(200)]
    # Strategy A consistently +0.5% better than benchmark
    strategy_a = [b + 0.005 for b in benchmark]
    # Strategy B is just the benchmark with noise
    strategy_b = [b + rng.gauss(0.0, 0.005) for b in benchmark]
    r = _reality_check(
        {"A": strategy_a, "B": strategy_b},
        benchmark, block_length=5.0, n_boot=300, seed=42,
    )
    assert r["best_strategy"] == "A"
    assert r["p_value"] < 0.05
    assert r["verdict"] == "significant"


def test_reality_check_no_real_winner_not_significant() -> None:
    rng = random.Random(0)
    benchmark = [rng.gauss(0.0, 0.01) for _ in range(200)]
    # 5 strategies, all just benchmark + small noise (no real edge)
    strategies = {
        f"S{i}": [b + rng.gauss(0.0, 0.005) for b in benchmark] for i in range(5)
    }
    r = _reality_check(
        strategies, benchmark, block_length=5.0, n_boot=300, seed=42,
    )
    assert r["p_value"] >= 0.05


def test_hansen_spa_returns_studentized_stat() -> None:
    rng = random.Random(0)
    benchmark = [rng.gauss(0.0, 0.01) for _ in range(200)]
    strategy_a = [b + 0.005 for b in benchmark]
    r = _hansen_spa(
        {"A": strategy_a}, benchmark, block_length=5.0, n_boot=300, seed=42,
    )
    assert "observed_max_studentized_stat" in r
    assert r["best_strategy"] == "A"


def test_spa_consistency_finds_dominant_winner() -> None:
    rng = random.Random(0)
    benchmark = [rng.gauss(0.0, 0.01) for _ in range(200)]
    # Strategy A is dominant
    strategies = {
        "A": [b + 0.01 for b in benchmark],
        "B": [b + rng.gauss(0.0, 0.005) for b in benchmark],
        "C": [b + rng.gauss(0.0, 0.005) for b in benchmark],
    }
    r = _spa_consistency(
        strategies, benchmark, block_length=5.0, n_boot=300, seed=42,
    )
    a_share = next(s["win_share_pct"] for s in r["strategies"] if s["name"] == "A")
    assert a_share > 80.0  # A should win almost every bootstrap
    assert r["most_consistent_winner"] == "A"
