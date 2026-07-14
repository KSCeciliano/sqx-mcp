"""Risk-adjusted return ratios — explicit-input calculators.

The existing `analytics.py` module computes Sharpe/Sortino from a
strategy's parsed metrics. This module exposes them with explicit
arrays/inputs so the caller can use them with any source (MT5 report,
custom CSV, manually-entered numbers).

Tools:

- ``ratios_sharpe`` — annualized Sharpe from a return series.
- ``ratios_sortino`` — Sortino (downside deviation in denominator).
- ``ratios_calmar`` — annualized return / |max drawdown|.
- ``ratios_information`` — Sharpe-like ratio of (strategy - benchmark)
  returns; useful for evaluating vs buy-and-hold.
- ``ratios_mar`` — CAGR / max drawdown ratio (Managed Account Reports
  metric).
- ``ratios_omega`` — Omega ratio at a threshold = sum(gains above
  threshold) / sum(losses below threshold).
- ``ratios_pain_index`` — average drawdown across the equity curve
  (vs `max` drawdown).
- ``ratios_ulcer_index`` — RMS of drawdowns; penalizes deep and
  prolonged drawdowns more than max-DD.

All math pure Python — no numpy. Read-only.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    safe_div,
    validate_finite_floats,
)

# ---- argument schemas ------------------------------------------------------


class ReturnSeriesArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=100_000)
    risk_free_rate: float = Field(0.0, ge=-0.5, le=1.0)  # annual %
    periods_per_year: int = Field(252, ge=1, le=525_600)  # 252 trading days, 252*8 hours, etc.


class CalmarArgs(BaseModel):
    total_return_pct: float
    max_drawdown_pct: float = Field(..., gt=0, le=100)
    years_observed: float = Field(1.0, gt=0)


class InformationRatioArgs(BaseModel):
    strategy_returns: list[float] = Field(..., min_length=10, max_length=100_000)
    benchmark_returns: list[float] = Field(..., min_length=10, max_length=100_000)
    periods_per_year: int = Field(252, ge=1, le=525_600)


class OmegaArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=100_000)
    threshold: float = 0.0


class EquityCurveArgs(BaseModel):
    equity_curve: list[float] = Field(..., min_length=10, max_length=100_000)


# ---- helpers ---------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], *, sample: bool = True) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    denom = (len(xs) - 1) if sample else len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / denom)


def _downside_std(xs: list[float], threshold: float = 0.0) -> float:
    downside = [x - threshold for x in xs if x < threshold]
    if len(downside) < 2:
        return 0.0
    return math.sqrt(sum(d * d for d in downside) / (len(downside) - 1))


def _sharpe(args: ReturnSeriesArgs) -> dict[str, Any]:
    validate_finite_floats(args.returns, name="returns")
    rf_per_period = args.risk_free_rate / args.periods_per_year
    excess = [r - rf_per_period for r in args.returns]
    mean_excess = _mean(excess)
    std_excess = _std(excess)
    if std_excess <= 1e-12:
        return {"sharpe": None, "note": "std is zero — no signal"}
    sharpe_period = safe_div(mean_excess, std_excess)
    if sharpe_period is None:
        return {"sharpe": None, "note": "non-finite ratio"}
    sharpe_annual = sharpe_period * math.sqrt(args.periods_per_year)
    return {
        "sharpe": round(sharpe_annual, 4),
        "sharpe_per_period": round(sharpe_period, 4),
        "mean_excess_return_per_period": round(mean_excess, 6),
        "std_excess_return_per_period": round(std_excess, 6),
        "periods_per_year": args.periods_per_year,
    }


def _sortino(args: ReturnSeriesArgs) -> dict[str, Any]:
    validate_finite_floats(args.returns, name="returns")
    rf_per_period = args.risk_free_rate / args.periods_per_year
    excess = [r - rf_per_period for r in args.returns]
    mean_excess = _mean(excess)
    d_std = _downside_std(args.returns, threshold=rf_per_period)
    if d_std == 0:
        return {"sortino": None, "note": "downside std is zero (no negative excess)"}
    sortino_period = mean_excess / d_std
    sortino_annual = sortino_period * math.sqrt(args.periods_per_year)
    return {
        "sortino": round(sortino_annual, 4),
        "sortino_per_period": round(sortino_period, 4),
        "mean_excess_return_per_period": round(mean_excess, 6),
        "downside_std_per_period": round(d_std, 6),
    }


def _calmar(args: CalmarArgs) -> dict[str, Any]:
    cagr = ((1.0 + args.total_return_pct / 100.0) ** (1.0 / args.years_observed) - 1.0) * 100.0
    if args.max_drawdown_pct == 0:
        return {"calmar": None, "note": "zero drawdown — calmar undefined"}
    calmar = cagr / args.max_drawdown_pct
    return {
        "calmar": round(calmar, 4),
        "cagr_pct": round(cagr, 4),
        "max_drawdown_pct": args.max_drawdown_pct,
        "years_observed": args.years_observed,
    }


def _information_ratio(args: InformationRatioArgs) -> dict[str, Any]:
    validate_finite_floats(args.strategy_returns, name="strategy_returns")
    validate_finite_floats(args.benchmark_returns, name="benchmark_returns")
    n = min(len(args.strategy_returns), len(args.benchmark_returns))
    s = args.strategy_returns[:n]
    b = args.benchmark_returns[:n]
    active = [si - bi for si, bi in zip(s, b, strict=False)]
    mean_active = _mean(active)
    std_active = _std(active)
    if std_active == 0:
        return {"information_ratio": None, "note": "no tracking error variance"}
    ir_period = mean_active / std_active
    ir_annual = ir_period * math.sqrt(args.periods_per_year)
    return {
        "information_ratio": round(ir_annual, 4),
        "tracking_error_per_period": round(std_active, 6),
        "mean_active_return_per_period": round(mean_active, 6),
        "n_periods": n,
    }


def _omega(args: OmegaArgs) -> dict[str, Any]:
    validate_finite_floats(args.returns, name="returns")
    gains = sum(max(0.0, r - args.threshold) for r in args.returns)
    losses = sum(max(0.0, args.threshold - r) for r in args.returns)
    if losses == 0:
        return {"omega": None, "note": "no observations below threshold"}
    omega = gains / losses
    return {
        "omega": round(omega, 4),
        "threshold": args.threshold,
        "gains_above": round(gains, 6),
        "losses_below": round(losses, 6),
    }


def _drawdowns(curve: list[float]) -> list[float]:
    """Per-sample drawdown percentages (peak-to-trough as a fraction of peak)."""
    if not curve:
        return []
    peak = curve[0]
    dds = []
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dds.append((peak - v) / peak)
        else:
            dds.append(0.0)
    return dds


def _pain_index(curve: list[float]) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    dds = _drawdowns(curve)
    if not dds:
        return {"pain_index": 0.0}
    return {
        "pain_index": round(sum(dds) / len(dds), 6),
        "max_drawdown": round(max(dds), 6),
        "samples": len(dds),
    }


def _ulcer_index(curve: list[float]) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    dds = _drawdowns(curve)
    if not dds:
        return {"ulcer_index": 0.0}
    rms = math.sqrt(sum(d * d for d in dds) / len(dds))
    return {
        "ulcer_index": round(rms, 6),
        "max_drawdown": round(max(dds), 6),
        "samples": len(dds),
    }


def _mar_ratio(args: CalmarArgs) -> dict[str, Any]:
    """MAR = CAGR / max DD, same as Calmar in practice (some sources use Lookback Window)."""
    return {**_calmar(args), "note": "MAR is functionally equivalent to Calmar at any period"}


# ---- MCP registration ------------------------------------------------------


def _safe(fn, args) -> dict[str, Any]:  # noqa: ANN001
    try:
        return {"ok": True, **fn(args)}
    except NumericValidationError as exc:
        return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Annualized Sharpe ratio from a return series. Subtracts the "
            "per-period risk-free rate, computes (mean / std) × sqrt(periods/yr). "
            "Read-only."
        )
    )
    async def ratios_sharpe(args: ReturnSeriesArgs) -> dict:
        return _safe(_sharpe, args)

    @mcp.tool(
        description=(
            "Annualized Sortino ratio — like Sharpe but uses downside deviation "
            "(only returns below the risk-free rate count). Rewards strategies "
            "with limited downside even if upside is volatile."
        )
    )
    async def ratios_sortino(args: ReturnSeriesArgs) -> dict:
        return _safe(_sortino, args)

    @mcp.tool(
        description=(
            "Calmar ratio: CAGR / |max drawdown|. CAGR computed from "
            "total_return_pct + years_observed. Useful when you don't have a "
            "full return series."
        )
    )
    async def ratios_calmar(args: CalmarArgs) -> dict:
        return _safe(_calmar, args)

    @mcp.tool(
        description=(
            "Information ratio — Sharpe-like ratio of active returns "
            "(strategy - benchmark). Use to evaluate a strategy vs buy-and-hold."
        )
    )
    async def ratios_information(args: InformationRatioArgs) -> dict:
        return _safe(_information_ratio, args)

    @mcp.tool(
        description=(
            "MAR ratio = CAGR / max drawdown (functionally equivalent to Calmar). "
            "Standard Managed Account Reports metric."
        )
    )
    async def ratios_mar(args: CalmarArgs) -> dict:
        return _safe(_mar_ratio, args)

    @mcp.tool(
        description=(
            "Omega ratio at a threshold = sum(gains above) / sum(losses below). "
            "More forgiving than Sharpe — captures skewness. Threshold often 0 "
            "(profit/loss) or risk_free_rate."
        )
    )
    async def ratios_omega(args: OmegaArgs) -> dict:
        return _safe(_omega, args)

    @mcp.tool(
        description=(
            "Pain Index — average per-sample drawdown across an equity curve. "
            "Captures the 'pain' of being underwater on average, not just at "
            "the worst point."
        )
    )
    async def ratios_pain_index(args: EquityCurveArgs) -> dict:
        try:
            return {"ok": True, **_pain_index(args.equity_curve)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Ulcer Index — RMS of per-sample drawdowns. Penalizes deep AND "
            "prolonged drawdowns more than max-DD. Lower = smoother ride."
        )
    )
    async def ratios_ulcer_index(args: EquityCurveArgs) -> dict:
        try:
            return {"ok": True, **_ulcer_index(args.equity_curve)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}
