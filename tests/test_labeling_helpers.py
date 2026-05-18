"""Unit tests for labeling helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.labeling import (
    _bet_size_average_active,
    _bet_size_from_prob,
    _label_one_entry,
    _meta_labels,
    _triple_barrier,
)


def test_triple_barrier_pt_hit_long() -> None:
    prices = [100.0, 101.0, 102.5, 103.0]
    r = _label_one_entry(prices, 0, pt_pct=2.0, sl_pct=2.0, horizon=10, side="long")
    assert r["label"] == 1
    assert r["barrier"] == "profit_target"


def test_triple_barrier_sl_hit_long() -> None:
    prices = [100.0, 99.5, 97.5, 96.0]
    r = _label_one_entry(prices, 0, pt_pct=2.0, sl_pct=2.0, horizon=10, side="long")
    assert r["label"] == -1
    assert r["barrier"] == "stop_loss"


def test_triple_barrier_time_hit() -> None:
    prices = [100.0, 100.5, 100.3, 100.7, 100.4]
    r = _label_one_entry(prices, 0, pt_pct=5.0, sl_pct=5.0, horizon=4, side="long")
    assert r["label"] == 0
    assert r["barrier"] == "time_horizon"


def test_triple_barrier_short_inverts_signs() -> None:
    prices = [100.0, 99.0, 97.5]
    r = _label_one_entry(prices, 0, pt_pct=2.0, sl_pct=2.0, horizon=10, side="short")
    assert r["label"] == 1
    assert r["barrier"] == "profit_target"


def test_triple_barrier_batch() -> None:
    prices = [100.0, 102.5, 100.0, 97.0]
    result = _triple_barrier(
        prices, [0, 2], pt_pct=2.0, sl_pct=2.0, horizon=10, side="long"
    )
    assert result["n_entries"] == 2
    assert result["barrier_counts"]["profit_target"] == 1
    assert result["barrier_counts"]["stop_loss"] == 1


def test_triple_barrier_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _triple_barrier(
            [float("nan"), 100.0, 101.0], [0],
            pt_pct=1.0, sl_pct=1.0, horizon=5, side="long",
        )


def test_meta_labels_long_correct() -> None:
    primary = [1, 1, 0, -1]
    pnl = [5.0, -3.0, 0.0, 4.0]  # long+pos=ok, long+neg=bad, no signal, short+pos=ok
    result = _meta_labels(primary, pnl)
    assert result["meta_labels"] == [1, 0, 0, 1]
    assert result["n_correct"] == 2


def test_meta_labels_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _meta_labels([1, 0], [1.0, float("nan")])


def test_bet_size_from_prob_high_confidence_long() -> None:
    r = _bet_size_from_prob(0.9, "long", n_classes=2, step_size=0.0)
    assert r["bet_size"] > 0.5
    assert r["bet_size"] <= 1.0


def test_bet_size_from_prob_high_confidence_short() -> None:
    r = _bet_size_from_prob(0.9, "short", n_classes=2, step_size=0.0)
    assert r["bet_size"] < -0.5


def test_bet_size_from_prob_uncertain_near_zero() -> None:
    r = _bet_size_from_prob(0.5, "long", n_classes=2, step_size=0.0)
    assert abs(r["bet_size"]) < 1e-9


def test_bet_size_from_prob_step_size_discretizes() -> None:
    r = _bet_size_from_prob(0.9, "long", n_classes=2, step_size=0.25)
    # bet_size must be a multiple of 0.25
    assert math.isclose(r["bet_size"] % 0.25, 0.0, abs_tol=1e-9) or \
           math.isclose(r["bet_size"] % 0.25, 0.25, abs_tol=1e-9)


def test_bet_size_average_simple() -> None:
    # Three bets with overlap = 3 → avg = (0.5+0.5+0.5)/3 = 0.5
    sizes = [0.5, 0.5, 0.5]
    windows = [3, 3, 3]
    result = _bet_size_average_active(sizes, windows)
    assert result["max_overlap"] == 3
    assert result["averaged"][2] == 0.5


def test_bet_size_average_no_overlap() -> None:
    sizes = [0.8, -0.8]
    windows = [1, 1]
    result = _bet_size_average_active(sizes, windows)
    assert result["averaged"] == [0.8, -0.8]


def test_bet_size_average_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _bet_size_average_active([0.5, float("nan")], [1, 1])
