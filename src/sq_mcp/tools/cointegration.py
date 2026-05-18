"""Cointegration testing for pairs trading.

Two non-stationary price series can still have a stationary linear
combination — they are "cointegrated." This is the foundation of
pairs trading: long one, short the other, with the spread
mean-reverting to a long-run equilibrium.

The Engle-Granger test is a two-step procedure:
  1. Regress series Y on series X to get residuals (the spread).
  2. Test the residuals for stationarity (heuristic ADF here).

Tools:

- ``cointegration_engle_granger`` — full two-step test; returns the
  hedge ratio + residual stationarity stat + verdict.
- ``cointegration_spread_series`` — compute the spread (Y - β·X) given
  the hedge ratio, return the spread series + its half-life.
- ``cointegration_zscore`` — z-score of the latest spread value against
  its rolling mean/std; the canonical pairs-trading entry signal.
- ``cointegration_pair_scan`` — given a set of series, score every
  pairwise combination by Engle-Granger and return the most cointegrated.

Pure Python.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)
from sq_mcp.tools.frac_diff import _heuristic_adf
from sq_mcp.tools.mean_reversion import _ou_half_life


class EngleGrangerArgs(BaseModel):
    y: list[float] = Field(..., min_length=30, max_length=500_000)
    x: list[float] = Field(..., min_length=30, max_length=500_000)


class SpreadArgs(BaseModel):
    y: list[float] = Field(..., min_length=10, max_length=500_000)
    x: list[float] = Field(..., min_length=10, max_length=500_000)
    hedge_ratio: float = Field(..., gt=-1e6, lt=1e6)


class ZscoreArgs(SpreadArgs):
    window: int = Field(20, ge=2, le=10_000)


class PairScanArgs(BaseModel):
    series_by_name: dict[str, list[float]] = Field(..., min_length=2, max_length=100)
    top_n: int = Field(10, ge=1, le=100)


def _ols_slope_intercept(y: list[float], x: list[float]) -> tuple[float, float]:
    n = min(len(y), len(x))
    if n < 2:
        return 0.0, 0.0
    mean_x = sum(x[:n]) / n
    mean_y = sum(y[:n]) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den = sum((x[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _engle_granger(y: list[float], x: list[float]) -> dict[str, Any]:
    validate_finite_floats(y, name="y")
    validate_finite_floats(x, name="x")
    n = min(len(y), len(x))
    slope, intercept = _ols_slope_intercept(y[:n], x[:n])
    residuals = [y[i] - slope * x[i] - intercept for i in range(n)]
    adf = _heuristic_adf(residuals)
    return {
        "hedge_ratio": round(slope, 6),
        "intercept": round(intercept, 6),
        "adf_statistic_on_residuals": round(adf, 6),
        "n_samples": n,
        "verdict": (
            "cointegrated_1pct" if adf <= -3.43
            else "cointegrated_5pct" if adf <= -2.86
            else "cointegrated_10pct" if adf <= -1.95
            else "not_cointegrated"
        ),
        "interpretation": (
            "The two series share a long-run equilibrium — suitable for "
            "pairs trading."
            if adf <= -2.86
            else "No statistically significant cointegration — pairs trading "
            "on this combination is unreliable."
        ),
    }


def _spread(y: list[float], x: list[float], hedge: float) -> dict[str, Any]:
    validate_finite_floats(y, name="y")
    validate_finite_floats(x, name="x")
    n = min(len(y), len(x))
    series = [y[i] - hedge * x[i] for i in range(n)]
    half = _ou_half_life(series)
    return {
        "spread": series,
        "n": n,
        "hedge_ratio": hedge,
        "ou_half_life": half.get("half_life"),
        "mean": round(sum(series) / n, 6),
        "std": round(
            (sum((s - sum(series) / n) ** 2 for s in series) / max(1, n - 1)) ** 0.5,
            6,
        ),
    }


def _zscore(
    y: list[float], x: list[float], hedge: float, window: int
) -> dict[str, Any]:
    validate_finite_floats(y, name="y")
    validate_finite_floats(x, name="x")
    n = min(len(y), len(x))
    series = [y[i] - hedge * x[i] for i in range(n)]
    if n < window:
        return {"zscore": None, "note": "window > spread length"}
    win = series[-window:]
    m = sum(win) / window
    var = sum((s - m) ** 2 for s in win) / max(1, window - 1)
    std = var ** 0.5
    if std == 0:
        return {"zscore": None, "note": "zero spread variance in window"}
    z = (series[-1] - m) / std
    return {
        "zscore": round(z, 4),
        "current_spread": round(series[-1], 6),
        "rolling_mean": round(m, 6),
        "rolling_std": round(std, 6),
        "window": window,
        "signal": (
            "long_y_short_x" if z < -2.0
            else "short_y_long_x" if z > 2.0
            else "weak_signal" if abs(z) > 1.0
            else "no_signal"
        ),
    }


def _pair_scan(
    series_by_name: dict[str, list[float]], top_n: int
) -> dict[str, Any]:
    for k, v in series_by_name.items():
        validate_finite_floats(v, name=f"series[{k!r}]")
    names = sorted(series_by_name.keys())
    results: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            y = series_by_name[names[i]]
            x = series_by_name[names[j]]
            r = _engle_granger(y, x)
            results.append({
                "pair": (names[i], names[j]),
                "hedge_ratio": r["hedge_ratio"],
                "adf_statistic": r["adf_statistic_on_residuals"],
                "verdict": r["verdict"],
            })
    results.sort(key=lambda r: r["adf_statistic"])  # most negative first
    return {
        "n_pairs_tested": len(results),
        "top_n": top_n,
        "best_pairs": results[:top_n],
        "n_cointegrated_at_5pct": sum(
            1 for r in results
            if r["verdict"] in {"cointegrated_5pct", "cointegrated_1pct"}
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Engle-Granger two-step cointegration test: regress y on x, "
            "compute residuals (the spread), test residuals for stationarity. "
            "Returns hedge_ratio + ADF-on-residuals + verdict "
            "(cointegrated_1pct / 5pct / 10pct / not_cointegrated). Use as "
            "the foundation of pairs trading."
        )
    )
    async def cointegration_engle_granger(args: EngleGrangerArgs) -> dict:
        try:
            return {"ok": True, **_engle_granger(args.y, args.x)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Compute the spread series (y - hedge_ratio · x) given the hedge "
            "ratio. Returns the spread + its mean + std + Ornstein-Uhlenbeck "
            "half-life (how fast deviations decay)."
        )
    )
    async def cointegration_spread_series(args: SpreadArgs) -> dict:
        try:
            return {"ok": True, **_spread(args.y, args.x, args.hedge_ratio)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Z-score of the latest spread value against its rolling window "
            "mean/std. Returns signal: long_y_short_x (z<-2), "
            "short_y_long_x (z>2), weak_signal (|z|>1), or no_signal. "
            "Canonical pairs-trading entry rule."
        )
    )
    async def cointegration_zscore(args: ZscoreArgs) -> dict:
        try:
            return {
                "ok": True,
                **_zscore(args.y, args.x, args.hedge_ratio, args.window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Scan every pairwise combination of a set of price series for "
            "cointegration. Returns the top_n best pairs sorted by ADF "
            "statistic on the residuals. O(N²) — cap series count to "
            "~30-50 for fast response."
        )
    )
    async def cointegration_pair_scan(args: PairScanArgs) -> dict:
        try:
            return {"ok": True, **_pair_scan(args.series_by_name, args.top_n)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "EngleGrangerArgs",
    "PairScanArgs",
    "SpreadArgs",
    "ZscoreArgs",
    "_engle_granger",
    "_ols_slope_intercept",
    "_pair_scan",
    "_spread",
    "_zscore",
    "register",
]
