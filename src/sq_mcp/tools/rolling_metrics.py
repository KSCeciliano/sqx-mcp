"""Rolling-window metrics for live monitoring.

Compute Sharpe, drawdown, volatility, and correlation over a rolling
window so the agent can watch how a strategy's behavior changes over
time — not just summary stats over the full backtest.

Tools:

- ``rolling_sharpe`` — Sharpe per rolling window of N periods.
- ``rolling_drawdown`` — current and max drawdown over a rolling window
  of the equity curve.
- ``rolling_volatility`` — annualized stddev over a rolling window.
- ``rolling_correlation`` — Pearson correlation between two series over
  a rolling window.
- ``rolling_beta_alpha`` — rolling regression of strategy on benchmark.
- ``rolling_win_rate`` — fraction of positive trades in a rolling
  window.

All windows are *trailing*: the value at index t uses [t-window+1, t].
Pure Python.
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


class RollingArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    window: int = Field(..., ge=2, le=10_000)
    annualize_periods: int = Field(252, ge=1, le=525_600)


class RollingDrawdownArgs(BaseModel):
    equity_curve: list[float] = Field(..., min_length=10, max_length=500_000)
    window: int = Field(..., ge=2, le=10_000)


class RollingTwoSeriesArgs(BaseModel):
    series_a: list[float] = Field(..., min_length=10, max_length=500_000)
    series_b: list[float] = Field(..., min_length=10, max_length=500_000)
    window: int = Field(..., ge=2, le=10_000)


class RollingWinRateArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=10, max_length=500_000)
    window: int = Field(..., ge=2, le=10_000)


def _rolling_sharpe(
    series: list[float], window: int, annualize: int
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    if window > len(series):
        return {"values": [], "note": "window > series length"}
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, len(series)):
        win = series[t - window + 1 : t + 1]
        m = sum(win) / window
        var = sum((x - m) ** 2 for x in win) / (window - 1)
        if var <= 1e-12:
            out.append(None)
            continue
        s = math.sqrt(var)
        sharpe_period = m / s
        sharpe_annual = sharpe_period * math.sqrt(annualize)
        out.append(round(sharpe_annual, 4))
    valid = [v for v in out if v is not None]
    return {
        "values": out,
        "n": len(out),
        "window": window,
        "annualize_periods": annualize,
        "summary": {
            "mean": round(sum(valid) / len(valid), 4) if valid else None,
            "min": round(min(valid), 4) if valid else None,
            "max": round(max(valid), 4) if valid else None,
            "current": valid[-1] if valid else None,
        },
    }


def _rolling_drawdown(curve: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    out: list[float | None] = [None] * (window - 1)
    n = len(curve)
    for t in range(window - 1, n):
        win = curve[t - window + 1 : t + 1]
        peak = win[0]
        max_dd = 0.0
        for v in win:
            if v > peak:
                peak = v
            if peak > 0:
                dd = (peak - v) / peak
                if dd > max_dd:
                    max_dd = dd
        out.append(round(max_dd * 100.0, 6))
    valid = [v for v in out if v is not None]
    return {
        "values": out,
        "window": window,
        "summary": {
            "mean_max_dd_pct": round(sum(valid) / len(valid), 6) if valid else None,
            "worst_window_dd_pct": round(max(valid), 6) if valid else None,
            "current_max_dd_pct": valid[-1] if valid else None,
        },
    }


def _rolling_volatility(
    series: list[float], window: int, annualize: int
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, len(series)):
        win = series[t - window + 1 : t + 1]
        m = sum(win) / window
        var = sum((x - m) ** 2 for x in win) / (window - 1)
        out.append(round(math.sqrt(max(0.0, var) * annualize), 6))
    valid = [v for v in out if v is not None]
    return {
        "values": out,
        "window": window,
        "summary": {
            "mean": round(sum(valid) / len(valid), 6) if valid else None,
            "min": round(min(valid), 6) if valid else None,
            "max": round(max(valid), 6) if valid else None,
            "current": valid[-1] if valid else None,
        },
    }


def _rolling_correlation(
    a: list[float], b: list[float], window: int
) -> dict[str, Any]:
    validate_finite_floats(a, name="series_a")
    validate_finite_floats(b, name="series_b")
    n = min(len(a), len(b))
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        wa = a[t - window + 1 : t + 1]
        wb = b[t - window + 1 : t + 1]
        ma = sum(wa) / window
        mb = sum(wb) / window
        da = [x - ma for x in wa]
        db = [x - mb for x in wb]
        var_a = sum(x * x for x in da)
        var_b = sum(x * x for x in db)
        if var_a <= 0 or var_b <= 0:
            out.append(None)
            continue
        cov = sum(da[i] * db[i] for i in range(window))
        out.append(round(cov / math.sqrt(var_a * var_b), 6))
    valid = [v for v in out if v is not None]
    return {
        "values": out,
        "window": window,
        "summary": {
            "mean": round(sum(valid) / len(valid), 6) if valid else None,
            "min": round(min(valid), 6) if valid else None,
            "max": round(max(valid), 6) if valid else None,
            "current": valid[-1] if valid else None,
        },
    }


def _rolling_beta_alpha(
    strat: list[float], bench: list[float], window: int
) -> dict[str, Any]:
    validate_finite_floats(strat, name="strategy_returns")
    validate_finite_floats(bench, name="benchmark_returns")
    n = min(len(strat), len(bench))
    betas: list[float | None] = [None] * (window - 1)
    alphas: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        ws = strat[t - window + 1 : t + 1]
        wb = bench[t - window + 1 : t + 1]
        ms = sum(ws) / window
        mb = sum(wb) / window
        cov = sum((ws[i] - ms) * (wb[i] - mb) for i in range(window))
        var_b = sum((x - mb) ** 2 for x in wb)
        if var_b <= 0:
            betas.append(None)
            alphas.append(None)
            continue
        beta = cov / var_b
        alpha = ms - beta * mb
        betas.append(round(beta, 6))
        alphas.append(round(alpha, 8))
    return {
        "beta_values": betas,
        "alpha_values": alphas,
        "window": window,
        "current_beta": next((b for b in reversed(betas) if b is not None), None),
        "current_alpha": next((a for a in reversed(alphas) if a is not None), None),
    }


def _rolling_win_rate(pnls: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(pnls, name="trade_pnls")
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, len(pnls)):
        win = pnls[t - window + 1 : t + 1]
        wins = sum(1 for p in win if p > 0)
        out.append(round(wins / window, 4))
    valid = [v for v in out if v is not None]
    return {
        "values": out,
        "window": window,
        "summary": {
            "mean": round(sum(valid) / len(valid), 4) if valid else None,
            "min": round(min(valid), 4) if valid else None,
            "max": round(max(valid), 4) if valid else None,
            "current": valid[-1] if valid else None,
        },
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Annualized Sharpe ratio computed over a rolling trailing window "
            "of N periods. Returns a parallel series + a summary "
            "(mean / min / max / current). Useful for detecting Sharpe "
            "decay live."
        )
    )
    async def rolling_sharpe(args: RollingArgs) -> dict:
        try:
            return {
                "ok": True,
                **_rolling_sharpe(args.series, args.window, args.annualize_periods),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Max drawdown computed over each rolling window of the equity "
            "curve. Returns the windowed series + worst-window DD across "
            "the curve. Detects 'we got dragged into a deeper drawdown' "
            "early."
        )
    )
    async def rolling_drawdown(args: RollingDrawdownArgs) -> dict:
        try:
            return {"ok": True, **_rolling_drawdown(args.equity_curve, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Annualized volatility over a rolling window. Useful for live "
            "volatility regime detection — compare current vs mean to "
            "detect surprises."
        )
    )
    async def rolling_volatility(args: RollingArgs) -> dict:
        try:
            return {
                "ok": True,
                **_rolling_volatility(args.series, args.window, args.annualize_periods),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Pearson correlation between two series over a rolling window. "
            "Use to detect when a previously diversifying strategy starts "
            "tracking the benchmark (correlation drift)."
        )
    )
    async def rolling_correlation(args: RollingTwoSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_rolling_correlation(args.series_a, args.series_b, args.window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Rolling beta and alpha from a regression of strategy returns on "
            "benchmark returns. Returns parallel series of beta_values and "
            "alpha_values + current_beta / current_alpha."
        )
    )
    async def rolling_beta_alpha(args: RollingTwoSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_rolling_beta_alpha(args.series_a, args.series_b, args.window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Win rate (fraction of positive trades) computed over a rolling "
            "window. Detect early when a strategy's edge starts eroding "
            "without waiting for a full backtest update."
        )
    )
    async def rolling_win_rate(args: RollingWinRateArgs) -> dict:
        try:
            return {"ok": True, **_rolling_win_rate(args.trade_pnls, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "RollingArgs",
    "RollingDrawdownArgs",
    "RollingTwoSeriesArgs",
    "RollingWinRateArgs",
    "_rolling_beta_alpha",
    "_rolling_correlation",
    "_rolling_drawdown",
    "_rolling_sharpe",
    "_rolling_volatility",
    "_rolling_win_rate",
    "register",
]
