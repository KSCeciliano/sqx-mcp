"""Unit tests for bar_construction helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.bar_construction import (
    _dollar_bars,
    _imbalance_bars,
    _tick_bars,
    _volume_bars,
)


def test_tick_bars_groups_correctly() -> None:
    ts = list(range(10))
    prices = [100.0 + i for i in range(10)]
    volumes = [1.0] * 10
    r = _tick_bars(ts, prices, volumes, n_per=5)
    assert r["n_bars"] == 2
    assert r["bars"][0]["open"] == 100.0
    assert r["bars"][0]["close"] == 104.0
    assert r["bars"][0]["high"] == 104.0
    assert r["bars"][0]["low"] == 100.0


def test_volume_bars_emits_on_threshold() -> None:
    ts = [0, 1, 2, 3]
    prices = [100.0, 101.0, 102.0, 103.0]
    volumes = [3.0, 3.0, 4.0, 5.0]
    # threshold 10: bar 1 = ticks 0+1+2 (sum 10); bar 2 = tick 3 alone (not emitted, leftover 5)
    r = _volume_bars(ts, prices, volumes, vol_per=10.0)
    assert r["n_bars"] == 1
    assert r["bars"][0]["volume"] == 10.0
    assert r["leftover_volume"] == 5.0


def test_dollar_bars_threshold() -> None:
    ts = [0, 1, 2]
    prices = [100.0, 100.0, 100.0]
    volumes = [1.0, 1.0, 1.0]
    # dollars per tick = 100; threshold 200 → bar emits after tick 1
    r = _dollar_bars(ts, prices, volumes, dollar_per=200.0)
    assert r["n_bars"] == 1
    assert r["bars"][0]["n_ticks"] == 2


def test_imbalance_bars_emits_when_threshold_hit() -> None:
    ts = [0, 1, 2, 3]
    prices = [100.0] * 4
    volumes = [5.0, 5.0, 5.0, 5.0]
    sides = ["buy", "buy", "sell", "buy"]
    # cumulative: +5, +10 (threshold!), then reset; -5, then +5
    r = _imbalance_bars(ts, prices, volumes, sides, threshold=10.0)
    assert r["n_bars"] == 1
    assert r["bars"][0]["imbalance_direction"] == "buy"


def test_imbalance_bars_sell_side() -> None:
    ts = [0, 1, 2]
    prices = [100.0] * 3
    volumes = [5.0, 5.0, 5.0]
    sides = ["sell", "sell", "buy"]
    r = _imbalance_bars(ts, prices, volumes, sides, threshold=10.0)
    assert r["n_bars"] == 1
    assert r["bars"][0]["imbalance_direction"] == "sell"


def test_bar_construction_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _tick_bars(
            [0, 1, 2], [100.0, float("nan"), 102.0], [1.0, 1.0, 1.0], n_per=2,
        )


def test_tick_bars_partial_last_bar_dropped() -> None:
    """If the last group has < 2 ticks, it's dropped (no enough data)."""
    ts = [0, 1, 2, 3, 4]
    prices = [100.0, 101.0, 102.0, 103.0, 104.0]
    volumes = [1.0] * 5
    r = _tick_bars(ts, prices, volumes, n_per=2)
    # 5 ticks, 2 per bar → 2 full bars + 1 leftover tick (size 1, dropped)
    assert r["n_bars"] == 2


def test_dollar_bars_handles_large_threshold() -> None:
    ts = [0, 1, 2]
    prices = [100.0, 100.0, 100.0]
    volumes = [1.0, 1.0, 1.0]
    # threshold larger than total → no bars
    r = _dollar_bars(ts, prices, volumes, dollar_per=10_000.0)
    assert r["n_bars"] == 0
    assert r["leftover_dollars"] == 300.0
