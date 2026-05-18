"""Tail-risk metrics: VaR, CVaR/expected shortfall, tail ratio, gain-to-pain.

Tools:

- ``tail_risk_historical_var`` — Historical Value at Risk at confidence
  level c (e.g. 0.95). Returns the worst c-quantile loss observed.
- ``tail_risk_cvar`` — Conditional VaR / Expected Shortfall — average loss
  in the tail beyond VaR. More robust than VaR for fat-tailed returns.
- ``tail_risk_parametric_var`` — Parametric VaR assuming Normal returns
  (mean + std × z-score). Compare against historical to gauge tail
  fatness.
- ``tail_risk_tail_ratio`` — abs(p95) / abs(p5) of returns. Symmetric
  distributions give ~1.0; positive skew >1.0.
- ``tail_risk_gain_to_pain`` — sum of positive returns / abs(sum of
  negative returns). Higher is better.

All math pure Python. Read-only.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class ReturnSeriesArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=200_000)
    confidence: float = Field(0.95, gt=0.0, lt=1.0)


class TailRatioArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=200_000)


def _percentile(sorted_xs: list[float], p: float) -> float:
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    idx = (len(sorted_xs) - 1) * (p / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)


def _historical_var(returns: list[float], confidence: float) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns")
    sorted_r = sorted(returns)
    q = (1.0 - confidence) * 100.0
    var = -_percentile(sorted_r, q)
    return {
        "var": round(var, 6),
        "confidence": confidence,
        "tail_size": sum(1 for r in returns if var > 0 and r <= -var),
        "sample_size": len(returns),
    }


def _cvar(returns: list[float], confidence: float) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns")
    sorted_r = sorted(returns)
    cutoff_idx = max(1, int(round((1.0 - confidence) * len(sorted_r))))
    tail = sorted_r[:cutoff_idx]
    if not tail:
        return {"cvar": None, "note": "empty tail"}
    var_value = -tail[-1]
    cvar = -(sum(tail) / len(tail))
    return {
        "cvar": round(cvar, 6),
        "var": round(var_value, 6),
        "tail_size": len(tail),
        "tail_share_pct": round(100.0 * len(tail) / len(returns), 4),
        "confidence": confidence,
    }


def _parametric_var(returns: list[float], confidence: float) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns")
    n = len(returns)
    mean = sum(returns) / n
    if n < 2:
        return {"var": None, "note": "need ≥2 samples"}
    var_pop = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var_pop)
    z = _inv_norm_cdf(1.0 - confidence)
    var = -(mean + z * std)
    return {
        "var": round(var, 6),
        "mean": round(mean, 6),
        "std": round(std, 6),
        "z_score": round(z, 6),
        "confidence": confidence,
        "note": "assumes Normal returns — compare to historical_var for tail-fatness check",
    }


def _inv_norm_cdf(p: float) -> float:
    """Beasley-Springer-Moro approximation to the inverse normal CDF."""
    if p <= 0.0 or p >= 1.0:
        if p <= 0.0:
            return -math.inf
        return math.inf
    # Beasley-Springer-Moro (sufficient for VaR work)
    a = [
        -3.969683028665376e+01,
        2.209460984245205e+02,
        -2.759285104469687e+02,
        1.383577518672690e+02,
        -3.066479806614716e+01,
        2.506628277459239e+00,
    ]
    b = [
        -5.447609879822406e+01,
        1.615858368580409e+02,
        -1.556989798598866e+02,
        6.680131188771972e+01,
        -1.328068155288572e+01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e+00,
        -2.549732539343734e+00,
        4.374664141464968e+00,
        2.938163982698783e+00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e+00,
        3.754408661907416e+00,
    ]
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            ((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]
        ) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    )


def _tail_ratio(returns: list[float]) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns")
    sorted_r = sorted(returns)
    p95 = _percentile(sorted_r, 95.0)
    p5 = _percentile(sorted_r, 5.0)
    if p5 == 0:
        return {"tail_ratio": None, "note": "p5 is zero — undefined"}
    ratio = abs(p95) / abs(p5)
    return {
        "tail_ratio": round(ratio, 4),
        "p95": round(p95, 6),
        "p5": round(p5, 6),
        "interpretation": (
            "fat upside" if ratio > 1.2
            else "fat downside" if ratio < 0.8
            else "approximately symmetric"
        ),
    }


def _gain_to_pain(returns: list[float]) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns")
    gains = sum(r for r in returns if r > 0)
    losses = abs(sum(r for r in returns if r < 0))
    if losses == 0:
        return {"gain_to_pain": None, "note": "no losing periods"}
    gtp = gains / losses
    return {
        "gain_to_pain": round(gtp, 4),
        "total_gains": round(gains, 6),
        "total_losses_abs": round(losses, 6),
        "n_periods": len(returns),
        "interpretation": (
            "robust" if gtp > 2.0
            else "acceptable" if gtp > 1.0
            else "weak"
        ),
    }


def _safe(fn, *fn_args):  # noqa: ANN001, ANN002
    try:
        return {"ok": True, **fn(*fn_args)}
    except NumericValidationError as exc:
        return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Historical Value at Risk at the given confidence level "
            "(default 0.95). Returns the worst 5% (1−c) loss observed in the "
            "return series. Non-parametric — no distributional assumption. "
            "Read-only."
        )
    )
    async def tail_risk_historical_var(args: ReturnSeriesArgs) -> dict:
        return _safe(_historical_var, args.returns, args.confidence)

    @mcp.tool(
        description=(
            "Conditional Value at Risk (Expected Shortfall) — the average loss "
            "in the tail beyond VaR. More informative than VaR alone for "
            "fat-tailed distributions. Read-only."
        )
    )
    async def tail_risk_cvar(args: ReturnSeriesArgs) -> dict:
        return _safe(_cvar, args.returns, args.confidence)

    @mcp.tool(
        description=(
            "Parametric VaR assuming Normal returns. Compare against historical "
            "VaR — if historical >> parametric, returns have fat tails and "
            "Normal assumption underestimates risk."
        )
    )
    async def tail_risk_parametric_var(args: ReturnSeriesArgs) -> dict:
        return _safe(_parametric_var, args.returns, args.confidence)

    @mcp.tool(
        description=(
            "Tail ratio = |p95| / |p5|. >1 means fatter upside tail; <1 means "
            "fatter downside tail; ~1 means symmetric. Useful for evaluating "
            "skewness of P&L distribution."
        )
    )
    async def tail_risk_tail_ratio(args: TailRatioArgs) -> dict:
        return _safe(_tail_ratio, args.returns)

    @mcp.tool(
        description=(
            "Gain-to-pain ratio = sum(positive returns) / |sum(negative returns)|. "
            ">2.0 is robust, <1.0 is weak. Simpler alternative to Sharpe when "
            "you care about edge over losses, not volatility."
        )
    )
    async def tail_risk_gain_to_pain(args: TailRatioArgs) -> dict:
        return _safe(_gain_to_pain, args.returns)


__all__ = [
    "ReturnSeriesArgs",
    "TailRatioArgs",
    "_cvar",
    "_gain_to_pain",
    "_historical_var",
    "_inv_norm_cdf",
    "_parametric_var",
    "_tail_ratio",
    "register",
]
