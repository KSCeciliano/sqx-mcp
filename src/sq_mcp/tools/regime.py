"""Market regime detection and regime-conditioned PnL split.

Tools to classify each sample of a time series into a regime (low/normal/
high volatility; bull/neutral/bear trend) and split a strategy's PnL across
those regimes. Useful for spotting strategies that only work in one regime.

Tools:

- ``regime_classify_volatility`` — given a price series, label each
  sample as low/normal/high vol using a rolling stddev tertile.
- ``regime_classify_trend`` — given a price series, label each sample as
  bull/neutral/bear using rolling slope.
- ``regime_pnl_split`` — given matched price and trade arrays, report
  PnL conditional on the regime label at each trade.
- ``regime_recommend_filter`` — given a regime-split PnL report,
  recommend regime filters (e.g. "trade only in low vol").

Pure Python, no numpy.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class VolatilityRegimeArgs(BaseModel):
    prices: list[float] = Field(..., min_length=20, max_length=500_000)
    window: int = Field(20, ge=5, le=500)


class TrendRegimeArgs(BaseModel):
    prices: list[float] = Field(..., min_length=20, max_length=500_000)
    window: int = Field(50, ge=5, le=500)


class RegimePnLSplitArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=10, max_length=200_000)
    trade_regimes: list[str] = Field(..., min_length=10, max_length=200_000)


class RecommendFilterArgs(BaseModel):
    regime_pnl: dict[str, dict[str, float]]
    metric: Literal["mean_pnl", "win_rate", "total_pnl"] = "mean_pnl"


def _rolling_std(prices: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(prices)
    if len(prices) < window:
        return out
    for i in range(window - 1, len(prices)):
        win = prices[i - window + 1 : i + 1]
        m = sum(win) / window
        var = sum((p - m) ** 2 for p in win) / (window - 1)
        out[i] = math.sqrt(var)
    return out


def _rolling_slope(prices: list[float], window: int) -> list[float | None]:
    """Slope of linear regression of prices on time, over a rolling window."""
    out: list[float | None] = [None] * len(prices)
    if len(prices) < window:
        return out
    xs = list(range(window))
    x_mean = sum(xs) / window
    x_var = sum((x - x_mean) ** 2 for x in xs)
    for i in range(window - 1, len(prices)):
        win = prices[i - window + 1 : i + 1]
        y_mean = sum(win) / window
        slope = sum((xs[k] - x_mean) * (win[k] - y_mean) for k in range(window))
        slope = slope / x_var if x_var > 0 else 0.0
        out[i] = slope
    return out


def _tertile_labels(values: list[float | None]) -> list[str]:
    """Classify each value into 'low'/'normal'/'high' by global tertile."""
    nums = sorted(v for v in values if v is not None)
    if len(nums) < 6:
        return ["unknown"] * len(values)
    t1 = nums[len(nums) // 3]
    t2 = nums[2 * len(nums) // 3]
    out: list[str] = []
    for v in values:
        if v is None:
            out.append("unknown")
        elif v < t1:
            out.append("low")
        elif v < t2:
            out.append("normal")
        else:
            out.append("high")
    return out


def _trend_labels(slopes: list[float | None]) -> list[str]:
    """Classify slopes into 'bear'/'neutral'/'bull' by sign and magnitude."""
    nums = sorted(s for s in slopes if s is not None)
    if len(nums) < 6:
        return ["unknown"] * len(slopes)
    t1 = nums[len(nums) // 3]  # bottom tertile cutoff
    t2 = nums[2 * len(nums) // 3]
    out: list[str] = []
    for s in slopes:
        if s is None:
            out.append("unknown")
        elif s < t1:
            out.append("bear")
        elif s < t2:
            out.append("neutral")
        else:
            out.append("bull")
    return out


def _classify_volatility(prices: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    stds = _rolling_std(prices, window)
    labels = _tertile_labels(stds)
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    return {
        "labels": labels,
        "stds": [None if s is None else round(s, 8) for s in stds],
        "window": window,
        "counts": counts,
    }


def _classify_trend(prices: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    slopes = _rolling_slope(prices, window)
    labels = _trend_labels(slopes)
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    return {
        "labels": labels,
        "slopes": [None if s is None else round(s, 8) for s in slopes],
        "window": window,
        "counts": counts,
    }


def _pnl_split(pnls: list[float], regimes: list[str]) -> dict[str, Any]:
    validate_finite_floats(pnls, name="trade_pnls")
    n = min(len(pnls), len(regimes))
    by_regime: dict[str, dict[str, Any]] = {}
    for i in range(n):
        r = regimes[i]
        bucket = by_regime.setdefault(
            r, {"n": 0, "wins": 0, "total_pnl": 0.0, "pnls": []}
        )
        bucket["n"] += 1
        bucket["total_pnl"] += pnls[i]
        if pnls[i] > 0:
            bucket["wins"] += 1
        bucket["pnls"].append(pnls[i])
    out: dict[str, dict[str, float]] = {}
    for r, b in by_regime.items():
        win_rate = (b["wins"] / b["n"]) if b["n"] > 0 else 0.0
        mean_pnl = (b["total_pnl"] / b["n"]) if b["n"] > 0 else 0.0
        out[r] = {
            "n": b["n"],
            "wins": b["wins"],
            "total_pnl": round(b["total_pnl"], 6),
            "mean_pnl": round(mean_pnl, 6),
            "win_rate": round(win_rate, 4),
        }
    return {
        "by_regime": out,
        "n_samples": n,
        "n_regimes": len(out),
    }


def _recommend_filter(
    regime_pnl: dict[str, dict[str, float]], metric: str
) -> dict[str, Any]:
    """Recommend which regimes to trade based on a sort metric."""
    if not regime_pnl:
        return {"recommendation": "no regime data"}
    ranked = sorted(
        regime_pnl.items(),
        key=lambda kv: kv[1].get(metric, 0.0),
        reverse=True,
    )
    top_value = ranked[0][1].get(metric, 0.0)
    keep = [r for r, m in ranked if m.get(metric, 0.0) >= 0]
    drop = [r for r, m in ranked if m.get(metric, 0.0) < 0]
    return {
        "metric": metric,
        "ranked": [{"regime": r, **{metric: m.get(metric, 0.0)}} for r, m in ranked],
        "recommend_trade": keep,
        "recommend_skip": drop,
        "best_regime": ranked[0][0] if ranked else None,
        "best_value": top_value,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Classify each sample of a price series into a volatility regime "
            "(low/normal/high) using a rolling stddev tertile. Window controls "
            "the smoothing — 20 bars is typical for daily, 50 for hourly. "
            "Read-only."
        )
    )
    async def regime_classify_volatility(args: VolatilityRegimeArgs) -> dict:
        try:
            return {"ok": True, **_classify_volatility(args.prices, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Classify each sample of a price series into a trend regime "
            "(bear/neutral/bull) using rolling linear-regression slope. "
            "Read-only."
        )
    )
    async def regime_classify_trend(args: TrendRegimeArgs) -> dict:
        try:
            return {"ok": True, **_classify_trend(args.prices, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Split trade PnLs across regime labels. Use to spot strategies that "
            "only work in one regime (e.g. only profitable in high-vol). Caller "
            "supplies trade_pnls and a parallel array of regime labels "
            "(e.g. from regime_classify_volatility, sampled at trade times). "
            "Read-only."
        )
    )
    async def regime_pnl_split(args: RegimePnLSplitArgs) -> dict:
        try:
            return {"ok": True, **_pnl_split(args.trade_pnls, args.trade_regimes)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Given a regime PnL split, recommend which regimes to trade and "
            "which to skip. Sorts regimes by metric (mean_pnl, win_rate, "
            "total_pnl) and reports the best plus a keep/skip recommendation."
        )
    )
    async def regime_recommend_filter(args: RecommendFilterArgs) -> dict:
        return {"ok": True, **_recommend_filter(args.regime_pnl, args.metric)}


__all__ = [
    "RecommendFilterArgs",
    "RegimePnLSplitArgs",
    "TrendRegimeArgs",
    "VolatilityRegimeArgs",
    "_classify_trend",
    "_classify_volatility",
    "_pnl_split",
    "_recommend_filter",
    "_rolling_slope",
    "_rolling_std",
    "_tertile_labels",
    "_trend_labels",
    "register",
]
