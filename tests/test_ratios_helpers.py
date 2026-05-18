"""Unit tests for ratios helpers."""

from __future__ import annotations

from sq_mcp.tools.ratios import (
    CalmarArgs,
    InformationRatioArgs,
    OmegaArgs,
    ReturnSeriesArgs,
    _calmar,
    _downside_std,
    _drawdowns,
    _information_ratio,
    _mean,
    _omega,
    _pain_index,
    _sharpe,
    _sortino,
    _std,
    _ulcer_index,
)

# ---- basic stats -----------------------------------------------------------


def test_mean_basic() -> None:
    assert _mean([1.0, 2.0, 3.0]) == 2.0


def test_mean_empty_returns_zero() -> None:
    assert _mean([]) == 0.0


def test_std_sample() -> None:
    assert abs(_std([1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_std_one_value_is_zero() -> None:
    assert _std([5.0]) == 0.0


def test_downside_std_only_below() -> None:
    # Only the negatives below 0 contribute
    out = _downside_std([2.0, -1.0, -2.0, 3.0], threshold=0.0)
    assert out > 0


def test_downside_std_all_above_returns_zero() -> None:
    assert _downside_std([1.0, 2.0, 3.0], threshold=0.0) == 0.0


# ---- _sharpe ---------------------------------------------------------------


def test_sharpe_zero_std_returns_none() -> None:
    out = _sharpe(ReturnSeriesArgs(returns=[0.01] * 30, risk_free_rate=0.0, periods_per_year=252))
    assert out["sharpe"] is None


def test_sharpe_positive_with_excess_return() -> None:
    # 30 returns alternating around 0.01 with low std
    returns = [0.012 if i % 2 == 0 else 0.008 for i in range(30)]
    out = _sharpe(ReturnSeriesArgs(returns=returns, risk_free_rate=0.0, periods_per_year=252))
    assert out["sharpe"] is not None
    assert out["sharpe"] > 0


def test_sharpe_negative_when_below_rf() -> None:
    returns = [-0.001 if i % 2 == 0 else 0.0005 for i in range(30)]
    out = _sharpe(ReturnSeriesArgs(returns=returns, risk_free_rate=0.5, periods_per_year=252))
    # Excess returns are negative
    assert out["sharpe"] is not None
    assert out["sharpe"] < 0


# ---- _sortino --------------------------------------------------------------


def test_sortino_all_positive_returns() -> None:
    out = _sortino(ReturnSeriesArgs(returns=[0.01] * 30))
    # No downside → sortino is None
    assert out["sortino"] is None


def test_sortino_with_some_negative() -> None:
    returns = [0.02 if i % 3 != 0 else -0.01 for i in range(30)]
    out = _sortino(ReturnSeriesArgs(returns=returns))
    assert out["sortino"] is not None
    assert out["sortino"] > 0


# ---- _calmar ---------------------------------------------------------------


def test_calmar_basic() -> None:
    out = _calmar(CalmarArgs(total_return_pct=100.0, max_drawdown_pct=20.0, years_observed=4.0))
    # CAGR = (2.0)^(1/4) - 1 = 0.189 ≈ 18.9%; calmar = 18.9 / 20 = 0.945
    assert out["calmar"] is not None
    assert 0.9 < out["calmar"] < 1.0


def test_calmar_zero_dd_returns_none() -> None:
    # Pydantic catches zero DD (gt=0), so this should raise
    import pytest

    with pytest.raises(ValueError):
        CalmarArgs(total_return_pct=10.0, max_drawdown_pct=0.0)


def test_calmar_one_year_no_compounding() -> None:
    out = _calmar(CalmarArgs(total_return_pct=20.0, max_drawdown_pct=10.0, years_observed=1.0))
    # CAGR = 20%, calmar = 2.0
    assert out["calmar"] == 2.0
    assert out["cagr_pct"] == 20.0


# ---- _information_ratio ---------------------------------------------------


def test_information_ratio_zero_tracking_error() -> None:
    out = _information_ratio(
        InformationRatioArgs(
            strategy_returns=[0.01] * 30, benchmark_returns=[0.01] * 30
        )
    )
    assert out["information_ratio"] is None


def test_information_ratio_strategy_beats_benchmark() -> None:
    strat = [0.02 if i % 2 == 0 else 0.01 for i in range(30)]
    bench = [0.01] * 30
    out = _information_ratio(
        InformationRatioArgs(strategy_returns=strat, benchmark_returns=bench)
    )
    assert out["information_ratio"] is not None
    assert out["information_ratio"] > 0


# ---- _omega ----------------------------------------------------------------


def test_omega_basic() -> None:
    # 5 gains of 1+ + 5 losses of -1 → omega = 1
    out = _omega(OmegaArgs(returns=[1.0] * 5 + [-1.0] * 5, threshold=0.0))
    assert out["omega"] == 1.0


def test_omega_all_above_threshold() -> None:
    out = _omega(OmegaArgs(returns=[1.0] * 10, threshold=0.0))
    assert out["omega"] is None


def test_omega_skewed_positive() -> None:
    out = _omega(OmegaArgs(returns=[10.0] + [-1.0] * 9))
    # Gains 10, losses 9 → omega ≈ 1.111
    assert out["omega"] is not None
    assert abs(out["omega"] - 10.0 / 9.0) < 0.001


# ---- _drawdowns / _pain_index / _ulcer_index -----------------------------


def test_drawdowns_monotonic_up_all_zero() -> None:
    curve = [float(i + 1) for i in range(10)]
    dds = _drawdowns(curve)
    assert all(d == 0 for d in dds)


def test_drawdowns_peak_and_trough() -> None:
    curve = [1.0, 2.0, 1.0, 2.0]
    dds = _drawdowns(curve)
    # Peak 2, trough 1 → 0.5
    assert max(dds) == 0.5


def test_pain_index_smoke() -> None:
    curve = [1.0, 2.0, 1.0, 2.0, 1.0]
    out = _pain_index(curve)
    assert out["pain_index"] > 0
    assert out["max_drawdown"] == 0.5


def test_ulcer_index_smoke() -> None:
    curve = [1.0, 2.0, 1.0, 2.0, 1.0]
    out = _ulcer_index(curve)
    assert out["ulcer_index"] > 0
    # Ulcer >= pain since it's the RMS, but both bounded by max DD
    assert out["max_drawdown"] == 0.5


def test_pain_index_no_drawdown_zero() -> None:
    curve = [1.0, 2.0, 3.0, 4.0]
    out = _pain_index(curve)
    assert out["pain_index"] == 0.0
