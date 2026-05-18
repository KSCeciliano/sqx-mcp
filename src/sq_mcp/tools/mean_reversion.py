"""Mean-reversion diagnostics: Hurst exponent, OU half-life, CUSUM detector.

Mean-reversion strategies bet that a price series will return to its
long-run mean after deviations. The diagnostics here help decide whether
that bet is grounded:

- **Hurst exponent** (H): 0.5 = random walk, < 0.5 = mean-reverting,
  > 0.5 = trending. Computed via rescaled range (R/S) analysis.
- **Ornstein-Uhlenbeck half-life**: how many bars it typically takes
  for a deviation from the mean to decay by half. Practical input to
  position-holding-time decisions.
- **CUSUM change-point detector**: detects structural breaks in the
  mean of a series. Useful for live monitoring of strategy PnL —
  flags when the strategy's behavior has changed.
- **Augmented Dickey-Fuller (heuristic)**: re-exposed from frac_diff
  for stationarity testing on price-derived series.

Pure Python. No statsmodels.
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
from sq_mcp.tools.frac_diff import _heuristic_adf


class SeriesArgs(BaseModel):
    series: list[float] = Field(..., min_length=50, max_length=500_000)


class CusumArgs(BaseModel):
    series: list[float] = Field(..., min_length=20, max_length=500_000)
    threshold: float = Field(..., gt=0.0, le=1_000_000.0)
    reference: float | None = Field(
        None, description="Target mean; None = use series mean"
    )


def _hurst_rs(series: list[float]) -> dict[str, Any]:
    """Rescaled range (R/S) Hurst exponent.

    Steps: divide series into chunks of increasing size n; for each chunk,
    compute R/S (range / stddev). Slope of log(R/S) vs log(n) ≈ H.
    """
    validate_finite_floats(series, name="series")
    n_total = len(series)
    if n_total < 50:
        return {"hurst": None, "note": "need ≥50 samples"}
    # Pick chunk sizes geometrically: powers of 2 up to n/2
    chunks: list[int] = []
    size = 8
    while size <= n_total // 2:
        chunks.append(size)
        size *= 2
    if len(chunks) < 3:
        return {"hurst": None, "note": "series too short for R/S analysis"}
    log_n: list[float] = []
    log_rs: list[float] = []
    for chunk_size in chunks:
        n_chunks = n_total // chunk_size
        rs_values: list[float] = []
        for c in range(n_chunks):
            sub = series[c * chunk_size : (c + 1) * chunk_size]
            mean = sum(sub) / chunk_size
            deviations = [x - mean for x in sub]
            cum = [0.0]
            for d in deviations:
                cum.append(cum[-1] + d)
            r = max(cum) - min(cum)
            variance = sum((x - mean) ** 2 for x in sub) / chunk_size
            if variance <= 0:
                continue
            s = math.sqrt(variance)
            if r == 0 or s == 0:
                continue
            rs_values.append(r / s)
        if not rs_values:
            continue
        mean_rs = sum(rs_values) / len(rs_values)
        if mean_rs <= 0:
            continue
        log_n.append(math.log(chunk_size))
        log_rs.append(math.log(mean_rs))
    if len(log_n) < 3:
        return {"hurst": None, "note": "insufficient valid chunk sizes"}
    # Linear regression of log_rs on log_n; slope ≈ H
    nx = len(log_n)
    mean_x = sum(log_n) / nx
    mean_y = sum(log_rs) / nx
    num = sum((log_n[i] - mean_x) * (log_rs[i] - mean_y) for i in range(nx))
    den = sum((log_n[i] - mean_x) ** 2 for i in range(nx))
    hurst = num / den if den > 0 else 0.5
    return {
        "hurst": round(hurst, 4),
        "n_chunks_used": nx,
        "chunk_sizes": chunks[:nx],
        "interpretation": (
            "strongly_mean_reverting" if hurst < 0.4
            else "mean_reverting" if hurst < 0.48
            else "random_walk" if hurst <= 0.52
            else "trending" if hurst <= 0.6
            else "strongly_trending"
        ),
    }


def _ou_half_life(series: list[float]) -> dict[str, Any]:
    """Estimate Ornstein-Uhlenbeck half-life via lag-1 AR(1):

        Δy_t = θ (μ - y_{t-1}) + ε
        y_t = α + β · y_{t-1} + ε   where β = 1 - θ
        half_life = -log(2) / log(β)
    """
    validate_finite_floats(series, name="series")
    n = len(series)
    if n < 30:
        return {"half_life": None, "note": "need ≥30 samples"}
    dy = [series[t] - series[t - 1] for t in range(1, n)]
    y_lag = series[:-1]
    m = len(dy)
    mean_x = sum(y_lag) / m
    mean_y = sum(dy) / m
    num = sum((y_lag[i] - mean_x) * (dy[i] - mean_y) for i in range(m))
    den = sum((y_lag[i] - mean_x) ** 2 for i in range(m))
    if den == 0:
        return {"half_life": None, "note": "no variance in series"}
    theta = -num / den  # AR(1) slope = (1 - θ) − 1 = -θ
    if theta <= 0:
        return {
            "half_life": None,
            "theta": round(theta, 6),
            "note": "no mean-reverting tendency (θ ≤ 0)",
        }
    beta = 1.0 - theta
    if beta <= 0 or beta >= 1:
        return {
            "half_life": None,
            "theta": round(theta, 6),
            "beta": round(beta, 6),
            "note": "β outside (0, 1); half-life undefined",
        }
    half_life = -math.log(2.0) / math.log(beta)
    return {
        "half_life": round(half_life, 4),
        "theta": round(theta, 6),
        "beta": round(beta, 6),
        "interpretation": (
            "fast_mean_reversion" if half_life < 10
            else "moderate_mean_reversion" if half_life < 50
            else "slow_mean_reversion" if half_life < 500
            else "very_slow_or_unstable"
        ),
    }


def _cusum(
    series: list[float], threshold: float, reference: float | None
) -> dict[str, Any]:
    """Two-sided CUSUM change-point detector.

    Maintains S_h = max(0, S_h_prev + (x - μ)) and S_l = min(0, S_l_prev + (x - μ)).
    A break is signaled when |S| crosses threshold. Indices and signs of
    detected breaks are returned.
    """
    validate_finite_floats(series, name="series")
    mu = reference if reference is not None else sum(series) / len(series)
    s_high = 0.0
    s_low = 0.0
    detections: list[dict[str, Any]] = []
    for i, x in enumerate(series):
        s_high = max(0.0, s_high + (x - mu))
        s_low = min(0.0, s_low + (x - mu))
        if s_high >= threshold:
            detections.append({"index": i, "direction": "upward", "cusum_value": round(s_high, 6), "value": x})
            s_high = 0.0
        elif s_low <= -threshold:
            detections.append({"index": i, "direction": "downward", "cusum_value": round(s_low, 6), "value": x})
            s_low = 0.0
    return {
        "reference_mean": round(mu, 6),
        "threshold": threshold,
        "n_detections": len(detections),
        "detections": detections[:100],  # cap output
        "final_s_high": round(s_high, 6),
        "final_s_low": round(s_low, 6),
        "verdict": (
            "stable" if len(detections) == 0
            else "occasional_breaks" if len(detections) <= 3
            else "frequent_breaks"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Hurst exponent via rescaled-range (R/S) analysis. <0.5 = "
            "mean-reverting, ~0.5 = random walk, >0.5 = trending. Verdict "
            "buckets: strongly_mean_reverting / mean_reverting / random_walk "
            "/ trending / strongly_trending. Use to decide between "
            "mean-reversion vs trend-following strategies. Read-only."
        )
    )
    async def mean_reversion_hurst(args: SeriesArgs) -> dict:
        try:
            return {"ok": True, **_hurst_rs(args.series)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Estimate Ornstein-Uhlenbeck mean-reversion half-life — how "
            "many bars a deviation from the long-run mean takes to decay "
            "by 50%. Verdict: fast (<10), moderate (10-50), slow (50-500), "
            "very_slow_or_unstable. Returns None if no mean-reverting "
            "tendency is detected."
        )
    )
    async def mean_reversion_ou_half_life(args: SeriesArgs) -> dict:
        try:
            return {"ok": True, **_ou_half_life(args.series)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Two-sided CUSUM change-point detector for live PnL monitoring. "
            "Signals when cumulative deviation from a reference (default = "
            "series mean) crosses ±threshold. Returns detection indices + "
            "direction (upward/downward) + verdict (stable / occasional_ "
            "breaks / frequent_breaks)."
        )
    )
    async def mean_reversion_cusum_detector(args: CusumArgs) -> dict:
        try:
            return {
                "ok": True,
                **_cusum(args.series, args.threshold, args.reference),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Heuristic Augmented Dickey-Fuller stationarity test (re-exposed "
            "from frac_diff for general use). Returns the t-statistic; "
            "more negative = more stationary. Critical values approximately "
            "−1.95 (10%), −2.86 (5%), −3.43 (1%)."
        )
    )
    async def mean_reversion_adf_heuristic(args: SeriesArgs) -> dict:
        try:
            validate_finite_floats(args.series, name="series")
            stat = _heuristic_adf(args.series)
            return {
                "ok": True,
                "adf_statistic": round(stat, 6),
                "verdict": (
                    "stationary_1pct" if stat <= -3.43
                    else "stationary_5pct" if stat <= -2.86
                    else "stationary_10pct" if stat <= -1.95
                    else "non_stationary"
                ),
                "interpretation": (
                    "Series is stationary at conventional levels — "
                    "mean-reversion strategies are theoretically applicable."
                    if stat <= -2.86
                    else "Series shows unit-root behavior — mean-reversion "
                    "assumption is questionable."
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "CusumArgs",
    "SeriesArgs",
    "_cusum",
    "_hurst_rs",
    "_ou_half_life",
    "register",
]
