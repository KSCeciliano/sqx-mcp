"""Adversarial-input tests for priority modules.

Every helper in the priority modules (ratios, tail_risk, overfit_diag,
stats_extra, regime, calendar_effects, stress_test, montecarlo,
portfolio_opt) should either:
  (a) raise NumericValidationError when given NaN / Inf in a float list,
  (b) return a well-formed result on edge inputs (constant series, extreme
      magnitudes, length-at-boundary).

These tests run the helpers directly (bypass Pydantic) to confirm the
guards inside each function. The MCP layer wraps NumericValidationError
into {"ok": False, "error": ...} so the tool never raises to the
transport.
"""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.portfolio_opt import (
    _equal_weight,
    _inverse_vol,
    _min_variance,
    _risk_parity,
)
from sq_mcp.tools.ratios import (
    CalmarArgs,
    InformationRatioArgs,
    OmegaArgs,
    ReturnSeriesArgs,
    _information_ratio,
    _omega,
    _pain_index,
    _sharpe,
    _sortino,
    _ulcer_index,
)
from sq_mcp.tools.regime import (
    _classify_trend,
    _classify_volatility,
    _pnl_split,
)
from sq_mcp.tools.stats_extra import (
    _autocorrelation,
    _irr,
    _jarque_bera,
    _kurtosis,
    _runs_test,
    _skewness,
    _summary,
    _time_weighted_return,
)
from sq_mcp.tools.stress_test import _metrics
from sq_mcp.tools.tail_risk import (
    _cvar,
    _gain_to_pain,
    _historical_var,
    _parametric_var,
    _tail_ratio,
)

# ============================================================================
# NaN / Inf rejection — every priority helper must reject these
# ============================================================================


@pytest.mark.parametrize(
    "fn,arg",
    [
        (_historical_var, ([float("nan")] * 20, 0.95)),
        (_cvar, ([1.0, float("inf")] + [0.5] * 18, 0.95)),
        (_parametric_var, ([float("nan")] * 20, 0.95)),
        (_tail_ratio, ([float("inf")] * 20,)),
        (_gain_to_pain, ([float("nan"), 1.0] + [0.5] * 18,)),
        (_skewness, ([float("inf")] * 20,)),
        (_kurtosis, ([float("nan")] * 20,)),
        (_jarque_bera, ([float("inf"), 1.0] + [0.5] * 18,)),
        (_autocorrelation, ([float("nan"), 1.0] + [0.5] * 18, 1)),
        (_runs_test, ([float("inf")] * 20,)),
        (_summary, ([float("nan")] * 20,)),
        (_irr, ([-100.0, float("nan"), 110.0], 0.1, 100, 1e-8)),
        (_time_weighted_return, ([float("inf"), 1.0],)),
        (_pain_index, ([float("nan")] * 20,)),
        (_ulcer_index, ([float("inf")] * 20,)),
        (_classify_volatility, ([float("nan")] * 50, 10)),
        (_classify_trend, ([float("inf")] * 50, 10)),
        (_pnl_split, ([float("nan")] * 20, ["bull"] * 20)),
    ],
)
def test_priority_helpers_reject_nan_inf(fn, arg) -> None:  # noqa: ANN001
    with pytest.raises(NumericValidationError):
        fn(*arg)


def test_sharpe_rejects_nan() -> None:
    args = ReturnSeriesArgs(returns=[float("nan")] + [0.01] * 19)
    with pytest.raises(NumericValidationError):
        _sharpe(args)


def test_sortino_rejects_nan() -> None:
    args = ReturnSeriesArgs(returns=[float("inf")] + [0.01] * 19)
    with pytest.raises(NumericValidationError):
        _sortino(args)


def test_information_ratio_rejects_nan_either_side() -> None:
    a = InformationRatioArgs(
        strategy_returns=[float("nan")] + [0.01] * 19,
        benchmark_returns=[0.01] * 20,
    )
    with pytest.raises(NumericValidationError):
        _information_ratio(a)


def test_omega_rejects_nan() -> None:
    args = OmegaArgs(returns=[float("inf")] + [0.01] * 19)
    with pytest.raises(NumericValidationError):
        _omega(args)


def test_portfolio_helpers_reject_nan() -> None:
    bad = {"A": [1.0, float("nan"), 2.0] * 10, "B": [0.1] * 30}
    with pytest.raises(NumericValidationError):
        _equal_weight(bad)
    with pytest.raises(NumericValidationError):
        _inverse_vol(bad)
    with pytest.raises(NumericValidationError):
        _risk_parity(bad, max_iter=50, tol=1e-6)
    with pytest.raises(NumericValidationError):
        _min_variance(bad, max_iter=50, tol=1e-6)


# ============================================================================
# Constant series — zero variance edge
# ============================================================================


def test_sharpe_constant_returns_none() -> None:
    args = ReturnSeriesArgs(returns=[0.01] * 20)
    result = _sharpe(args)
    assert result["sharpe"] is None
    assert "std is zero" in result["note"]


def test_information_ratio_constant_active() -> None:
    args = InformationRatioArgs(
        strategy_returns=[0.01] * 20,
        benchmark_returns=[0.01] * 20,
    )
    result = _information_ratio(args)
    assert result["information_ratio"] is None


def test_omega_no_losses_returns_none() -> None:
    args = OmegaArgs(returns=[1.0] * 20)
    result = _omega(args)
    assert result["omega"] is None


def test_calmar_zero_drawdown_rejected_by_pydantic() -> None:
    # Pydantic gt=0 on max_drawdown_pct catches the degenerate case
    from pydantic import ValidationError as PydanticVE
    with pytest.raises(PydanticVE):
        CalmarArgs(total_return_pct=10.0, max_drawdown_pct=0.0, years_observed=1.0)


def test_pain_index_flat_curve_zero() -> None:
    result = _pain_index([100.0] * 20)
    assert result["pain_index"] == 0.0


def test_ulcer_flat_curve_zero() -> None:
    result = _ulcer_index([100.0] * 20)
    assert result["ulcer_index"] == 0.0


def test_skewness_constant_returns_zero() -> None:
    result = _skewness([5.0] * 30)
    assert result["skewness"] == 0.0


def test_kurtosis_constant_returns_zero() -> None:
    result = _kurtosis([5.0] * 30)
    assert result["excess_kurtosis"] == 0.0


def test_autocorrelation_constant_returns_zero() -> None:
    result = _autocorrelation([5.0] * 30, 1)
    assert result["autocorrelation"] == 0.0


# ============================================================================
# Extreme magnitudes — should not overflow
# ============================================================================


def test_historical_var_extreme_magnitudes() -> None:
    returns = [1e10, -1e10] * 50  # finite but huge
    result = _historical_var(returns, 0.95)
    assert result["var"] is not None
    assert math.isfinite(result["var"])


def test_summary_extreme_magnitudes() -> None:
    series = [1e15, -1e15] * 50
    result = _summary(series)
    assert math.isfinite(result["mean"])
    assert math.isfinite(result["std"])


def test_stress_metrics_extreme() -> None:
    trades = [1e10, -1e10] * 100
    result = _metrics(trades)
    assert math.isfinite(result["net"])
    assert math.isfinite(result["max_drawdown_abs"])


# ============================================================================
# Empty / length-at-boundary inputs
# ============================================================================


def test_stress_metrics_empty_returns_zero_fields() -> None:
    result = _metrics([])
    assert result["n_trades"] == 0
    assert result["net"] == 0.0
    assert result["max_drawdown_abs"] == 0.0
    # Should not crash on missing win_rate / average_trade
    assert "win_rate" in result
    assert "average_trade" in result


def test_pnl_split_mismatched_lengths_truncates() -> None:
    # pnls longer than regimes — should silently truncate
    pnls = [1.0, 2.0, 3.0, 4.0, 5.0]
    regimes = ["bull", "bear"]
    result = _pnl_split(pnls, regimes)
    assert result["n_samples"] == 2


def test_irr_no_sign_change_returns_none() -> None:
    result = _irr([100.0, 200.0, 300.0], 0.1, 100, 1e-8)
    assert result["irr"] is None


def test_irr_single_period_returns_correct() -> None:
    # -100 now, +110 next period → 10%
    result = _irr([-100.0, 110.0], 0.1, 100, 1e-8)
    assert abs(result["irr_pct"] - 10.0) < 0.001


# ============================================================================
# Determinism — same input, same seed → same output
# ============================================================================


def test_montecarlo_seeded_determinism() -> None:
    from sq_mcp.tools.montecarlo import _run_simulation
    returns = [0.01, -0.005, 0.02, -0.01, 0.015] * 4
    a = _run_simulation(returns, n_paths=200, n_steps=20, seed=42)
    b = _run_simulation(returns, n_paths=200, n_steps=20, seed=42)
    assert a["terminal_return_pct"] == b["terminal_return_pct"]
    assert a["max_drawdown_pct"] == b["max_drawdown_pct"]


def test_montecarlo_different_seeds_differ() -> None:
    from sq_mcp.tools.montecarlo import _run_simulation
    returns = [0.01, -0.005, 0.02, -0.01, 0.015] * 4
    a = _run_simulation(returns, n_paths=200, n_steps=20, seed=1)
    b = _run_simulation(returns, n_paths=200, n_steps=20, seed=2)
    # Some metric should differ — tail percentiles are deterministic per seed
    assert a["terminal_return_pct"] != b["terminal_return_pct"]


# ============================================================================
# Calendar effects — bad timestamps + NaN PnL via Pydantic
# ============================================================================


def test_calendar_pnl_rejects_nan_via_pydantic() -> None:
    from pydantic import ValidationError as PydanticVE

    from sq_mcp.tools.calendar_effects import TradeWithTime
    with pytest.raises(PydanticVE):
        TradeWithTime(when="2025-01-01T00:00:00", pnl=float("nan"))


def test_calendar_pnl_rejects_inf_via_pydantic() -> None:
    from pydantic import ValidationError as PydanticVE

    from sq_mcp.tools.calendar_effects import TradeWithTime
    with pytest.raises(PydanticVE):
        TradeWithTime(when="2025-01-01T00:00:00", pnl=float("inf"))


def test_calendar_unparseable_timestamp_counted() -> None:
    from sq_mcp.tools.calendar_effects import _hour_table
    trades = [
        {"when": "not-a-date", "pnl": 1.0},
        {"when": "2025-01-01T12:00:00", "pnl": 2.0},
    ]
    result = _hour_table(trades)
    assert result["skipped_unparseable"] == 1


# ============================================================================
# Overfit diagnostics — pydantic-level finite guard
# ============================================================================


def test_dsr_args_reject_nan() -> None:
    from pydantic import ValidationError as PydanticVE

    from sq_mcp.tools.overfit_diag import DeflatedSharpeArgs
    with pytest.raises(PydanticVE):
        DeflatedSharpeArgs(
            sharpe_observed=float("nan"),
            n_trials=10,
            n_observations=252,
        )


def test_pbo_pair_rejects_inf() -> None:
    from pydantic import ValidationError as PydanticVE

    from sq_mcp.tools.overfit_diag import PBOPair
    with pytest.raises(PydanticVE):
        PBOPair(is_sharpe=float("inf"), oos_sharpe=1.0)
