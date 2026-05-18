"""Fractional differentiation — stationary returns while preserving memory.

Standard integer differencing (returns = price[t] - price[t-1]) makes a
series stationary but destroys all long-memory information. Fractional
differentiation (Lopez de Prado, AFML Ch.5) parametrizes the differencing
order ``d`` in (0,1) so the series becomes stationary at the *minimum*
``d`` that an ADF-style test accepts. This preserves the maximum amount of
predictive memory while still producing stationary input for ML models.

This module implements the **fixed-width window** variant: weights are
computed up to a tolerance threshold, then convolved with the price
series.

Tools:

- ``fracdiff_weights`` — compute the binomial-coefficient weights for a
  given ``d`` and a tolerance for truncation.
- ``fracdiff_apply`` — apply a given ``d`` to a price series, returning
  the fractionally-differentiated series and the effective window length.
- ``fracdiff_find_min_d`` — sweep ``d`` from 0.1 to 0.95 and return the
  minimum ``d`` whose ADF-like statistic (computed pure-Python) crosses a
  user-supplied stationarity threshold.

No scipy. The ADF approximation here is a simple lag-1 regression with
heuristic critical values — adequate for picking ``d`` but not a
publishable hypothesis test.
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


class FracDiffWeightsArgs(BaseModel):
    d: float = Field(..., gt=0.0, lt=2.0)
    tolerance: float = Field(1e-5, gt=0.0, lt=1.0)
    max_window: int = Field(2_000, ge=10, le=20_000)


class FracDiffApplyArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=200_000)
    d: float = Field(..., gt=0.0, lt=2.0)
    tolerance: float = Field(1e-5, gt=0.0, lt=1.0)
    max_window: int = Field(2_000, ge=10, le=20_000)


class FracDiffSweepArgs(BaseModel):
    series: list[float] = Field(..., min_length=50, max_length=200_000)
    d_min: float = Field(0.1, gt=0.0, lt=1.0)
    d_max: float = Field(0.95, gt=0.0, lt=1.0)
    n_steps: int = Field(18, ge=4, le=100)
    tolerance: float = Field(1e-4, gt=0.0, lt=1.0)
    max_window: int = Field(2_000, ge=10, le=20_000)
    stationarity_threshold: float = Field(-3.0, le=0.0)


def _fracdiff_weights(d: float, tolerance: float, max_window: int) -> list[float]:
    """Compute fractional-differencing weights via the Lopez de Prado recursion:

        ω_0 = 1
        ω_k = -ω_{k-1} · (d - k + 1) / k

    Truncate when |ω_k| falls below tolerance. For d=1 this collapses to
    [-1, 1] (after reversal) which is the standard first-difference filter.
    """
    if d <= 0:
        return [1.0]
    weights = [1.0]
    for k in range(1, max_window + 1):
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < tolerance:
            break
        weights.append(w)
    # Reverse so that index 0 is the oldest sample (most-negative-index weight last)
    return weights[::-1]


def _fracdiff_apply(
    series: list[float], d: float, tolerance: float, max_window: int
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    weights = _fracdiff_weights(d, tolerance, max_window)
    window = len(weights)
    if window > len(series):
        return {
            "differentiated": [],
            "window": window,
            "note": "window longer than series — no output",
            "n_input": len(series),
        }
    out: list[float] = []
    for t in range(window - 1, len(series)):
        s = 0.0
        for k, w in enumerate(weights):
            s += w * series[t - window + 1 + k]
        out.append(s)
    return {
        "differentiated": out,
        "window": window,
        "n_input": len(series),
        "n_output": len(out),
        "d": d,
        "tolerance": tolerance,
    }


def _heuristic_adf(series: list[float]) -> float:
    """Compute a heuristic Dickey-Fuller-like test stat:
        Δy_t = α + β · y_{t-1} + ε
    Stat = β / SE(β). More negative = more stationary.

    Critical values (rough, large-sample): -1.95 (10%), -2.86 (5%), -3.43 (1%).
    """
    n = len(series)
    if n < 30:
        return 0.0
    dy = [series[t] - series[t - 1] for t in range(1, n)]
    y_lag = series[:-1]
    m = len(dy)
    mean_x = sum(y_lag) / m
    mean_y = sum(dy) / m
    num = sum((y_lag[i] - mean_x) * (dy[i] - mean_y) for i in range(m))
    den = sum((y_lag[i] - mean_x) ** 2 for i in range(m))
    if den == 0:
        return 0.0
    beta = num / den
    # Residuals + SE of beta
    pred = [mean_y + beta * (y_lag[i] - mean_x) for i in range(m)]
    rss = sum((dy[i] - pred[i]) ** 2 for i in range(m))
    sigma2 = rss / max(1, m - 2)
    se = math.sqrt(sigma2 / den) if den > 0 else float("inf")
    if se == 0 or not math.isfinite(se):
        return 0.0
    return beta / se


def _fracdiff_sweep(
    series: list[float],
    d_min: float,
    d_max: float,
    n_steps: int,
    tolerance: float,
    max_window: int,
    threshold: float,
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    results: list[dict[str, Any]] = []
    step = (d_max - d_min) / max(1, n_steps - 1)
    min_d_found: float | None = None
    for i in range(n_steps):
        d = d_min + i * step
        res = _fracdiff_apply(series, d, tolerance, max_window)
        out = res.get("differentiated") or []
        if len(out) < 30:
            results.append({"d": round(d, 4), "adf": None, "n_output": len(out)})
            continue
        adf = _heuristic_adf(out)
        results.append({
            "d": round(d, 4),
            "adf_stat": round(adf, 4),
            "n_output": len(out),
            "stationary": adf < threshold,
        })
        if min_d_found is None and adf < threshold:
            min_d_found = d
    return {
        "min_stationary_d": round(min_d_found, 4) if min_d_found is not None else None,
        "threshold": threshold,
        "sweep": results,
        "interpretation": (
            "found_min_d" if min_d_found is not None
            else "no_d_satisfies_threshold"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compute the fixed-width-window weights for fractional "
            "differentiation order d. Weights truncate when |w_k| < tolerance "
            "(default 1e-5). Returns the weight vector and its length. "
            "Read-only."
        )
    )
    async def fracdiff_weights(args: FracDiffWeightsArgs) -> dict:
        weights = _fracdiff_weights(args.d, args.tolerance, args.max_window)
        return {
            "ok": True,
            "weights": weights,
            "window": len(weights),
            "d": args.d,
            "tolerance": args.tolerance,
        }

    @mcp.tool(
        description=(
            "Apply fractional differentiation order d to a price series. "
            "Returns the differentiated series (shorter than input by window-1 "
            "samples) plus the effective window length. Use to produce "
            "stationary input for ML without destroying long-memory signal."
        )
    )
    async def fracdiff_apply(args: FracDiffApplyArgs) -> dict:
        try:
            return {
                "ok": True,
                **_fracdiff_apply(args.series, args.d, args.tolerance, args.max_window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Sweep d from d_min to d_max and return the minimum d that makes "
            "the series stationary by a heuristic Dickey-Fuller statistic "
            "(threshold default -3.0, roughly 1% large-sample critical "
            "value). Use to pick the smallest differencing order that "
            "preserves the most memory. Read-only."
        )
    )
    async def fracdiff_find_min_d(args: FracDiffSweepArgs) -> dict:
        try:
            return {
                "ok": True,
                **_fracdiff_sweep(
                    args.series,
                    args.d_min,
                    args.d_max,
                    args.n_steps,
                    args.tolerance,
                    args.max_window,
                    args.stationarity_threshold,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "FracDiffApplyArgs",
    "FracDiffSweepArgs",
    "FracDiffWeightsArgs",
    "_fracdiff_apply",
    "_fracdiff_sweep",
    "_fracdiff_weights",
    "_heuristic_adf",
    "register",
]
