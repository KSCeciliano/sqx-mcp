"""Unit tests for montecarlo helpers."""

from __future__ import annotations

import random

from sq_mcp.tools.montecarlo import (
    _bootstrap_path,
    _max_drawdown,
    _percentile,
    _probability_of_drawdown,
    _run_simulation,
)

# ---- _percentile -----------------------------------------------------------


def test_percentile_empty_returns_zero() -> None:
    assert _percentile([], 50) == 0.0


def test_percentile_single_value() -> None:
    assert _percentile([42.0], 50) == 42.0


def test_percentile_median_simple() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(xs, 50) == 3.0


def test_percentile_extremes() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(xs, 0) == 1.0
    assert _percentile(xs, 100) == 5.0


def test_percentile_monotonic() -> None:
    xs = [float(i) for i in range(100)]
    p10 = _percentile(xs, 10)
    p50 = _percentile(xs, 50)
    p90 = _percentile(xs, 90)
    assert p10 < p50 < p90


# ---- _max_drawdown ---------------------------------------------------------


def test_max_drawdown_monotonic_up() -> None:
    path = [1.0, 1.1, 1.2, 1.3, 1.4]
    assert _max_drawdown(path) == 0.0


def test_max_drawdown_simple_dip() -> None:
    # Peak 2.0, trough 1.0 → 50%
    path = [1.0, 2.0, 1.0, 1.5]
    assert _max_drawdown(path) == 0.5


def test_max_drawdown_recovery_doesnt_reset_max() -> None:
    # Peak 2.0, trough 1.0 → 50% DD; recovers to 3.0 then dips to 2.5 → 16.6%
    # Final answer: still 50% (the worst)
    path = [1.0, 2.0, 1.0, 3.0, 2.5]
    assert _max_drawdown(path) == 0.5


def test_max_drawdown_empty() -> None:
    assert _max_drawdown([]) == 0.0


# ---- _bootstrap_path -------------------------------------------------------


def test_bootstrap_path_length_correct() -> None:
    rng = random.Random(42)
    path = _bootstrap_path([0.01, -0.01, 0.02], 100, rng)
    assert len(path) == 100


def test_bootstrap_path_deterministic_with_seed() -> None:
    rng1 = random.Random(7)
    rng2 = random.Random(7)
    p1 = _bootstrap_path([0.01, -0.01, 0.02], 50, rng1)
    p2 = _bootstrap_path([0.01, -0.01, 0.02], 50, rng2)
    assert p1 == p2


def test_bootstrap_path_positive_drift() -> None:
    # Only positive returns → equity must monotonically rise
    rng = random.Random(0)
    path = _bootstrap_path([0.01, 0.02, 0.005], 100, rng)
    assert path[-1] > 1.0


# ---- _run_simulation -------------------------------------------------------


def test_run_simulation_smoke() -> None:
    returns = [0.01, -0.005, 0.02, -0.01, 0.005] * 20
    out = _run_simulation(returns, n_paths=200, n_steps=100, seed=42)
    assert out["n_paths"] == 200
    assert "terminal_return_pct" in out
    assert "max_drawdown_pct" in out
    assert "probability_of_loss_pct" in out
    # Percentile order
    tr = out["terminal_return_pct"]
    assert tr["p5"] <= tr["p25"] <= tr["median"] <= tr["p75"] <= tr["p95"]


def test_run_simulation_deterministic_with_seed() -> None:
    returns = [0.01, -0.005, 0.02, -0.01]
    out1 = _run_simulation(returns, n_paths=200, n_steps=50, seed=1)
    out2 = _run_simulation(returns, n_paths=200, n_steps=50, seed=1)
    assert out1["terminal_return_pct"] == out2["terminal_return_pct"]
    assert out1["max_drawdown_pct"] == out2["max_drawdown_pct"]


def test_run_simulation_all_losses_high_drawdown() -> None:
    out = _run_simulation([-0.05, -0.01], n_paths=200, n_steps=20, seed=7)
    assert out["max_drawdown_pct"]["median"] > 30.0


def test_run_simulation_all_gains_no_drawdown() -> None:
    out = _run_simulation([0.01, 0.02, 0.005], n_paths=200, n_steps=50, seed=7)
    assert out["max_drawdown_pct"]["median"] == 0.0
    # All paths end positive
    assert out["probability_of_loss_pct"] == 0.0


# ---- _probability_of_drawdown ---------------------------------------------


def test_probability_of_drawdown_low_threshold_high_probability() -> None:
    # A tiny threshold should be hit often
    out = _probability_of_drawdown(
        [0.01, -0.01], threshold_pct=0.1, n_paths=200, n_steps=50, seed=0
    )
    assert out["probability_pct"] > 80.0


def test_probability_of_drawdown_huge_threshold_zero_probability() -> None:
    # Trivial returns can't drawdown 99% — probability should be 0
    out = _probability_of_drawdown(
        [0.01, -0.001], threshold_pct=99.0, n_paths=200, n_steps=50, seed=0
    )
    assert out["probability_pct"] == 0.0


def test_probability_of_drawdown_deterministic_with_seed() -> None:
    rets = [0.01, -0.005, 0.02, -0.01]
    o1 = _probability_of_drawdown(rets, threshold_pct=5.0, n_paths=500, n_steps=100, seed=42)
    o2 = _probability_of_drawdown(rets, threshold_pct=5.0, n_paths=500, n_steps=100, seed=42)
    assert o1["probability_pct"] == o2["probability_pct"]
