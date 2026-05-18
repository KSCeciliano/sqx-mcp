"""Unit tests for stress_test helpers."""

from __future__ import annotations

from sq_mcp.tools.stress_test import (
    CombinedArgs,
    _apply_random_skip,
    _apply_skip_best,
    _apply_slip,
    _apply_worst_streak,
    _combined,
    _metrics,
    _summarize,
)

# ---- _metrics --------------------------------------------------------------


def test_metrics_basic() -> None:
    out = _metrics([10.0, -5.0, 10.0, -5.0])
    assert out["net"] == 10.0
    assert out["n_trades"] == 4
    assert out["win_rate"] == 0.5


def test_metrics_drawdown_correct() -> None:
    # Cumulative: 10, 0, 10, 0, 10 → peak 10, trough 0 → DD 10
    out = _metrics([10.0, -10.0, 10.0, -10.0, 10.0])
    assert out["max_drawdown_abs"] == 10.0


def test_metrics_empty() -> None:
    out = _metrics([])
    assert out["n_trades"] == 0
    assert out["net"] == 0.0


# ---- _apply_slip -----------------------------------------------------------


def test_slip_positive_loses_value() -> None:
    out = _apply_slip([10.0], slip_pct=10.0)
    assert abs(out[0] - 9.0) < 1e-9


def test_slip_negative_gets_worse() -> None:
    out = _apply_slip([-10.0], slip_pct=10.0)
    assert abs(out[0] - (-11.0)) < 1e-9


def test_slip_zero_unchanged() -> None:
    out = _apply_slip([0.0], slip_pct=50.0)
    assert out == [0.0]


# ---- _apply_skip_best ------------------------------------------------------


def test_skip_best_removes_top_n() -> None:
    out = _apply_skip_best([1.0, 5.0, 3.0, 10.0, 2.0], n=2)
    # Top 2 are 10 and 5; remaining is [1, 3, 2]
    assert sorted(out) == [1.0, 2.0, 3.0]


def test_skip_best_zero_returns_unchanged() -> None:
    src = [1.0, 2.0, 3.0]
    out = _apply_skip_best(src, n=0)
    # Sort just in case order changed (shouldn't)
    assert sorted(out) == sorted(src)


def test_skip_best_n_greater_than_length() -> None:
    out = _apply_skip_best([1.0, 2.0, 3.0], n=10)
    assert out == []


# ---- _apply_random_skip ----------------------------------------------------


def test_random_skip_seeded_deterministic() -> None:
    src = [1.0] * 100
    a = _apply_random_skip(src, 0.2, seed=7)
    b = _apply_random_skip(src, 0.2, seed=7)
    assert a == b


def test_random_skip_fraction_close_to_target() -> None:
    src = [1.0] * 1000
    out = _apply_random_skip(src, 0.2, seed=42)
    skipped = 1000 - len(out)
    # Approx 20% +- a few %
    assert 150 < skipped < 250


# ---- _apply_worst_streak ---------------------------------------------------


def test_worst_streak_makes_max_dd_bigger() -> None:
    # Original: alternating +5/-1
    src = [5.0, -1.0] * 50
    orig_dd = _metrics(src)["max_drawdown_abs"]
    streaked = _apply_worst_streak(src, streak_length=10)
    stress_dd = _metrics(streaked)["max_drawdown_abs"]
    # With 10 -1 trades up front, DD should be at least 10
    assert stress_dd >= 10
    assert stress_dd > orig_dd


def test_worst_streak_preserves_net() -> None:
    src = [5.0, -1.0] * 50
    orig_net = _metrics(src)["net"]
    streaked = _apply_worst_streak(src, streak_length=10)
    stress_net = _metrics(streaked)["net"]
    # Sum should be unchanged (just reordered)
    assert abs(orig_net - stress_net) < 1e-9


# ---- _summarize ------------------------------------------------------------


def test_summarize_survives_when_positive_net() -> None:
    out = _summarize({"net": 100.0}, {"net": 50.0})
    assert out["survives"] is True
    assert out["net_reduction"] == 50.0


def test_summarize_does_not_survive_when_negative() -> None:
    out = _summarize({"net": 100.0}, {"net": -10.0})
    assert out["survives"] is False


# ---- _combined -------------------------------------------------------------


def test_combined_robust_strategy() -> None:
    # Very profitable, low slippage sensitivity
    src = [100.0] * 200 + [-1.0] * 200  # net = 19800
    out = _combined(
        CombinedArgs(trades=src, slip_pct=1.0, n_best_to_remove=5, skip_fraction=0.05, streak_length=5)
    )
    assert out["verdict"] in {"robust", "moderate"}


def test_combined_fragile_strategy() -> None:
    # Marginal strategy with a single big win
    src = [-1.0] * 99 + [1000.0]  # net = 901
    out = _combined(
        CombinedArgs(trades=src, slip_pct=20.0, n_best_to_remove=1, skip_fraction=0.05, streak_length=10)
    )
    # Removing the top trade kills it
    assert out["scenarios"]["skip_best"]["survives"] is False
    assert out["verdict"] in {"fragile", "moderate"}
