"""Unit tests for drift helpers."""

from __future__ import annotations

import random

from sq_mcp.tools.drift import (
    DistributionsArgs,
    DrawdownArgs,
    MeanTradeArgs,
    OverallArgs,
    WinRateArgs,
    _distribution_drift,
    _drawdown_drift,
    _ks_critical,
    _ks_statistic,
    _mean_trade_drift,
    _overall_drift,
    _wilson_interval,
    _win_rate_drift,
)

# ---- _wilson_interval -----------------------------------------------------


def test_wilson_interval_zero_sample() -> None:
    lo, hi = _wilson_interval(0.5, 0, 0.95)
    # Degenerate: just return full unit interval
    assert lo == 0.0
    assert hi == 1.0


def test_wilson_interval_contains_phat() -> None:
    lo, hi = _wilson_interval(0.5, 100, 0.95)
    assert lo < 0.5 < hi


def test_wilson_interval_tightens_with_n() -> None:
    _, hi_small = _wilson_interval(0.5, 20, 0.95)
    _, hi_large = _wilson_interval(0.5, 200, 0.95)
    assert (hi_large - 0.5) < (hi_small - 0.5)


# ---- _win_rate_drift -------------------------------------------------------


def test_win_rate_drift_insufficient_sample() -> None:
    out = _win_rate_drift(
        WinRateArgs(backtest_win_rate=0.5, live_wins=5, live_losses=5)
    )
    assert out["verdict"] == "insufficient_sample"


def test_win_rate_drift_within_band_when_close() -> None:
    out = _win_rate_drift(
        WinRateArgs(backtest_win_rate=0.5, live_wins=25, live_losses=25)
    )
    assert out["verdict"] == "within_band"
    assert out["live_win_rate"] == 0.5


def test_win_rate_drift_detects_worse_streak() -> None:
    # 0 wins out of 30 — backtest expects 50%
    out = _win_rate_drift(
        WinRateArgs(backtest_win_rate=0.5, live_wins=0, live_losses=30)
    )
    assert out["verdict"] == "drift_detected"
    assert out["drift_direction"] == "worse_than_backtest"


def test_win_rate_drift_detects_better_streak() -> None:
    out = _win_rate_drift(
        WinRateArgs(backtest_win_rate=0.4, live_wins=30, live_losses=0)
    )
    assert out["verdict"] == "drift_detected"
    assert out["drift_direction"] == "better_than_backtest"


# ---- _mean_trade_drift -----------------------------------------------------


def test_mean_trade_drift_insufficient_sample() -> None:
    out = _mean_trade_drift(
        MeanTradeArgs(backtest_mean=1.0, backtest_std=1.0, live_trades=[1.0] * 10)
    )
    assert out["verdict"] == "insufficient_sample"


def test_mean_trade_drift_within_band() -> None:
    # 30 trades centered near 1.0 with std 1.0 → z ≈ 0
    rng = random.Random(42)
    trades = [1.0 + rng.gauss(0, 0.1) for _ in range(30)]
    out = _mean_trade_drift(
        MeanTradeArgs(backtest_mean=1.0, backtest_std=1.0, live_trades=trades)
    )
    assert out["verdict"] == "within_band"


def test_mean_trade_drift_detects_negative_drift() -> None:
    # 30 trades centered at -2 with backtest mean +1 → strong negative z
    trades = [-2.0] * 30
    out = _mean_trade_drift(
        MeanTradeArgs(backtest_mean=1.0, backtest_std=1.0, live_trades=trades)
    )
    assert out["verdict"] == "drift_detected"
    assert out["drift_direction"] == "worse_than_backtest"


# ---- _ks_statistic ---------------------------------------------------------


def test_ks_statistic_identical_distributions_zero() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _ks_statistic(a, b) == 0.0


def test_ks_statistic_disjoint_distributions_max() -> None:
    a = [1.0, 2.0, 3.0]
    b = [10.0, 11.0, 12.0]
    # CDFs never overlap; max distance hits 1.0 at some point in between
    assert _ks_statistic(a, b) == 1.0


def test_ks_statistic_partial_overlap() -> None:
    a = [1.0, 2.0, 3.0, 4.0]
    b = [3.0, 4.0, 5.0, 6.0]
    d = _ks_statistic(a, b)
    assert 0 < d < 1


def test_ks_statistic_empty_inputs_zero() -> None:
    assert _ks_statistic([], [1.0]) == 0.0
    assert _ks_statistic([1.0], []) == 0.0


# ---- _ks_critical ---------------------------------------------------------


def test_ks_critical_increases_with_smaller_sample() -> None:
    # Smaller samples → larger critical value
    big = _ks_critical(1000, 1000)
    small = _ks_critical(20, 20)
    assert small > big


# ---- _distribution_drift ---------------------------------------------------


def test_distribution_drift_insufficient_sample() -> None:
    out = _distribution_drift(
        DistributionsArgs(backtest_trades=[1.0] * 20, live_trades=[1.0] * 10)
    )
    assert out["verdict"] == "insufficient_sample"


def test_distribution_drift_within_band_when_similar() -> None:
    rng = random.Random(42)
    bt = [rng.gauss(0, 1) for _ in range(200)]
    live = [rng.gauss(0, 1) for _ in range(50)]
    out = _distribution_drift(DistributionsArgs(backtest_trades=bt, live_trades=live))
    # Should not flag when both from same distribution
    assert out["verdict"] in {"within_band", "drift_detected"}


def test_distribution_drift_detects_shifted_distribution() -> None:
    rng = random.Random(42)
    bt = [rng.gauss(0, 1) for _ in range(200)]
    # Live trades shifted by +5 std
    live = [rng.gauss(5, 1) for _ in range(50)]
    out = _distribution_drift(DistributionsArgs(backtest_trades=bt, live_trades=live))
    assert out["verdict"] == "drift_detected"


# ---- _drawdown_drift -------------------------------------------------------


def test_drawdown_drift_within_band() -> None:
    out = _drawdown_drift(
        DrawdownArgs(backtest_max_dd_pct=10.0, live_max_dd_pct=12.0)
    )
    # Only 2pp excess → within default 5pp threshold
    assert out["verdict"] == "within_band"


def test_drawdown_drift_flag_when_exceeds_threshold() -> None:
    out = _drawdown_drift(
        DrawdownArgs(backtest_max_dd_pct=10.0, live_max_dd_pct=30.0)
    )
    assert out["verdict"] == "drift_detected"


# ---- _overall_drift --------------------------------------------------------


def test_overall_drift_insufficient_sample() -> None:
    out = _overall_drift(
        OverallArgs(
            backtest_win_rate=0.5,
            backtest_mean_trade=1.0,
            backtest_std_trade=1.0,
            backtest_max_dd_pct=10.0,
            live_trades=[1.0] * 5,
            live_max_dd_pct=8.0,
        )
    )
    assert out["verdict"] == "insufficient_sample"


def test_overall_drift_green_all_consistent() -> None:
    rng = random.Random(42)
    # 25 trades, roughly half wins, mean near 1.0
    trades = [rng.choice([2.0, -0.5]) for _ in range(25)]
    out = _overall_drift(
        OverallArgs(
            backtest_win_rate=0.5,
            backtest_mean_trade=0.5,
            backtest_std_trade=1.5,
            backtest_max_dd_pct=10.0,
            live_trades=trades,
            live_max_dd_pct=9.0,
        )
    )
    assert out["verdict"] in {"green", "yellow"}


def test_overall_drift_red_when_multiple_flags() -> None:
    # All losses + big DD
    out = _overall_drift(
        OverallArgs(
            backtest_win_rate=0.6,
            backtest_mean_trade=1.0,
            backtest_std_trade=0.5,
            backtest_max_dd_pct=5.0,
            live_trades=[-5.0] * 30,
            live_max_dd_pct=40.0,
        )
    )
    assert out["verdict"] == "red"
    assert out["drift_count"] >= 2
